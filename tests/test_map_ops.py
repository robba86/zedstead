"""Tests for app.maps.map_ops.download_and_extract_mission (plans/60 G)."""

from __future__ import annotations

import io
import json
import shutil
import zipfile

import pytest

from app.maps.map_ops import download_and_extract_mission
from app.shared.seed_data import WORKSHOP_MAPS


def _zip_bytes(entries: dict[str, str | None], *, comment: bytes = b"") -> bytes:
    """A GitHub-style archive: every entry under one top folder.

    A value of None makes a directory entry. GitHub writes the commit as
    the archive's *comment*.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.comment = comment
        for name, text in entries.items():
            if text is None:
                zf.writestr(zipfile.ZipInfo(f"repo-main/{name}/"), "")
            else:
                zf.writestr(f"repo-main/{name}", text)
    return buf.getvalue()


class _Resp:
    """A streamed `requests` response with the given chunks; records how many
    were read."""

    def __init__(self, chunks):
        self.chunks = chunks
        self.read = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size=8192):
        for chunk in self.chunks:
            self.read += 1
            yield chunk


@pytest.fixture()
def serve_zip(monkeypatch):
    """Make requests.get answer with the given archive bytes (or chunks)."""

    def _serve(data) -> _Resp:
        resp = _Resp([data] if isinstance(data, bytes) else data)
        monkeypatch.setattr("requests.get", lambda *a, **k: resp)
        return resp

    return _serve


def test_extracts_the_mission_folder_under_the_archive_prefix(tmp_path, serve_zip):
    serve_zip(_zip_bytes({
        "dayzOffline.banov/init.c": "void main() {}",
        "dayzOffline.banov/db/types.xml": "<types/>",
        "empty.banovfrost/init.c": "frost",
        "README.md": "readme",
    }))
    dest = tmp_path / "mpmissions" / "empty.banov"

    count = download_and_extract_mission("https://x/repo.zip", "dayzOffline.banov", dest)

    assert count == 2
    assert (dest / "init.c").read_text() == "void main() {}"
    assert (dest / "db" / "types.xml").read_text() == "<types/>"
    assert not (dest / "README.md").exists()


def test_a_missing_mission_path_raises_before_creating_dest(tmp_path, serve_zip):
    """Upstream deleted empty.banov; this used to extract 0 files and succeed."""
    serve_zip(_zip_bytes({
        "dayzOffline.banov/init.c": "void main() {}",
        "empty.banovfrost/init.c": "frost",
    }))
    dest = tmp_path / "mpmissions" / "empty.banov"

    with pytest.raises(ValueError, match="Mission folder not found") as exc:
        download_and_extract_mission("https://x/repo.zip", "empty.banov", dest)

    assert "empty.banov" in str(exc.value)
    assert "https://x/repo.zip" in str(exc.value)
    assert not dest.exists()


def test_a_folder_holding_no_files_counts_as_missing(tmp_path, serve_zip):
    serve_zip(_zip_bytes({
        "empty.banov": None,
        "empty.banov/db": None,
        "dayzOffline.banov/init.c": "void main() {}",
    }))
    dest = tmp_path / "mission"

    with pytest.raises(ValueError, match="Mission folder not found"):
        download_and_extract_mission("https://x/repo.zip", "empty.banov", dest)
    assert not dest.exists()


# ── size limits (plans/68 NEW-7) ─────────────────────────────────────


def _limits(monkeypatch, **kw):
    """Shrink MISSION_ZIP_LIMITS for a test; the rest stay as shipped."""
    import dataclasses

    from app.maps import map_ops

    monkeypatch.setattr(map_ops, "MISSION_ZIP_LIMITS", dataclasses.replace(map_ops.MISSION_ZIP_LIMITS, **kw))


def test_a_download_past_the_limit_stops_before_the_file_reaches_it(tmp_path, serve_zip, monkeypatch):
    from app.shared.zip_utils import ArchiveTooLarge

    _limits(monkeypatch, download_bytes=1000)
    endless = (b"x" * 300 for _ in range(1_000_000))
    resp = serve_zip(endless)
    dest = tmp_path / "mpmissions" / "empty.banov"

    with pytest.raises(ArchiveTooLarge, match="larger than"):
        download_and_extract_mission("https://x/repo.zip", "empty.banov", dest)
    assert resp.read == 4  # the fourth chunk would pass 1000 bytes
    assert not dest.exists()


def test_the_temp_file_never_holds_more_than_the_limit(tmp_path):
    from app.shared.zip_utils import ArchiveTooLarge, save_capped

    out = tmp_path / "repo.zip"
    with pytest.raises(ArchiveTooLarge):
        save_capped((b"x" * 300 for _ in range(10)), out, 1000, source="The archive")
    assert out.stat().st_size == 900


@pytest.mark.parametrize("limit", [
    {"total_bytes": 1500},
    {"member_bytes": 1500},
    {"members": 2},
])
def test_a_mission_that_would_unpack_past_a_limit_writes_nothing(tmp_path, serve_zip, monkeypatch, limit):
    from app.shared.zip_utils import ArchiveTooLarge

    _limits(monkeypatch, **limit)
    serve_zip(_zip_bytes({
        "dayzOffline.banov/init.c": "x" * 1000,
        "dayzOffline.banov/db/types.xml": "y" * 2000,
        "dayzOffline.banov/db/events.xml": "z" * 10,
    }))
    dest = tmp_path / "mpmissions" / "empty.banov"

    with pytest.raises(ArchiveTooLarge, match="nothing was unpacked|more than"):
        download_and_extract_mission("https://x/repo.zip", "dayzOffline.banov", dest)
    assert not dest.exists()


def test_only_the_mission_folder_counts_toward_the_limits(tmp_path, serve_zip, monkeypatch):
    _limits(monkeypatch, total_bytes=1500, members=2)
    serve_zip(_zip_bytes({
        "dayzOffline.banov/init.c": "x" * 1000,
        "empty.banovfrost/big.xml": "y" * 5000,
        "README.md": "readme",
    }))
    dest = tmp_path / "mission"
    assert download_and_extract_mission("https://x/repo.zip", "dayzOffline.banov", dest) == 1


def test_a_member_that_understates_its_size_writes_no_more_than_it_claims(tmp_path, serve_zip, monkeypatch):
    """Why `check_unpack_size` may trust the archive's sizes: zipfile stops
    reading a member at its stated size, then fails the CRC check."""
    import struct

    data = bytearray(_zip_bytes({"dayzOffline.banov/init.c": "x" * 100_000}))
    struct.pack_into("<I", data, 22, 10)  # the local header's uncompressed size
    central = data.rfind(b"PK\x01\x02")
    struct.pack_into("<I", data, central + 24, 10)  # and the central directory's
    serve_zip(bytes(data))
    dest = tmp_path / "mission"
    written = []
    real_copy = shutil.copyfileobj

    def _measured(src, dst, *args):
        try:
            real_copy(src, dst, *args)
        finally:
            written.append(dst.tell())

    monkeypatch.setattr(shutil, "copyfileobj", _measured)

    with pytest.raises(zipfile.BadZipFile):
        download_and_extract_mission("https://x/repo.zip", "dayzOffline.banov", dest)
    assert written and written[0] <= 10
    assert not dest.exists()


# ── replacing a mission only on success (RELEASE item 22) ────────────


def _old_mission(root):
    """An installed mission with an admin's edit, a stray file and a world."""
    mission = root / "mpmissions" / "regular.namalsk"
    (mission / "storage_1").mkdir(parents=True)
    (mission / "storage_1" / "players.db").write_text("PLAYER_DATA")
    (mission / "init.c").write_text("old")
    (mission / "stale.xml").write_text("gone upstream")
    return mission


