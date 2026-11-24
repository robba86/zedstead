"""Rollback after a mod update broke the server (TODO C5).

The failure: a mod publishes a new version, the update copies it over the
working one, and the server never comes back. The old bytes existed in exactly
one place — `<server_dir>/@Mod` — and SteamCMD's workshop cache was overwritten
first, so once `remove_server_mods` has run there is nothing to go back to.

So the tests that carry this are about *where* and *when* the previous version
is kept, and about not offering a rollback to somebody whose server is fine.
"""

from __future__ import annotations

import pytest

from app.ops import fileops, mod_rollback
from tests.conftest import write_test_ids, write_test_mods


def _install(server_dir, name, content="pbo"):
    mod = server_dir / name
    (mod / "Addons").mkdir(parents=True, exist_ok=True)
    (mod / "Addons" / "data.pbo").write_text(content, encoding="utf-8")
    return mod


# ---------------------------------------------------------------------------
# where the previous version is kept
# ---------------------------------------------------------------------------


def test_removing_the_old_mods_keeps_them_instead_of_deleting_them(sample_config):
    """This is the step in front of every copy, and the last moment the
    previous versions exist anywhere."""
    _install(sample_config.server_dir_path, "@CF", "old")
    _install(sample_config.server_dir_path, "@Expansion", "old")

    fileops.remove_server_mods(sample_config, log=lambda s: None)

    assert not (sample_config.server_dir_path / "@CF").exists()
    state = mod_rollback.rollback_state(sample_config)
    assert state["available"] is True
    assert sorted(state["mods"]) == ["@CF", "@Expansion"]
    kept = mod_rollback.rollback_dir(sample_config) / "@CF" / "Addons" / "data.pbo"
    assert kept.read_text(encoding="utf-8") == "old"


def test_the_stash_is_hidden_from_every_mod_walk(sample_config):
    """A leading dot, so the `@*` glob and the Cleanup page's listings skip it
    without being taught about it."""
    _install(sample_config.server_dir_path, "@CF")
    fileops.remove_server_mods(sample_config, log=lambda s: None)

    assert mod_rollback.ROLLBACK_DIRNAME.startswith(".")
    assert list(sample_config.server_dir_path.glob("@*")) == []


def test_only_one_generation_is_kept(sample_config):
    """Two would mean nobody can say how much disk this costs."""
    _install(sample_config.server_dir_path, "@CF", "gen1")
    fileops.remove_server_mods(sample_config, log=lambda s: None)
    _install(sample_config.server_dir_path, "@CF", "gen2")
    fileops.remove_server_mods(sample_config, log=lambda s: None)

    kept = mod_rollback.rollback_dir(sample_config) / "@CF" / "Addons" / "data.pbo"
    assert kept.read_text(encoding="utf-8") == "gen2"
    assert mod_rollback.rollback_state(sample_config)["mods"] == ["@CF"]


def test_an_undo_does_not_overwrite_the_stash(sample_config):
    """`_undo_copy_mods` removes the mods a failed pipeline just copied. If
    that stashed, the rollback of the update would replace the good previous
    version with the broken new one — the exact opposite of the feature."""
    _install(sample_config.server_dir_path, "@CF", "good")
    fileops.remove_server_mods(sample_config, log=lambda s: None)
    _install(sample_config.server_dir_path, "@CF", "broken")

    fileops.remove_server_mods(sample_config, log=lambda s: None, stash=False)

    kept = mod_rollback.rollback_dir(sample_config) / "@CF" / "Addons" / "data.pbo"
    assert kept.read_text(encoding="utf-8") == "good"


