#!/usr/bin/env bash
#
# zsm dev mode — one command, two processes:
#
#   [api]  FastAPI backend on :8433, restarted by uvicorn when app/ changes
#   [web]  Vite dev server on :5173 with HMR, proxying /api + /ws to [api].
#          It listens on 0.0.0.0, so other machines on the LAN can open it.
#          [api] stays on 127.0.0.1: Vite proxies to it on this machine.
#
# Open the *Vite* URL (:5173). Port 8433 serves the last committed build in
# app/web/static/, so changes to webui/src won't show there until you run
# `npm run build`.
#
# Env overrides:  ZSM_API_PORT (8433)   ZSM_WEB_PORT (5173)
# Ctrl+C stops both.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
VITE="$ROOT/webui/node_modules/.bin/vite"

API_PORT="${ZSM_API_PORT:-8433}"
WEB_PORT="${ZSM_WEB_PORT:-5173}"
# Read by webui/vite.config.js so a non-default backend port still proxies.
export ZSM_API_PORT="$API_PORT"

if [ -t 1 ]; then
  C_API=$'\033[36m'; C_WEB=$'\033[35m'; C_ERR=$'\033[31m'; C_OFF=$'\033[0m'
else
  C_API=''; C_WEB=''; C_ERR=''; C_OFF=''
fi

die() { printf '%sdev.sh: %s%s\n' "$C_ERR" "$1" "$C_OFF" >&2; exit 1; }

# Tag each line of a child's output. Pure bash (no `sed -u`) so nothing is
# block-buffered; the `|| [ -n "$line" ]` tail flushes a final unterminated line.
prefix() {
  local color="$1" tag="$2" line
  while IFS= read -r line || [ -n "$line" ]; do
    printf '%s[%s]%s %s\n' "$color" "$tag" "$C_OFF" "$line"
  done
}

port_busy() { (exec 3<>"/dev/tcp/127.0.0.1/$1") >/dev/null 2>&1; }

# --- preflight -------------------------------------------------------------

[ -x "$PY" ] || die "no virtualenv at .venv/ — create it with:
  uv venv .venv --python 3.12
  uv pip install --python .venv/bin/python -e \".[dev]\""

if [ ! -x "$VITE" ]; then
  printf '%s[web]%s node_modules missing — running npm install…\n' "$C_WEB" "$C_OFF"
  (cd "$ROOT/webui" && npm install)
fi

# `zsm web` prompts for a password when none is set, but that prompt would be
# swallowed by the line-prefixing below — so require it up front instead.
set +e
"$PY" - <<'EOF'
from app.shared.config import DayzConfig

cfg = DayzConfig.load_or_none()
raise SystemExit(2 if cfg is None else (0 if cfg.web_password_hash else 1))
EOF
cfg_state=$?
set -e
case "$cfg_state" in
  1) die "no web UI password set yet — run once:  $PY -m app web --set-password" ;;
  2) die "no config yet — run the wizard once:  $PY -m app init" ;;
esac

# `port_busy X && die` would abort the script via `set -e` on the *not busy*
# path (the && list returns non-zero), so test with an explicit `if`.
if port_busy "$API_PORT"; then
  die "port $API_PORT is already in use (another zsm web running?)"
fi
if port_busy "$WEB_PORT"; then
  die "port $WEB_PORT is already in use (another vite running?)"
fi

# --- run -------------------------------------------------------------------

api_pid=''; web_pid=''
cleanup() {
  trap - INT TERM EXIT
  [ -n "$api_pid" ] && kill -TERM "$api_pid" 2>/dev/null || true
  [ -n "$web_pid" ] && kill -TERM "$web_pid" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup INT TERM EXIT

"$PY" -m app web --reload --host 127.0.0.1 --port "$API_PORT" \
  > >(prefix "$C_API" api) 2>&1 &
api_pid=$!

cd "$ROOT/webui"
"$VITE" --host 0.0.0.0 --port "$WEB_PORT" --strictPort \
  > >(prefix "$C_WEB" web) 2>&1 &
web_pid=$!

printf '\n  Open %shttp://localhost:%s%s (or this machine'"'"'s LAN address on that port).\n' \
  "$C_WEB" "$WEB_PORT" "$C_OFF"
printf '  Edits under webui/src hot-reload,\n'
printf '  edits under app/ restart the backend (you stay logged in).\n'
printf '  Ctrl+C stops both.\n\n'

set +e
wait -n
status=$?
set -e
printf '\n%sdev.sh: a dev process exited (status %s) — stopping the other%s\n' \
  "$C_ERR" "$status" "$C_OFF" >&2
