"""The RPT tailer skips a flooded backlog instead of replaying it for hours."""

from __future__ import annotations

import asyncio

from app.web import rpt


class _Hub:
    """Collects what the tailer broadcasts."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    async def broadcast(self, msg, channel=None) -> None:
        self.lines.append(msg["line"])


def _tailer(sample_config, tmp_path):
    profiles = sample_config.server_dir_path / sample_config.profiles_path
    profiles.mkdir(parents=True, exist_ok=True)
    log = profiles / "DayZServer_x.RPT"
    log.write_bytes(b"boot\n")
    hub = _Hub()
    tailer = rpt.RptTailer(sample_config, hub)
    asyncio.run(tailer._tick())
    return tailer, hub, log


def test_a_small_append_is_sent_line_by_line(sample_config, tmp_path):
    tailer, hub, log = _tailer(sample_config, tmp_path)
    with open(log, "ab") as f:
        f.write(b"one\ntwo\n")
    asyncio.run(tailer._tick())
    assert hub.lines[-2:] == ["one", "two"]


def test_a_flood_is_skipped_to_the_tail_with_one_note(sample_config, tmp_path):
    tailer, hub, log = _tailer(sample_config, tmp_path)
    flood = b"SCRIPT : an invented mod line, printed over and over\n"
    with open(log, "ab") as f:
        f.write(flood * (rpt.BACKLOG_SKIP_BYTES // len(flood) * 4))
        f.write(b"the last line\n")
    before = len(hub.lines)
    asyncio.run(tailer._tick())
    sent = hub.lines[before:]
    assert sent[0].startswith("[Zedstead] Skipped ")
    assert sent[-1] == "the last line"
    assert len(sent) <= rpt.MAX_LINES_PER_TICK + 1
    assert all(line == flood.decode().rstrip("\n") for line in sent[1:-1])


def test_the_tail_start_is_a_whole_line(tmp_path):
    log = tmp_path / "x.RPT"
    log.write_bytes(b"abcdefghij\n" * 1000)
    pos = rpt._initial_seek_pos(log)
    assert log.read_bytes()[pos - 1 : pos] == b"\n"