def test_the_pipeline_undo_passes_stash_false(sample_config, monkeypatch):
    """Asserted on the call, because the invariant above lives in one keyword
    argument and nothing else would notice it going missing."""
    from app.ops import pipeline

    seen = {}
    monkeypatch.setattr(
        fileops,
        "remove_server_mods",
        lambda config, log=None, stash=True: seen.update(stash=stash),
    )
    monkeypatch.setattr(mod_rollback, "rollback_state", lambda config: {"available": True})
    ctx = pipeline.PipelineContext(
        config=sample_config,
        emit=lambda s: None,
        runner=None,
        should_run=lambda key: True,
        rollback_keys=frozenset({"copy_mods", "remove_old_mods"}),
        stashed_mods=["@CF"],  # the same run set them aside: the copies go
    )
    pipeline._undo_copy_mods(ctx)
    assert seen == {"stash": False}


async def test_a_rollback_of_the_copy_alone_puts_the_previous_mods_back(sample_config, workshop):
    """The web lets the admin pick copy_mods without remove_old_mods. Its undo
    removed every mod and nothing put the stash back."""
    from app.ops import pipeline

    server = sample_config.server_dir_path
    _install(server, "@CF", "old")
    done, ctx, _lines = _copy_steps_and_ctx(sample_config, workshop)
    for step in done:
        await step.run(ctx)
    fresh = pipeline.PipelineContext(
        config=sample_config, emit=lambda s: None, runner=None, should_run=lambda key: True
    )

    await pipeline.rollback_pipeline([done[1]], fresh)

    assert (server / "@CF" / "Addons" / "data.pbo").read_text(encoding="utf-8") == "old"


async def test_a_copy_with_nothing_set_aside_keeps_the_copied_mods(sample_config, workshop):
    """No stash to put back: removing the copies left the server with no mods."""
    from app.ops import pipeline

    server = sample_config.server_dir_path
    done, ctx, lines = _copy_steps_and_ctx(sample_config, workshop)
    await done[1].run(ctx)  # copy_mods alone: nothing was set aside

    await pipeline.rollback_pipeline([done[1]], ctx)

    assert (server / "@CF" / "Addons" / "data.pbo").read_text(encoding="utf-8") == "new"
    assert "Only the mods the stash holds go back. The other mods stay as they are." in lines
    assert "No previous mod versions were kept, so none were put back." in lines


def test_the_manifest_is_written_atomically(sample_config, monkeypatch):
    """A crash in the middle of a plain write_text left half a manifest."""
    from app.shared import json_utils

    written = []
    real = json_utils.atomic_write_text

    def _record(path, text, **kw):
        written.append(path)
        real(path, text, **kw)

    monkeypatch.setattr(mod_rollback, "atomic_write_text", _record)
    _install(sample_config.server_dir_path, "@CF")
    fileops.remove_server_mods(sample_config, log=lambda s: None)

    assert written == [mod_rollback.rollback_dir(sample_config) / mod_rollback.MANIFEST_NAME]
    assert mod_rollback.rollback_state(sample_config)["available"] is True


def test_nothing_installed_leaves_no_empty_stash(sample_config):
    fileops.remove_server_mods(sample_config, log=lambda s: None)
    assert mod_rollback.rollback_state(sample_config)["available"] is False
    assert not mod_rollback.rollback_dir(sample_config).exists()


# ---------------------------------------------------------------------------
# going back
# ---------------------------------------------------------------------------


def test_restore_puts_the_previous_version_back(sample_config):
    _install(sample_config.server_dir_path, "@CF", "old")
    fileops.remove_server_mods(sample_config, log=lambda s: None)
    _install(sample_config.server_dir_path, "@CF", "new-and-broken")

    result = mod_rollback.restore(sample_config)

    assert result.ok is True
    assert result.restored == ["@CF"]
    live = sample_config.server_dir_path / "@CF" / "Addons" / "data.pbo"
    assert live.read_text(encoding="utf-8") == "old"


def test_restore_consumes_the_stash(sample_config):
    """After it, the mods on disk ARE the stashed ones — keeping an empty
    promise around would be worse than none."""
    _install(sample_config.server_dir_path, "@CF", "old")
    fileops.remove_server_mods(sample_config, log=lambda s: None)

    mod_rollback.restore(sample_config)

    assert mod_rollback.rollback_state(sample_config)["available"] is False
    assert not mod_rollback.rollback_dir(sample_config).exists()