def _tree(root):
    return {str(p.relative_to(root)): p.read_text() for p in sorted(root.rglob("*")) if p.is_file()}


_NAMALSK = {
    "Mission Files/regular.namalsk/init.c": "new",
    "Mission Files/regular.namalsk/storage_1/players.db": "UPSTREAM",
}


def test_a_failed_download_leaves_the_mission_as_it_was(tmp_path, monkeypatch):
    import requests

    mission = _old_mission(tmp_path)
    before = _tree(mission)

    def _down(*a, **k):
        raise requests.ConnectionError("network down")

    monkeypatch.setattr("requests.get", _down)
    with pytest.raises(requests.ConnectionError):
        download_and_extract_mission(
            "https://x/repo.zip", "Mission Files/regular.namalsk", mission, keep_storage=True,
        )
    assert _tree(mission) == before


def test_a_failed_unpack_leaves_the_mission_and_no_half_folder(tmp_path, serve_zip, monkeypatch):
    mission = _old_mission(tmp_path)
    before = _tree(mission)
    serve_zip(_zip_bytes({**_NAMALSK, "Mission Files/regular.namalsk/db/types.xml": "<types/>"}))
    real_copy = shutil.copyfileobj
    calls = []

    def _disk_full(src, dst, *args):
        calls.append(1)
        if len(calls) == 2:
            raise OSError(28, "No space left on device")
        real_copy(src, dst, *args)

    monkeypatch.setattr(shutil, "copyfileobj", _disk_full)
    with pytest.raises(OSError, match="No space"):
        download_and_extract_mission(
            "https://x/repo.zip", "Mission Files/regular.namalsk", mission, keep_storage=True,
        )
    assert _tree(mission) == before
    assert [p.name for p in mission.parent.iterdir()] == ["regular.namalsk"]


def test_a_reset_replaces_the_mission_and_keeps_every_world(tmp_path, serve_zip):
    mission = _old_mission(tmp_path)
    serve_zip(_zip_bytes(_NAMALSK))

    count = download_and_extract_mission(
        "https://x/repo.zip", "Mission Files/regular.namalsk", mission, keep_storage=True,
    )

    assert count == 2
    # The admin's world wins over the archive's copy, and the stray file goes.
    assert _tree(mission) == {"init.c": "new", "storage_1/players.db": "PLAYER_DATA"}
    assert [p.name for p in mission.parent.iterdir()] == ["regular.namalsk"]


def test_a_reset_without_keep_storage_takes_the_archives_world(tmp_path, serve_zip):
    mission = _old_mission(tmp_path)
    serve_zip(_zip_bytes(_NAMALSK))

    download_and_extract_mission("https://x/repo.zip", "Mission Files/regular.namalsk", mission)

    assert _tree(mission) == {"init.c": "new", "storage_1/players.db": "UPSTREAM"}


_SHA = "0123456789abcdef0123456789abcdef01234567"


@pytest.mark.parametrize("comment, commit", [(_SHA.encode(), _SHA), (b"", None), (b"not a sha", None)])
def test_a_recorded_mission_names_its_upstream_commit(tmp_path, serve_zip, comment, commit):
    from app.maps.map_ops import MISSION_SOURCE_FILE, read_mission_source

    serve_zip(_zip_bytes({"V6.0/empty.deerisle/init.c": "new"}, comment=comment))
    dest = tmp_path / "mpmissions" / "empty.deerisle"

    download_and_extract_mission(
        "https://x/repo.zip", "V5.9/empty.deerisle", dest, record_source=True,
    )

    assert read_mission_source(dest) == {
        "zip_url": "https://x/repo.zip", "mission_path": "V6.0/empty.deerisle", "commit": commit,
    }
    assert json.loads((dest / MISSION_SOURCE_FILE).read_text())["commit"] == commit


def test_an_unrecorded_download_writes_no_record(tmp_path, serve_zip):
    from app.maps.map_ops import MISSION_SOURCE_FILE

    serve_zip(_zip_bytes({"dayzOffline.banov/init.c": "x"}, comment=_SHA.encode()))
    dest = tmp_path / "mission"
    download_and_extract_mission("https://x/repo.zip", "dayzOffline.banov", dest)
    assert not (dest / MISSION_SOURCE_FILE).exists()


# ── which folder an archive gives (RELEASE item 22) ──────────────────


@pytest.mark.parametrize("files, mission_path, expected", [
    # Deer Isle kept V5.3 when it added V5.9: a newer version folder wins
    # while the catalog's is still there.
    (["V5.3/empty.deerisle/init.c", "V5.9/empty.deerisle/init.c", "V6.0/empty.deerisle/init.c"],
     "V5.9/empty.deerisle", "V6.0/empty.deerisle"),
    (["V5.9/empty.deerisle/init.c", "V5.10/empty.deerisle/init.c"], "V5.9/empty.deerisle", "V5.10/empty.deerisle"),
    (["V5.3/empty.deerisle/init.c", "V5.9/empty.deerisle/init.c"], "V5.9/empty.deerisle", "V5.9/empty.deerisle"),
    # A version folder that holds no file is no candidate.
    (["V5.9/empty.deerisle/init.c", "V6.0/README.md"], "V5.9/empty.deerisle", "V5.9/empty.deerisle"),
    # Every version folder gone: found by its name.
    (["Missions/empty.deerisle/init.c"], "V5.9/empty.deerisle", "Missions/empty.deerisle"),
    # A catalog folder that is not in a version folder wins while it is there.
    (["Mission Files/regular.namalsk/init.c", "Old/regular.namalsk/init.c"],
     "Mission Files/regular.namalsk", "Mission Files/regular.namalsk"),
    (["Missions/regular.namalsk/init.c", "README.md"], "Mission Files/regular.namalsk", "Missions/regular.namalsk"),
    (["empty.banovfrost/init.c"], "empty.banovfrost", "empty.banovfrost"),
    # Moved into version folders: the highest.
    (["1.29/empty.banovfrost/init.c", "1.30/empty.banovfrost/init.c"], "empty.banovfrost", "1.30/empty.banovfrost"),
])
def test_the_mission_folder_follows_upstream(files, mission_path, expected):
    from app.maps.map_ops import resolve_mission_path

    assert resolve_mission_path(files, mission_path, source="https://x/repo.zip") == expected


