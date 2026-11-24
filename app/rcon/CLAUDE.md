# app/rcon/: the BattlEye RCon client

`rcon_ops.py` is the one-shot client, `rcon_manager.py` the panel's persistent connection, both on `berconpy`. The root file holds the `is_remote_rcon` skip rule.

## berconpy noise

- A closed RCon port is the normal case, and berconpy logs it as an ERROR traceback on every retry. `rcon_manager._quiet_unreachable_errors()` is the single mute point. Never silence the whole `berconpy` logger, which hides real login failures.
- Its filter sits on the `berconpy.io` logger, because a filter sees only records logged through its own logger. It re-emits `ConnectionError` records at DEBUG.
- It mutes two more, and only these: `failed to connect to the server`, matched by message so `password authentication was denied` still prints, and the keep-alive `AssertionError`, matched on the `asyncio` logger by its `_send_keep_alive` frame. Tests: `tests/test_rcon_manager.py`.
- `rcon_ops._connect` imports it inside the function, since `rcon_manager` imports `rcon_ops` at module level.

## One-shot calls

- berconpy has no overall deadline, so a login against a closed port waits forever. `rcon_ops.CONNECT_TIMEOUT` covers only the login: `_connect` runs `session.__aenter__()` through `asyncio.wait_for` and `__aexit__` in its own `finally`. Never go back to a plain `async with`. Tests: `tests/test_rcon_ops.py`.
- On a stopped local server `_connect` raises `ServerStopped` (an `RconError`) before it connects, and the Players routes answer 409 (`players._rcon_http_error`). The check is skipped when `is_remote_rcon` is true.
- A one-shot client removes `client.cache.on_login`, berconpy's automatic `players` fetch, which fails once the connection closes. `test_berconpy_still_registers_the_listener_we_remove` notices if berconpy changes how it registers it.

## Dropped connections

- A dropped connection reaches our coroutine as `CancelledError`, which `except Exception` does not catch. `rcon_ops.is_foreign_cancel()` tells it from a real cancel, and `CONNECTION_LOST` is the shared message.
- `RconManager._command` wraps every berconpy call. Never hand-roll `try/except Exception` around one. A real cancellation must still propagate. Test: `tests/test_web_players.py::test_summaries_survive_a_connection_dropped_mid_command`.
- `is_connected()` follows berconpy's run task, not a timer. `_run_connection` waits on that task and clears `_connected` when it ends and in its `finally`.
- `_connected.set()` comes before the first `_initial_sync()`, because the sync waits on that flag through `_ensure_connected`.

## Session log

- When a logged-in connection ends, `RconManager._close_open_sessions` writes a `disconnect` with `"reason": "connection_ended"` per known player. After a connection's first sync, `_open_resynced_sessions` writes a `connect` with `"reason": "resync"`.
- An event with a `reason` skips `on_event`: no toast, no WS event and no Discord post. A panel restart writes a disconnect and connect pair per online player, on purpose.
- `player_ops.compute_sessions` caps a session that only a later connect closed at `MAX_UNCLOSED` and marks it `capped`.

## Messages for the Activity drawer

- `on_message` gets BattlEye's non-chat messages unclassified and the connection state once per change (`RconManager._status`). The panel sends them as `rcon_message`, never `rcon_event`, which reloads the Players views. Tests: `tests/test_rcon_manager.py`.

## Host and credentials

- The host is `rcon_ops.rcon_host(config)`, default `127.0.0.1`, never a literal. `check_rcon_setup` shows the remote skip: it returns after its two Zedstead-side checks.
- The `rcon_host` setting text says BattlEye RCon is unencrypted UDP and that a VPN or SSH tunnel reaches a remote host. Keep that when you shorten it.
- Outside Docker, `rcon_password` and `rcon_port` are set only by a save of the BattlEye cfg (its editor, the Raw editor or an upload), through `rcon_ops.adopt_beserver_rcon`. Docker sets them from `RCON_PASSWORD`. There is no CLI setter.
- `/api/status` carries `rcon_remote`, so the Players page greys out Send only when the backend refuses it.