def test_a_mod_that_fails_to_restore_stays_in_the_stash(sample_config, monkeypatch):
    """restore() threw the whole stash away after a failure, so the one copy
    of a mod's previous version that could not be put back was deleted."""
    server_dir = sample_config.server_dir_path
    _install(server_dir, "@CF", "old")
    _install(server_dir, "@Expansion", "old")
    fileops.remove_server_mods(sample_config, log=lambda s: None)
    _install(server_dir, "@CF", "new-and-broken")
    _install(server_dir, "@Expansion", "new-and-broken")
    real_rmtree = mod_rollback.shutil.rmtree

    def rmtree(path, *args, **kwargs):
        if str(path) == str(server_dir / "@Expansion"):
            raise PermissionError(13, "Permission denied", str(path))
        real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(mod_rollback.shutil, "rmtree", rmtree)

    result = mod_rollback.restore(sample_config)

    assert result.restored == ["@CF"]
    assert "@Expansion" in result.message
    kept = mod_rollback.rollback_dir(sample_config) / "@Expansion" / "Addons" / "data.pbo"
    assert kept.read_text(encoding="utf-8") == "old"
    state = mod_rollback.rollback_state(sample_config)
    assert state["available"] is True
    assert state["mods"] == ["@Expansion"]


def test_restore_with_nothing_kept_says_so_rather_than_pretending(sample_config):
    result = mod_rollback.restore(sample_config)
    assert result.ok is False
    assert result.restored == []
    assert "no previous" in result.message.lower()


def test_discard_frees_it(sample_config):
    _install(sample_config.server_dir_path, "@CF")
    fileops.remove_server_mods(sample_config, log=lambda s: None)

    assert mod_rollback.discard(sample_config) is True
    assert mod_rollback.discard(sample_config) is False
    assert mod_rollback.rollback_state(sample_config)["available"] is False


# ---------------------------------------------------------------------------
# when to offer it
# ---------------------------------------------------------------------------


def _rpt(sample_config, text):
    profiles = sample_config.server_dir_path / sample_config.profiles_path
    profiles.mkdir(parents=True, exist_ok=True)
    log = profiles / "DayZServer_x64.RPT"
    log.write_text(text, encoding="utf-8")
    return log


NEVER_JOINABLE = """Current time:  2026/09/21 18:00:00
DayZ  version 1.29
18:00:01 Mission read.
"""

JOINABLE = """Current time:  2026/09/21 18:00:00
DayZ  version 1.29
18:00:30 Player connect enabled.
"""


def test_a_run_that_never_became_joinable_is_the_trigger(sample_config):
    _install(sample_config.server_dir_path, "@CF")
    fileops.remove_server_mods(sample_config, log=lambda s: None)
    _rpt(sample_config, NEVER_JOINABLE)

    state = mod_rollback.should_offer_rollback(sample_config)
    assert state["offer"] is True
    assert "never became joinable" in state["reason"] or "joinable" in state["reason"]


def test_a_healthy_server_is_not_offered_a_rollback(sample_config):
    """A rollback button on a working server is an invitation to undo an
    update that worked."""
    _install(sample_config.server_dir_path, "@CF")
    fileops.remove_server_mods(sample_config, log=lambda s: None)
    _rpt(sample_config, JOINABLE)

    state = mod_rollback.should_offer_rollback(sample_config)
    assert state["available"] is True
    assert state["offer"] is False


def test_no_log_means_no_offer(sample_config):
    _install(sample_config.server_dir_path, "@CF")
    fileops.remove_server_mods(sample_config, log=lambda s: None)

    state = mod_rollback.should_offer_rollback(sample_config)
    assert state["available"] is True
    assert state["offer"] is False


def test_nothing_kept_means_no_offer_even_after_a_bad_run(sample_config):
    _rpt(sample_config, NEVER_JOINABLE)
    state = mod_rollback.should_offer_rollback(sample_config)
    assert state["offer"] is False