def test_two_folders_of_the_same_name_that_are_not_versions_refuse():
    from app.maps.map_ops import resolve_mission_path

    with pytest.raises(ValueError, match="Mission folder not found") as exc:
        resolve_mission_path(
            ["A/regular.namalsk/init.c", "B/regular.namalsk/init.c"],
            "Mission Files/regular.namalsk", source="https://x/repo.zip",
        )
    assert "A/regular.namalsk, B/regular.namalsk" in str(exc.value)


def test_a_moved_folder_is_taken_and_logged(tmp_path, serve_zip):
    serve_zip(_zip_bytes({"V6.0/empty.deerisle/init.c": "new", "V5.3/empty.deerisle/init.c": "old"}))
    dest = tmp_path / "mission"
    lines = []

    download_and_extract_mission("https://x/repo.zip", "V5.9/empty.deerisle", dest, log=lines.append)

    assert (dest / "init.c").read_text() == "new"
    assert any("Taking V6.0/empty.deerisle/" in line for line in lines)


# ── asking GitHub whether upstream changed (RELEASE item 22) ─────────


_DEER_ZIP = "https://github.com/johnmclane666/Deerisle-Stable/archive/refs/heads/master.zip"


class _Answer:
    def __init__(self, status, data=None):
        self.status_code = status
        self._data = data

    def raise_for_status(self):
        import requests

        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")

    def json(self):
        return self._data


@pytest.fixture()
def github(monkeypatch):
    """Answer GitHub's compare API with *status* and *data*; records each URL."""
    asked = []

    def _answer(status, data=None):
        def _get(url, **kwargs):
            asked.append((url, kwargs))
            return _Answer(status, data)

        monkeypatch.setattr("requests.get", _get)
        return asked

    return _answer


def _recorded(tmp_path, commit=_SHA, folder="V5.9/empty.deerisle"):
    from app.maps.map_ops import MISSION_SOURCE_FILE

    mission = tmp_path / "empty.deerisle"
    mission.mkdir()
    (mission / MISSION_SOURCE_FILE).write_text(json.dumps(
        {"zip_url": _DEER_ZIP, "mission_path": folder, "commit": commit},
    ))
    return mission


def test_the_check_asks_github_from_the_recorded_commit_to_the_branch(tmp_path, github):
    from app.maps.map_ops import mission_update_status

    asked = github(200, {"ahead_by": 0, "files": []})
    result = mission_update_status(_recorded(tmp_path), _DEER_ZIP)

    assert result.newer is False
    ((url, kwargs),) = asked
    assert url == f"https://api.github.com/repos/johnmclane666/Deerisle-Stable/compare/{_SHA}...master"
    assert kwargs["timeout"] == 20


@pytest.mark.parametrize("files, newer, changed", [
    # A file in the mission, in a new version folder of the same name too.
    (["V5.9/empty.deerisle/db/types.xml", "V6.0/empty.deerisle/init.c", "README.md"], True, 2),
    (["README.md", "V5.9/Keys/deerisle.bikey"], False, 0),
    ([f"other/{i}.txt" for i in range(300)], True, 0),
])
def test_the_check_counts_only_mission_files(tmp_path, github, files, newer, changed):
    from app.maps.map_ops import mission_update_status

    github(200, {"ahead_by": 3, "files": [{"filename": f} for f in files]})
    result = mission_update_status(_recorded(tmp_path), _DEER_ZIP)

    assert (result.newer, result.changed) == (newer, changed)


def test_a_commit_github_no_longer_knows_cannot_be_told(tmp_path, github):
    from app.maps.map_ops import mission_update_status

    github(404)
    result = mission_update_status(_recorded(tmp_path), _DEER_ZIP)
    assert result.newer is None
    assert "rewrote its history" in result.detail


@pytest.mark.parametrize("status", [403, 429, 500])
def test_a_refused_check_raises(tmp_path, github, status):
    from app.maps.map_ops import MissionCheckError, mission_update_status

    github(status)
    with pytest.raises(MissionCheckError):
        mission_update_status(_recorded(tmp_path), _DEER_ZIP)


@pytest.mark.parametrize("commit", [None, "abc", "../../evil"])
def test_a_mission_without_a_good_record_asks_nothing(tmp_path, github, commit):
    from app.maps.map_ops import mission_update_status

    asked = github(200, {"ahead_by": 0, "files": []})
    result = mission_update_status(_recorded(tmp_path, commit=commit), _DEER_ZIP)
    assert result.newer is None
    assert asked == []


def test_a_mission_not_from_a_github_branch_asks_nothing(tmp_path, github):
    from app.maps.map_ops import mission_update_status

    asked = github(200, {"ahead_by": 0, "files": []})
    result = mission_update_status(_recorded(tmp_path), "https://example.com/m.zip")
    assert result.newer is None
    assert asked == []


def test_every_community_mission_archive_can_be_checked():
    from app.maps.map_ops import _GITHUB_ARCHIVE

    for name, data in WORKSHOP_MAPS.items():
        mission = data["mission"]
        if mission.get("source") == "zip":
            assert _GITHUB_ARCHIVE.fullmatch(mission["zip_url"]), name


def test_the_recommended_cfg_download_has_the_same_limit(tmp_path, serve_zip, monkeypatch):
    from app.maps.map_ops import extract_server_cfg_from_zip
    from app.shared.zip_utils import ArchiveTooLarge

    _limits(monkeypatch, download_bytes=1000)
    serve_zip(b"x" * 300 for _ in range(100))
    with pytest.raises(ArchiveTooLarge):
        extract_server_cfg_from_zip("https://x/repo.zip", "serverDZ.cfg", tmp_path / "serverDZ.cfg")
    assert not (tmp_path / "serverDZ.cfg").exists()


