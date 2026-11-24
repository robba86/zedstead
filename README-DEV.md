# Developer Guide — Running & Debugging Zedstead

How to run `zsm` from source and work on the web UI. For architecture and
conventions, see `CLAUDE.md`.

All commands assume the local virtualenv at `.venv/` (see `CLAUDE.md` →
*Environment* for first-time setup with `uv`):

```
uv pip install --python .venv/bin/python -e ".[dev]"
```

---

## Run the app from source

```
.venv/bin/python -m app          # no subcommand → init if needed, then the web UI
.venv/bin/python -m app web      # the same thing, explicitly
.venv/bin/python -m app run      # any CLI subcommand works standalone
.venv/bin/python -m app --help   # list every command
```

`zsm web` binds `127.0.0.1:8433` by default (`0.0.0.0` automatically under
Docker/SSH). First run prompts for a password, stored as a scrypt hash in
`config.json`.

---

## Working on the web UI

The frontend is React (plain JSX, Vite) in `webui/`. Built assets are
**committed** to `app/web/static/` — there is no build step in CI or in the
installer, so a `webui/src` change that isn't rebuilt ships a stale UI silently.

### Live-reload development

```
make dev          # or: ./scripts/dev.sh
```

One command, two processes, prefixed output, Ctrl+C stops both:

```
[api]  .venv/bin/python -m app web --reload    127.0.0.1:8433
[web]  vite                                    localhost:5173
```

Open **http://localhost:5173** — port 8433 serves the last committed build in
`app/web/static/`, so `webui/src` edits don't show there until `npm run build`.

- **`webui/src` edits** → Vite HMR, no reload.
- **`app/` edits** → uvicorn restarts the backend (`--reload`), and **you stay
  logged in**: sessions are in-memory and would otherwise die with the worker,
  so dev mode mirrors them to `~/.config/zsm/dev-sessions.json`. That file is
  written **only** under `--reload` (gated on `ZSM_DEV_RELOAD=1`); a normal
  `zsm web` is unchanged. Delete it to force a fresh login.

The Vite dev server proxies `/api` and `/ws` to the backend
(`webui/vite.config.js`), which keeps the browser same-origin — the session
cookie and the WebSocket upgrade behave exactly as they do on 8433.

Ports are overridable; the script exports `ZSM_API_PORT` so the proxy follows:

```
ZSM_API_PORT=8455 ZSM_WEB_PORT=5199 make dev
```

`dev.sh` refuses to start on a busy port, runs `npm install` if `node_modules`
is missing, and stops the survivor if either process exits. It also bails early
when no config/password exists yet (`python -m app init` /
`python -m app web --set-password`) — that prompt can't be answered through the
prefixed output.

Running the two halves by hand still works:

```
.venv/bin/python -m app web --reload     # terminal 1
cd webui && npm run dev                  # terminal 2
```

### Before committing

```
cd webui && npm run build        # or: make web-build
```

Then commit the regenerated `app/web/static/` diff **in the same commit** as the
`webui/src` change. There is no JS test harness; a clean `npm run build` is the
only compile gate.

---

## Tests

```
.venv/bin/python -m pytest tests/ -x -q --tb=short   # full suite
.venv/bin/python -m pytest tests/test_mods.py        # one file
.venv/bin/python -m pytest tests/test_mods.py::test_name
```

`pytest-asyncio` runs in auto mode, so async tests need no marker. Nothing in
the suite touches a real DayZ server — everything uses temp dirs and fixture
XML (`tests/conftest.py`).

Web-app tests that poll a job **must** use `with TestClient(app) as client:` —
a bare `TestClient(app)` opens a fresh event loop per request and the job never
resumes. See `tests/test_web_jobs.py` for the pattern.