# ---------------------------------------------------------------------------
# the endpoints
# ---------------------------------------------------------------------------


PASSWORD = "testpass123"


@pytest.fixture()
def client(sample_config, monkeypatch):
    from app.server import service_control
    from app.web import server
    from app.web.auth import hash_password

    monkeypatch.setattr(service_control, "is_server_running", lambda config=None: False)
    sample_config.web_password_hash = hash_password(PASSWORD)
    from fastapi.testclient import TestClient

    tclient = TestClient(server.create_app(sample_config))
    tclient.post("/api/auth/login", json={"password": PASSWORD})
    return tclient


def test_the_rollback_endpoint_needs_a_session(sample_config):
    from fastapi.testclient import TestClient

    from app.web import server

    app = server.create_app(sample_config)
    assert TestClient(app).get("/api/mods/rollback").status_code == 401


def test_the_endpoint_reports_and_restores(client, sample_config):
    _install(sample_config.server_dir_path, "@CF", "old")
    fileops.remove_server_mods(sample_config, log=lambda s: None)
    _install(sample_config.server_dir_path, "@CF", "broken")

    body = client.get("/api/mods/rollback").json()
    assert body["available"] is True and body["mods"] == ["@CF"]

    resp = client.post("/api/mods/rollback/restore")
    assert resp.status_code == 200, resp.text
    assert resp.json()["restored"] == ["@CF"]
    live = sample_config.server_dir_path / "@CF" / "Addons" / "data.pbo"
    assert live.read_text(encoding="utf-8") == "old"


def test_restoring_while_the_server_runs_is_refused(client, sample_config, monkeypatch):
    from app.server import service_control

    _install(sample_config.server_dir_path, "@CF")
    fileops.remove_server_mods(sample_config, log=lambda s: None)
    monkeypatch.setattr(service_control, "is_server_running", lambda config=None: True)

    resp = client.post("/api/mods/rollback/restore")
    assert resp.status_code == 409
    assert mod_rollback.rollback_state(sample_config)["available"] is True


def test_restoring_nothing_is_a_409_not_a_silent_ok(client):
    assert client.post("/api/mods/rollback/restore").status_code == 409


def test_the_fixed_path_is_not_eaten_by_the_per_mod_route(client):
    """`/api/mods/{ref}` is declared last for exactly this reason; a
    `rollback` mod detail lookup would be a 404 with a confusing message."""
    body = client.get("/api/mods/rollback").json()
    assert "available" in body


# ---------------------------------------------------------------------------
# a copy over an installed mod (2026 Q4)
# ---------------------------------------------------------------------------


@pytest.fixture
def workshop(sample_config, tmp_path):
    """A pinned workshop cache: `workshop_dir` otherwise finds the host's."""
    sample_config.workshop_content_dir = str(tmp_path / "workshop" / "content" / "221100")
    sample_config.workshop_dir.mkdir(parents=True, exist_ok=True)

    def publish(mod_id, files):
        item = sample_config.workshop_dir / mod_id
        if item.exists():
            import shutil

            shutil.rmtree(item)
        for rel, text in files.items():
            (item / rel).parent.mkdir(parents=True, exist_ok=True)
            (item / rel).write_text(text, encoding="utf-8")

    return publish


def test_a_reinstall_replaces_the_mod_and_keeps_the_old_one(sample_config, workshop):
    """MZSS 2.0 renamed its PBO; copying over the old folder loaded both."""
    server = sample_config.server_dir_path
    ids = {"@MZSS": "111"}
    workshop("111", {"Addons/MutantSpawnSystem.pbo": "v1"})
    assert fileops.copy_mod_to_server(sample_config, "@MZSS", ids, log=lambda s: None)
    workshop("111", {"Addons/MZSS.pbo": "v2"})

    assert fileops.copy_mod_to_server(sample_config, "@MZSS", ids, log=lambda s: None)

    assert sorted(p.name for p in (server / "@MZSS" / "Addons").iterdir()) == ["MZSS.pbo"]
    kept = mod_rollback.rollback_dir(sample_config) / "@MZSS" / "Addons" / "MutantSpawnSystem.pbo"
    assert kept.read_text(encoding="utf-8") == "v1"