def test_banov_extracts_the_folder_upstream_still_has():
    """KubeloLive/Banov-Central-Economy deleted empty.banov (f040b801)."""
    mission = WORKSHOP_MAPS["Banov"]["mission"]
    assert mission["mission_path"] == "dayzOffline.banov"
    # Installed servers are recognised by the template; it must not change.
    assert mission["template"] == "empty.banov"


def test_a_mission_copied_from_a_workshop_map_leaves_symlinks_out(sample_config, tmp_path):
    from app.maps.map_ops import copy_mission_from_mod

    # Pinned, or workshop_dir resolves to the host's real Steam cache.
    sample_config.workshop_content_dir = str(tmp_path / "workshop")
    mission = sample_config.workshop_dir / "123" / "Missions" / "empty.deerisle"
    (mission / "db").mkdir(parents=True)
    (mission / "db" / "types.xml").write_text("<types/>")
    secret = tmp_path / "secret.txt"
    secret.write_text("not the map's")
    (mission / "db" / "events.xml").symlink_to(secret)
    (mission / "up").symlink_to(mission.parent, target_is_directory=True)

    dest = tmp_path / "server" / "mpmissions" / "empty.deerisle"
    count = copy_mission_from_mod(
        sample_config, "@DeerIsle", "Missions/empty.deerisle", dest, {"@DeerIsle": "123"}
    )
    assert count == 1
    assert sorted(p.relative_to(dest).as_posix() for p in dest.rglob("*")) == ["db", "db/types.xml"]


# ── a map switch and serverDZ.cfg (apply_recommended_cfg, restore_map_cfg) ──

THEIR_CFG = (
    'hostname = "Namalsk";\n'
    'password = "";\n'
    'passwordAdmin = "theirs";\n'
    'class Missions\n{\n\tclass DayZ\n\t{\n\t\ttemplate = "regular.namalsk";\n\t};\n};\n'
)


def _passwords(cfg):
    from app.parsers.servercfg import read_cfg_values

    return read_cfg_values(cfg, ["password", "passwordAdmin"])


def test_a_recommended_cfg_keeps_the_admin_password(sample_config, serve_zip):
    from app.maps.map_ops import apply_recommended_cfg

    cfg = sample_config.server_cfg_path
    cfg.write_text('hostname = "Mine";\npassword = "join";\npasswordAdmin = "mine";\n')
    serve_zip(_zip_bytes({"Server Config/Regular/serverDZ.cfg": THEIR_CFG}))

    assert apply_recommended_cfg(
        sample_config, "https://x/repo.zip", "Server Config/Regular/serverDZ.cfg", "regular.namalsk"
    )
    assert _passwords(cfg) == {"password": "join", "passwordAdmin": "mine"}


def test_a_recommended_cfg_does_not_bring_its_own_admin_password(sample_config, serve_zip):
    from app.maps.map_ops import apply_recommended_cfg

    cfg = sample_config.server_cfg_path  # conftest's cfg sets neither password
    serve_zip(_zip_bytes({"Server Config/Regular/serverDZ.cfg": THEIR_CFG}))

    assert apply_recommended_cfg(
        sample_config, "https://x/repo.zip", "Server Config/Regular/serverDZ.cfg", "regular.namalsk"
    )
    assert _passwords(cfg) == {"password": None, "passwordAdmin": None}


ADMIN_CFG = (
    'hostname = "Mine";\n'
    'passwordAdmin = "mine";\n'
    "verifySignatures = 2;\n"
    "steamQueryPort = 27016;\n"
    "lightingConfig = 0;\n"
    'class Missions\n{\n\tclass DayZ\n\t{\n\t\ttemplate = "dayzOffline.chernarusplus";\n\t};\n};\n'
)

THEIR_FULL_CFG = (
    'hostname = "Namalsk";\n'
    'passwordAdmin = "theirs";\n'
    "verifySignatures = 0;\n"
    "steamQueryPort = 2305;\n"
    "instanceId = 7;\n"
    "BattlEye = 0;\n"
    "lightingConfig = 1;\n"
    "enableCfgGameplayFile = 1;\n"
    'class Missions\n{\n\tclass DayZ\n\t{\n\t\ttemplate = "regular.namalsk";\n\t};\n};\n'
)


def test_a_recommended_cfg_sets_only_the_map_keys(sample_config, serve_zip):
    from app.maps.map_ops import apply_recommended_cfg
    from app.parsers.servercfg import read_cfg_values

    cfg = sample_config.server_cfg_path
    cfg.write_text(ADMIN_CFG)
    serve_zip(_zip_bytes({"Server Config/Regular/serverDZ.cfg": THEIR_FULL_CFG}))
    lines = []

    assert apply_recommended_cfg(
        sample_config, "https://x/repo.zip", "Server Config/Regular/serverDZ.cfg",
        "regular.namalsk", log=lines.append,
    )

    keys = ["hostname", "passwordAdmin", "verifySignatures", "steamQueryPort", "instanceId",
            "BattlEye", "lightingConfig", "enableCfgGameplayFile"]
    assert read_cfg_values(cfg, keys) == {
        "hostname": "Mine", "passwordAdmin": "mine", "verifySignatures": "2",
        "steamQueryPort": "27016", "instanceId": None, "BattlEye": None,
        "lightingConfig": "1", "enableCfgGameplayFile": "1",
    }
    assert 'template = "regular.namalsk"' in cfg.read_text()
    assert "  lightingConfig: 0 -> 1" in lines
    assert "  enableCfgGameplayFile: (not set) -> 1" in lines


# Invented, in the shape of a map's recommended cfgs, as Namalsk ships a Regular
# and a Hardcore one (plans/86 D5): a lighting config of the map's own, the
# personal light and the view switches each way, a fixed start time, and the
# time keys both share.
MAP_CFG_SHARED = (
    "serverTimeAcceleration = 4;\n"
    "serverNightTimeAcceleration = 6;\n"
    "serverTimePersistent = 1;\n"
)
MAP_CFG_REGULAR = (
    "lightingConfig = 311;\n"
    "disablePersonalLight = 1;\n"
    "disable3rdPerson = 0;\n"
    "disableCrosshair = 0;\n"
    'serverTime = "2010/6/15/7/30";\n'
) + MAP_CFG_SHARED
MAP_CFG_HARDCORE = (
    "lightingConfig = 312;\n"
    "disablePersonalLight = 0;\n"
    "disable3rdPerson = 1;\n"
    "disableCrosshair = 1;\n"
    'serverTime = "2010/7/15/7/30";\n'
) + MAP_CFG_SHARED


