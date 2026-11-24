# tests/: fixtures and a green run

The root file holds how to run the suite and the isolation fixtures (Testing).

- Never paste Bohemia's or a mod's file into a test, not even a few lines of it. Use an invented fixture in the file's shape, `conftest.bohemia_ce` (Bohemia's mission files at a pinned commit, cut with `ce_lines`, `ce_pick`, `ce_block`), `conftest.dayz_docs`, or `conftest.private_fixture` (server output: logs, `ban.txt`, `serverDZ.cfg`, from the private repository checked out at `tests/private/`).
- A run that prints the "fixtures missing: this run is not green" section is not a green run, whatever the pass count says: those tests skipped. `conftest.pytest_terminal_summary` prints it from the skips `count_fixture_skip` counts (`tests/test_bohemia_ce.py`).