def test_one_operation_keeps_every_mod_it_replaced(sample_config, workshop):
    """Per mod, each set-aside would have wiped the one before it."""
    server = sample_config.server_dir_path
    _install(server, "@Old", "from the last update")
    fileops.remove_server_mods(sample_config, log=lambda s: None)  # the last update's stash
    ids = {"@A": "1", "@B": "2"}
    for name, mid in ids.items():
        _install(server, name, f"installed {name}")
        workshop(mid, {"Addons/data.pbo": f"new {name}"})

    generation = mod_rollback.Generation(sample_config)
    for name in ids:
        fileops.copy_mod_to_server(sample_config, name, ids, log=lambda s: None, generation=generation)

    state = mod_rollback.rollback_state(sample_config)
    assert sorted(state["mods"]) == ["@A", "@B"], "the operation's first replaces the update's"
    for name in ids:
        kept = mod_rollback.rollback_dir(sample_config) / name / "Addons" / "data.pbo"
        assert kept.read_text(encoding="utf-8") == f"installed {name}"


def test_the_pipeline_copy_leaves_the_updates_stash_alone(sample_config, workshop):
    """remove_old_mods ran first, so nothing is installed at copy time."""
    server = sample_config.server_dir_path
    _install(server, "@CF", "old")
    write_test_mods(sample_config, ["@CF"])
    write_test_ids({'@CF': '1559212036'})
    workshop("1559212036", {"Addons/data.pbo": "new"})

    fileops.remove_server_mods(sample_config, log=lambda s: None)
    fileops.copy_all_mods_to_server(sample_config, log=lambda s: None)

    kept = mod_rollback.rollback_dir(sample_config) / "@CF" / "Addons" / "data.pbo"
    assert kept.read_text(encoding="utf-8") == "old"
    assert (server / "@CF" / "Addons" / "data.pbo").read_text(encoding="utf-8") == "new"


def _copy_steps_and_ctx(sample_config, workshop):
    """@CF installed as "old", "new" in the Workshop, and the two pipeline steps that swap them."""
    from app.ops import pipeline

    write_test_mods(sample_config, ["@CF"])
    write_test_ids({"@CF": "1559212036"})
    workshop("1559212036", {"Addons/data.pbo": "new"})
    steps = {s.key: s for s in pipeline.build_update_steps(sample_config)}
    lines: list[str] = []
    ctx = pipeline.PipelineContext(
        config=sample_config, emit=lines.append, runner=None, should_run=lambda key: True
    )
    return [steps["remove_old_mods"], steps["copy_mods"]], ctx, lines


async def test_a_failed_update_puts_the_previous_mods_back(sample_config, workshop):
    """2026 Q4, a failed server download in the test container: the undo
    removed the new copies, nothing put the old ones back, and the server
    booted with no mods. A boot without a mod deletes its saved objects."""
    from app.ops import pipeline

    server = sample_config.server_dir_path
    _install(server, "@CF", "old")
    done, ctx, lines = _copy_steps_and_ctx(sample_config, workshop)
    for step in done:
        await step.run(ctx)
    assert (server / "@CF" / "Addons" / "data.pbo").read_text(encoding="utf-8") == "new"

    await pipeline.rollback_pipeline(done, ctx)

    assert (server / "@CF" / "Addons" / "data.pbo").read_text(encoding="utf-8") == "old"
    assert mod_rollback.rollback_state(sample_config)["available"] is False
    assert "Rolled back: Remove old mods from server" in lines