@pytest.mark.parametrize(
    "path, theirs, lighting, personal_light, third_person",
    [
        ("Server Config/Regular/serverDZ.cfg", MAP_CFG_REGULAR, "311", "1", "0"),
        ("Server Config/Hardcore/serverDZ.cfg", MAP_CFG_HARDCORE, "312", "0", "1"),
    ],
    ids=["regular", "hardcore"],
)
def test_namalsk_brings_its_lighting_and_leaves_the_admins_gameplay(
    sample_config, serve_zip, path, theirs, lighting, personal_light, third_person
):
    from app.maps.map_ops import apply_recommended_cfg
    from app.parsers.servercfg import read_cfg_values

    # The admin's own choices differ from the map's on every key.
    mine = "0" if third_person == "1" else "1"
    cfg = sample_config.server_cfg_path
    cfg.write_text(
        'hostname = "Mine";\n'
        "lightingConfig = 0;\n"
        f"disable3rdPerson = {mine};\n"
        f"disableCrosshair = {mine};\n"
        'serverTime = "SystemTime";\n'
        "serverTimeAcceleration = 1;\n"
        "serverNightTimeAcceleration = 1;\n"
        "serverTimePersistent = 0;\n"
        'class Missions\n{\n\tclass DayZ\n\t{\n\t\ttemplate = "dayzOffline.chernarusplus";\n\t};\n};\n'
    )
    serve_zip(_zip_bytes({path: theirs}))
    lines = []

    assert apply_recommended_cfg(sample_config, "https://x/repo.zip", path, "regular.namalsk", log=lines.append)

    keys = ["lightingConfig", "disablePersonalLight", "enableCfgGameplayFile", "disable3rdPerson",
            "disableCrosshair", "serverTime", "serverTimeAcceleration", "serverNightTimeAcceleration",
            "serverTimePersistent"]
    assert read_cfg_values(cfg, keys) == {
        "lightingConfig": lighting,
        "disablePersonalLight": personal_light,
        "enableCfgGameplayFile": None,
        "disable3rdPerson": mine,
        "disableCrosshair": mine,
        "serverTime": "SystemTime",
        "serverTimeAcceleration": "1",
        "serverNightTimeAcceleration": "1",
        "serverTimePersistent": "0",
    }
    assert f"  lightingConfig: 0 -> {lighting}" in lines
    assert f"  disablePersonalLight: (not set) -> {personal_light}" in lines


@pytest.mark.parametrize("doc", ["features.md", "help.md"])
def test_the_docs_name_every_key_a_map_cfg_sets(doc):
    """The features page and the Help page say which keys a map's recommended cfg sets,
    so the list there has to follow MAP_CFG_KEYS."""
    from app.maps.map_ops import MAP_CFG_KEYS
    from tests.conftest import site_doc

    text = site_doc(doc).read_text(encoding="utf-8")
    assert [key for key in MAP_CFG_KEYS if f"`{key}`" not in text] == []


def test_a_recommended_value_dayz_does_not_accept_is_not_written(sample_config, serve_zip):
    from app.maps.map_ops import apply_recommended_cfg
    from app.parsers.servercfg import read_cfg_value

    cfg = sample_config.server_cfg_path
    cfg.write_text(ADMIN_CFG)
    serve_zip(_zip_bytes({
        "Server Config/Regular/serverDZ.cfg": THEIR_FULL_CFG.replace("lightingConfig = 1", "lightingConfig = 1 }"),
    }))
    lines = []

    assert apply_recommended_cfg(
        sample_config, "https://x/repo.zip", "Server Config/Regular/serverDZ.cfg",
        "regular.namalsk", log=lines.append,
    )
    assert read_cfg_value(cfg, "lightingConfig") == "0"
    assert "}" not in "".join(lines)
    assert any(line.startswith("  lightingConfig: kept yours") for line in lines)


def test_a_recommended_cfg_that_already_matches_changes_nothing(sample_config, serve_zip):
    from app.maps.map_ops import apply_recommended_cfg

    cfg = sample_config.server_cfg_path
    cfg.write_text(ADMIN_CFG.replace("lightingConfig = 0;", "lightingConfig = 1;\nenableCfgGameplayFile = 1;"))
    before = cfg.read_text()
    serve_zip(_zip_bytes({"Server Config/Regular/serverDZ.cfg": THEIR_FULL_CFG}))
    lines = []

    assert apply_recommended_cfg(
        sample_config, "https://x/repo.zip", "Server Config/Regular/serverDZ.cfg",
        "regular.namalsk", log=lines.append,
    )
    assert cfg.read_text() == before.replace("dayzOffline.chernarusplus", "regular.namalsk")
    assert lines[-1] == "  serverDZ.cfg already has the map's recommended settings."


def test_restoring_a_map_cfg_backup_keeps_the_admin_password(sample_config):
    from app.maps.map_ops import restore_map_cfg

    cfg = sample_config.server_cfg_path
    cfg.write_text('password = "join";\npasswordAdmin = "current";\n')
    (cfg.parent / "serverDZ.cfg.namalsk.backup").write_text(
        THEIR_CFG.replace('"theirs"', '"older"')
    )

    restore_map_cfg(sample_config, "Namalsk", "regular.namalsk")
    assert _passwords(cfg) == {"password": "join", "passwordAdmin": "current"}


# ── switch_map_cfg: the one rule the CLI and the web share ──

NAMALSK_MISSION = {
    "zip_url": "https://x/repo.zip",
    "server_cfg_path": "Server Config/Regular/serverDZ.cfg",
}


def _no_archive(*args, **kwargs):
    raise AssertionError("the map's archive was fetched")


def test_switch_map_cfg_without_the_recommended_keys_fetches_nothing(sample_config, monkeypatch):
    """`zsm maps switch --no-apply-recommended`."""
    from app.maps.map_ops import switch_map_cfg

    monkeypatch.setattr("app.maps.map_ops.extract_server_cfg_from_zip", _no_archive)
    cfg = sample_config.server_cfg_path
    before = cfg.read_text()
    lines = []

    outcome = switch_map_cfg(
        sample_config, "Namalsk", "regular.namalsk", NAMALSK_MISSION,
        apply_recommended=False, log=lines.append,
    )
    assert outcome == "kept"
    assert cfg.read_text() == before
    assert lines == ["  Kept your current config; only the mission changed."]