async def test_an_update_that_set_nothing_aside_leaves_an_older_stash_alone(sample_config, workshop):
    """The stash is then an earlier operation's, not what this update replaced."""
    from app.ops import pipeline

    server = sample_config.server_dir_path
    _install(server, "@Old", "from the last update")
    fileops.remove_server_mods(sample_config, log=lambda s: None)
    done, ctx, _lines = _copy_steps_and_ctx(sample_config, workshop)
    for step in done:
        await step.run(ctx)

    await pipeline.rollback_pipeline(done, ctx)

    assert not (server / "@Old").exists()
    assert not (server / "@CF").exists()
    assert mod_rollback.rollback_state(sample_config)["mods"] == ["@Old"]


def test_a_rollback_from_a_fresh_context_restores_the_stash(sample_config):
    """The web's rollback endpoint builds its own context, so it cannot tell
    whose stash it is and puts back the one that is there."""
    from app.ops import pipeline

    server = sample_config.server_dir_path
    _install(server, "@CF", "old")
    fileops.remove_server_mods(sample_config, log=lambda s: None)
    ctx = pipeline.PipelineContext(
        config=sample_config, emit=lambda s: None, runner=None, should_run=lambda key: True
    )

    pipeline._undo_remove_old_mods(ctx)

    assert (server / "@CF" / "Addons" / "data.pbo").read_text(encoding="utf-8") == "old"



async def test_a_fresh_rollback_replaces_only_the_mods_the_stash_holds(sample_config):
    """One mod's reinstall leaves a stash of that mod alone. A web rollback of
    copy_mods removed every mod and put back only that one, so the server was
    left without @CF."""
    from app.ops import pipeline

    server = sample_config.server_dir_path
    _install(server, "@TraderPlus", "old")
    mod_rollback.stash_installed_mods(sample_config, ["@TraderPlus"])
    _install(server, "@TraderPlus", "new")
    _install(server, "@CF", "cf")
    steps = {s.key: s for s in pipeline.build_update_steps(sample_config)}
    fresh = pipeline.PipelineContext(
        config=sample_config, emit=lambda s: None, runner=None, should_run=lambda key: True
    )

    failed = await pipeline.rollback_pipeline([steps["copy_mods"]], fresh)

    assert failed == []
    assert (server / "@CF" / "Addons" / "data.pbo").read_text(encoding="utf-8") == "cf"
    assert (server / "@TraderPlus" / "Addons" / "data.pbo").read_text(encoding="utf-8") == "old"

def test_a_mod_that_cannot_be_set_aside_is_copied_over_as_before(sample_config, workshop, monkeypatch):
    server = sample_config.server_dir_path
    _install(server, "@A", "old")
    workshop("1", {"Addons/new.pbo": "new"})
    monkeypatch.setattr(mod_rollback, "stash_installed_mods", lambda *a, **k: [])
    lines = []

    assert fileops.copy_mod_to_server(sample_config, "@A", {"@A": "1"}, log=lines.append)

    assert sorted(p.name for p in (server / "@A" / "Addons").iterdir()) == ["data.pbo", "new.pbo"]
    assert any("Could not set the installed @A aside" in line for line in lines)


def test_restore_puts_the_restored_mods_keys_back(sample_config):
    """Step 0 of plans/78: the update deleted the old keys and the stash never
    held them, so a restored server failed the signature check."""
    server = sample_config.server_dir_path
    mod = _install(server, "@CF", "old")
    (mod / "Keys").mkdir()
    (mod / "Keys" / "cf.bikey").write_text("old key", encoding="utf-8")
    write_test_mods(sample_config, ["@CF"])
    fileops.remove_server_mods(sample_config, log=lambda s: None)
    fileops.copy_bikey_files(sample_config, log=lambda s: None)  # an update with nothing copied
    assert not (sample_config.keys_dir / "cf.bikey").exists()
    lines: list[str] = []

    mod_rollback.restore(sample_config, log=lines.append)

    assert (sample_config.keys_dir / "cf.bikey").read_text(encoding="utf-8") == "old key"
    assert not any("[" in l and "]" in l for l in lines if "bikey" in l)