def test_switch_map_cfg_warns_when_the_archive_has_no_cfg(sample_config, monkeypatch):
    from app.maps.map_ops import switch_map_cfg

    monkeypatch.setattr("app.maps.map_ops.extract_server_cfg_from_zip", lambda *a, **k: False)
    lines, warnings = [], []

    outcome = switch_map_cfg(
        sample_config, "Namalsk", "regular.namalsk", NAMALSK_MISSION,
        log=lines.append, warn=warnings.append,
    )
    assert outcome == "kept"
    assert lines == []
    assert warnings == [
        "Could not find the recommended config in the map's archive; kept your current config."
    ]


# ── Download only (the Maps page's button, POST /api/maps/download) ──


@pytest.fixture()
def workshop(sample_config, tmp_path):
    """`sample_config` does not sandbox the Workshop dir; this does."""
    sample_config.workshop_content_dir = str(tmp_path / "workshop")
    return tmp_path / "workshop"


def _download(workshop, mod_id: str) -> None:
    (workshop / mod_id / "addons").mkdir(parents=True, exist_ok=True)
    (workshop / mod_id / "addons" / "invented.pbo").write_bytes(b"x")


def test_a_downloaded_maps_world_file_is_looked_for_in_its_own_mods_workshop_folder(
    sample_config, workshop
):
    from app.maps.map_ops import downloaded_map_mod_dirs

    island = WORKSHOP_MAPS["Namalsk"]["mods"]["@Namalsk Island"]
    # Both Namalsk presets ship the one terrain from the one mod; the deps never carry it.
    assert downloaded_map_mod_dirs(sample_config, "namalsk") == [workshop / island]
    assert downloaded_map_mod_dirs(sample_config, "chernarusplus") == []
    assert downloaded_map_mod_dirs(sample_config, "nosuchmap") == []


def test_a_map_counts_as_downloaded_with_its_mission_and_every_mod(sample_config, workshop):
    from app.maps.map_ops import is_map_downloaded

    namalsk = WORKSHOP_MAPS["Namalsk"]
    mission = sample_config.server_dir_path / "mpmissions" / namalsk["mission"]["template"]
    assert is_map_downloaded(sample_config, namalsk) is False

    mission.mkdir(parents=True)
    assert is_map_downloaded(sample_config, namalsk) is False  # an empty folder is not a mission
    (mission / "init.c").write_text("// invented\n")
    _download(workshop, namalsk["mods"]["@Namalsk Island"])
    assert is_map_downloaded(sample_config, namalsk) is False  # the dep is missing

    # A mod in the server dir counts as well as the Workshop's copy.
    (sample_config.server_dir_path / "@Namalsk Survival").mkdir()
    assert is_map_downloaded(sample_config, namalsk) is True



def test_a_switch_with_no_mods_merges_nothing(sample_config, monkeypatch):
    from app.maps.map_ops import apply_mod_xml_to_new_mission
    from app.ops import pipeline

    def _never(ctx):
        raise AssertionError("merged with no mods")

    monkeypatch.setattr(pipeline, "_merge_xml_sync", _never)
    lines: list[str] = []
    assert apply_mod_xml_to_new_mission(sample_config, lines.append) is True
    assert lines == []


# --- an official map the catalog knows only by terrain (announced) ---------


def _mission(root, name, *, types=True):
    db = root / name / "db"
    db.mkdir(parents=True)
    if types:
        (db / "types.xml").write_text("<types/>")


def test_a_shipped_mission_is_found_by_its_terrain(tmp_path):
    from app.maps.map_ops import find_shipped_mission

    assert find_shipped_mission(tmp_path, "nasdara") is None
    _mission(tmp_path, "empty.Nasdara")
    _mission(tmp_path, "dayzOffline.nasdara")
    _mission(tmp_path, "dayzOffline.chernarusplus")
    # Bohemia's prefix wins over a community copy of the same terrain.
    assert find_shipped_mission(tmp_path, "nasdara") == "dayzOffline.nasdara"


def test_a_folder_without_types_xml_is_not_a_mission(tmp_path):
    from app.maps.map_ops import find_shipped_mission

    _mission(tmp_path, "dayzOffline.nasdara", types=False)
    assert find_shipped_mission(tmp_path, "nasdara") is None
    assert find_shipped_mission(tmp_path / "missing", "nasdara") is None


def test_an_announced_map_is_available_only_once_its_mission_is_on_the_server(
    sample_config, unreleased_map
):
    from app.maps.map_ops import get_active_map_name, map_available, vanilla_mission
    from app.parsers.servercfg import set_mission_template

    assert not map_available(sample_config, unreleased_map)
    assert vanilla_mission(sample_config, unreleased_map) is None
    assert vanilla_mission(sample_config, "Livonia") == "dayzOffline.enoch"

    _mission(sample_config.server_dir_path / "mpmissions", "dayzOffline.testlands")
    assert map_available(sample_config, unreleased_map)
    assert vanilla_mission(sample_config, unreleased_map) == "dayzOffline.testlands"

    set_mission_template(sample_config.server_cfg_path, "dayzOffline.testlands")
    assert get_active_map_name(sample_config) == unreleased_map


def test_badlands_switches_to_its_shipped_mission(sample_config):
    from app.maps.map_ops import get_active_map_name, map_available, vanilla_mission
    from app.parsers.servercfg import set_mission_template

    assert map_available(sample_config, "Badlands")
    assert vanilla_mission(sample_config, "Badlands") == "dayzOffline.nasdara"
    set_mission_template(sample_config.server_cfg_path, "dayzOffline.nasdara")
    assert get_active_map_name(sample_config) == "Badlands"


# ── what goes to the new map: the switch dialog (plans/83) ───────────


def _manual(file_id: str, *, terrain: str = "", enabled: bool = True) -> None:
    from app.xml import xml_manual
    from tests.conftest import manual_dir

    xml_manual.create_file(
        file_id, "types", '<types><type name="X"/></types>',
        manual_dir=manual_dir(), terrain=terrain, enabled=enabled,
    )


def _deer_isle_cfg(sample_config) -> None:
    from app.parsers.servercfg import set_mission_template

    set_mission_template(sample_config.server_cfg_path, "empty.deerisle")


def test_the_switch_plan_keeps_the_maps_own_mods_out_of_the_choice(sample_config):
    from app.maps.map_ops import switch_plan
    from tests.conftest import write_test_mods

    write_test_mods(sample_config, ["@CF", "@Trader"], disabled=["@CF"])
    (sample_config.server_dir_path / "@CF").mkdir()

    plan = switch_plan(sample_config, "Deer Isle")

    assert plan["from"] == "Chernarus"
    assert (plan["from_terrain"], plan["to_terrain"]) == ("chernarusplus", "deerisle")
    own = {m["name"]: m for m in plan["map_mods"]}
    assert own["@DeerIsle"] == {
        "name": "@DeerIsle", "id": "1602372402", "dep": False, "on_disk": False, "turn_on": False,
    }
    # Installed but off: the switch turns it on, and it is not a choice.
    assert own["@CF"]["dep"] is True and own["@CF"]["on_disk"] is True
    assert own["@CF"]["turn_on"] is True
    assert [m["name"] for m in plan["mods"]] == ["@Trader"]
    assert plan["off_mods"] == []
    assert plan["cfg"] == {
        "backup": None,
        "recommended": False,
        "keys": ["lightingConfig", "disablePersonalLight", "enableCfgGameplayFile"],
    }


def test_the_switch_plan_lists_the_old_maps_mods_as_leaving(sample_config):
    from app.maps.map_ops import switch_plan
    from tests.conftest import write_test_mods

    _deer_isle_cfg(sample_config)
    write_test_mods(sample_config, ["@DeerIsle", "@CF", "@Trader"])

    plan = switch_plan(sample_config, "Livonia")

    assert plan["from"] == "Deer Isle"
    assert plan["map_mods"] == []
    assert plan["leaving"] == {"removed": ["@DeerIsle"], "kept_deps": ["@CF"]}
    # A dep the switch keeps is an ordinary mod afterwards, so it is a choice.
    assert [m["name"] for m in plan["mods"]] == ["@CF", "@Trader"]


def test_the_switch_plan_lists_a_disabled_mod_as_already_off(sample_config):
    from app.maps.map_ops import switch_plan
    from tests.conftest import write_test_mods

    write_test_mods(sample_config, ["@Trader", "@Old"], disabled=["@Old"])

    plan = switch_plan(sample_config, "Livonia")

    assert [m["name"] for m in plan["mods"]] == ["@Trader"]
    assert plan["off_mods"] == ["@Old"]


def test_the_switch_plan_says_which_manual_files_can_stay_behind(sample_config):
    from app.maps.map_ops import switch_plan

    _manual("everywhere")
    _manual("on-livonia", terrain="enoch")
    _manual("on-chernarus", terrain="chernarusplus")
    _manual("on-sakhal", terrain="sakhal")
    _manual("switched-off", enabled=False)

    rows = {f["id"]: f for f in switch_plan(sample_config, "Livonia")["manual_files"]}

    assert set(rows) == {"everywhere", "on-livonia", "on-chernarus", "on-sakhal"}
    assert (rows["everywhere"]["scope"], rows["everywhere"]["can_leave"]) == ("every", True)
    assert (rows["on-livonia"]["scope"], rows["on-livonia"]["can_leave"]) == ("to", False)
    assert (rows["on-chernarus"]["scope"], rows["on-chernarus"]["can_leave"]) == ("from", False)
    assert (rows["on-sakhal"]["scope"], rows["on-sakhal"]["can_leave"]) == ("other", False)


def test_a_manual_file_cannot_stay_behind_on_the_same_terrain(sample_config):
    """Namalsk -> Namalsk Hardcore keeps the terrain: scoped to the old map a
    file would still apply on the new one, so leaving it behind would do
    nothing."""
    from app.maps.map_ops import switch_plan
    from app.parsers.servercfg import set_mission_template

    set_mission_template(sample_config.server_cfg_path, "regular.namalsk")
    _manual("everywhere")

    rows = switch_plan(sample_config, "Namalsk Hardcore")["manual_files"]
    assert rows[0]["scope"] == "every"
    assert rows[0]["can_leave"] is False


def test_a_manual_file_cannot_stay_behind_an_unknown_terrain(sample_config):
    from app.maps.map_ops import switch_plan
    from app.parsers.servercfg import set_mission_template

    set_mission_template(sample_config.server_cfg_path, "custom.someisland")
    _manual("everywhere")

    plan = switch_plan(sample_config, "Livonia")
    assert plan["from"] is None
    assert plan["manual_files"][0]["can_leave"] is False


def test_a_mod_left_behind_is_disabled_not_removed(sample_config, tmp_mission_dir):
    from app.maps.map_ops import SwitchChoices, leave_behind
    from app.modmgr import modlist
    from tests.conftest import installed_names, write_test_mods

    write_test_mods(sample_config, ["@Trader", "@Keep"])
    (sample_config.server_dir_path / "@Trader").mkdir()
    lines: list[str] = []

    result = leave_behind(
        sample_config, "Livonia", SwitchChoices(disable_mods=["Trader"]), lines.append,
    )

    assert result["left_mods"] == ["@Trader"]
    assert installed_names(sample_config) == ["@Trader", "@Keep"]
    assert modlist.disabled(sample_config) == {"@Trader"}
    assert (sample_config.server_dir_path / "@Trader").is_dir()
    assert any(
        line.startswith("  Turned off @Trader: still installed, off on every map") for line in lines
    )


def test_a_mod_that_went_off_after_the_check_is_skipped_with_a_line(sample_config):
    from app.maps.map_ops import SwitchChoices, leave_behind
    from tests.conftest import write_test_mods

    write_test_mods(sample_config, ["@Trader"], disabled=["@Trader"])
    lines: list[str] = []

    result = leave_behind(sample_config, "Livonia", SwitchChoices(disable_mods=["@Trader"]), lines.append)

    assert result["left_mods"] == []
    assert "  @Trader is already off; nothing to turn off." in lines


def test_a_manual_file_left_behind_is_scoped_to_the_old_map(sample_config):
    from app.maps.map_ops import SwitchChoices, leave_behind
    from app.xml import xml_manual
    from tests.conftest import manual_dir

    _manual("heli-spawns")
    _manual("loot")
    lines: list[str] = []

    result = leave_behind(
        sample_config, "Livonia", SwitchChoices(leave_manual_files=["heli-spawns"]), lines.append,
    )

    assert result["left_manual_files"] == ["heli-spawns"]
    left = xml_manual.get_file("heli-spawns", manual_dir=manual_dir())
    assert left["terrain"] == "chernarusplus"
    assert xml_manual.applies_to(left, "chernarusplus")
    assert not xml_manual.applies_to(left, "enoch")
    assert xml_manual.get_file("loot", manual_dir=manual_dir())["terrain"] == ""
    assert "  Manual XML file heli-spawns stays with Chernarus." in lines


def test_the_switch_turns_on_the_new_maps_own_dep(sample_config):
    from app.maps.map_ops import SwitchChoices, leave_behind
    from app.modmgr import modlist
    from tests.conftest import write_test_mods

    write_test_mods(sample_config, ["@CF", "@Trader"], disabled=["@CF"])
    lines: list[str] = []

    result = leave_behind(sample_config, "Deer Isle", SwitchChoices(), lines.append)

    assert result["turned_on"] == ["@CF"]
    assert modlist.disabled(sample_config) == set()
    assert "  Turned on @CF: Deer Isle needs it." in lines


def test_switching_back_keeps_a_left_mod_off_and_enabling_restores_its_xml(
    sample_config, tmp_mission_dir,
):
    import re

    from app.maps.map_ops import SwitchChoices, leave_behind
    from app.modmgr import modlist
    from app.modmgr.mods import set_mod_enabled
    from app.parsers.servercfg import set_mission_template
    from app.xml.xml_economy import merge_economy_core
    from tests.conftest import manual_dir, write_test_mods, write_xml

    server = sample_config.server_dir_path
    chernarus = "dayzOffline.chernarusplus"
    ce = tmp_mission_dir / "cfgeconomycore.xml"
    write_xml(ce, '<?xml version="1.0"?><economycore><classes/><defaults/></economycore>')
    write_xml(server / "@Bicycles" / "types.xml", '<?xml version="1.0"?><types><type name="Bike"/></types>')
    write_test_mods(sample_config, ["@Bicycles"])
    merge_economy_core(server, chernarus, disabled_mods=set(), manual_dir=manual_dir())

    def live(text: str) -> set[str]:
        return set(re.findall(r'<file name="([^"]+)"', re.sub(r"<!--.*?-->", "", text, flags=re.S)))

    assert "bicycles-types.xml" in live(ce.read_text())

    # Chernarus -> Livonia, leaving @Bicycles behind: parked in Chernarus' file.
    leave_behind(sample_config, "Livonia", SwitchChoices(disable_mods=["@Bicycles"]), lambda _l: None)
    assert "DISABLED @Bicycles:" in ce.read_text()
    assert "bicycles-types.xml" not in live(ce.read_text())
    set_mission_template(sample_config.server_cfg_path, "dayzOffline.enoch")

    # Back to Chernarus with every default: the mod stays off.
    leave_behind(sample_config, "Chernarus", SwitchChoices(), lambda _l: None)
    set_mission_template(sample_config.server_cfg_path, chernarus)
    assert modlist.disabled(sample_config) == {"@Bicycles"}
    assert "bicycles-types.xml" not in live(ce.read_text())

    # Turning it on brings its XML back.
    set_mod_enabled(sample_config, "@Bicycles")
    assert "bicycles-types.xml" in live(ce.read_text())
    assert (tmp_mission_dir / "custom" / "bicycles-types.xml").is_file()


@pytest.mark.parametrize(
    ("choices", "expected"),
    [
        ({"disable_mods": ["CF"]}, "@CF is Deer Isle's dependency and always goes with it."),
        ({"disable_mods": ["@DeerIsle"]}, "@DeerIsle is Deer Isle's own mod and always goes with it."),
        ({"disable_mods": ["@Nope"]}, "@Nope is not in the mod list."),
        ({"disable_mods": ["@Old"]}, "@Old is already turned off."),
        ({"leave_manual_files": ["nope"]}, "There is no enabled manual XML file 'nope'."),
        ({"disable_mods": ["@Trader"], "leave_manual_files": ["everywhere"]}, None),
    ],
)
def test_check_switch_choices_refuses_the_maps_own_dep(sample_config, choices, expected):
    from app.maps.map_ops import SwitchChoices, check_switch_choices, switch_plan
    from tests.conftest import write_test_mods

    write_test_mods(sample_config, ["@CF", "@Trader", "@Old"], disabled=["@Old"])
    _manual("everywhere")

    plan = switch_plan(sample_config, "Deer Isle")
    assert check_switch_choices(plan, SwitchChoices(**choices)) == expected


def test_check_switch_choices_refuses_an_old_map_mod_the_switch_removes(sample_config):
    from app.maps.map_ops import SwitchChoices, check_switch_choices, switch_plan
    from tests.conftest import write_test_mods

    _deer_isle_cfg(sample_config)
    write_test_mods(sample_config, ["@DeerIsle", "@CF", "@Trader"])

    plan = switch_plan(sample_config, "Livonia")
    assert check_switch_choices(plan, SwitchChoices(disable_mods=["@DeerIsle"])) == (
        "@DeerIsle leaves with Deer Isle: the switch removes it, so there is nothing to turn off."
    )
    assert check_switch_choices(plan, SwitchChoices(disable_mods=["@CF"])) is None


def test_the_switch_plan_refuses_an_unknown_or_unreleased_map(sample_config, unreleased_map):
    from app.maps.map_ops import switch_plan

    with pytest.raises(KeyError):
        switch_plan(sample_config, "Atlantis")
    with pytest.raises(ValueError):
        switch_plan(sample_config, unreleased_map)


def test_the_saved_map_cfg_is_dated_when_it_was_saved(sample_config):
    """The copy kept serverDZ.cfg's own mtime, so the
    map switch dialog said "saved Oct 13" for a copy made that day."""
    import os
    import time

    from app.maps.map_ops import backup_map_cfg

    old = time.time() - 3 * 86400
    os.utime(sample_config.server_cfg_path, (old, old))
    backup = backup_map_cfg(sample_config)
    assert backup is not None and backup.stat().st_mtime > time.time() - 60


def test_a_map_switch_keeps_the_bikeys_another_mod_or_the_game_needs(sample_config, tmp_server_dir):
    """The switch deleted every key the old map mod shipped, so a mod signed
    with the same key, or the game's own dayz.bikey, lost it."""
    from app.maps.map_ops import remove_old_map_mods_from_server

    keys = sample_config.keys_dir
    keys.mkdir(parents=True, exist_ok=True)
    for name in ("author.bikey", "dayz.bikey", "maponly.bikey"):
        (keys / name).write_text("key")
    old_map = tmp_server_dir / "@OldMap" / "keys"
    old_map.mkdir(parents=True)
    for name in ("author.bikey", "dayz.bikey", "maponly.bikey"):
        (old_map / name).write_text("key")
    other = tmp_server_dir / "@OtherMod" / "keys"
    other.mkdir(parents=True)
    (other / "author.bikey").write_text("key")

    assert remove_old_map_mods_from_server(sample_config, ["@OldMap"]) == ["@OldMap"]

    assert sorted(p.name for p in keys.iterdir()) == ["author.bikey", "dayz.bikey"]
    assert not (tmp_server_dir / "@OldMap").exists()
