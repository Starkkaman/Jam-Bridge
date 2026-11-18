# The Bridge: local development

No Docker needed. The server is a plain aiohttp app (`bridge/app/`, package `bridge.app`); the test fakes stand in for
n8n and Home Assistant's Core websocket.

## Venv (once)

```
UV_LINK_MODE=copy uv venv --python 3.14 /home/user/.local/opt/bridge-test-venv
UV_LINK_MODE=copy uv pip install --python /home/user/.local/opt/bridge-test-venv/bin/python -r tests/bridge/requirements.txt
```

## Run everything on localhost

```
/home/user/.local/opt/bridge-test-venv/bin/python tests/bridge/dev_server.py
```

| Port | What |
|---|---|
| **8099** | The Bridge, `http://127.0.0.1:8099/` (dev mode: a request without `X-Remote-User-*` acts as the admin "Alex") |
| 8100 | Mini ingress proxy, `http://127.0.0.1:8100/api/hassio_ingress/dev-token/` (strips the prefix, injects the user headers like the Supervisor, streams SSE): use it to check that every URL is relative |
| 8098 | Fake n8n (`tests/bridge/fixtures/n8n/*.json`; chat echoes; library findings 14-25 start unapplied) |
| 8097 | Fake Core websocket (`card/fixtures/snapshot-sample.json`) |

Flags: `--port N` (the others follow: N-1, N-2, N+1), `--data DIR` (default: a new temp folder, so each run starts
empty), `--scale 0.1` (ten times faster clock), `--require-key` (fake n8n answers 401 without the dev key).

The static page is served from `bridge/app/static/`; `index.html` gets `{{version}}` replaced with the app version.

The server on its own (as in the container, where `run.sh` runs `python3 -m app` from `/`):

```
BRIDGE_DEV=1 BRIDGE_DATA=/tmp/bridge-data BRIDGE_N8N_URL=http://127.0.0.1:8098 \
BRIDGE_CORE_WS=ws://127.0.0.1:8097/core/websocket SUPERVISOR_TOKEN=dev-token \
/home/user/.local/opt/bridge-test-venv/bin/python -m bridge.app --host 127.0.0.1 --port 8099
```

Environment overrides (dev and tests only; the add-on reads `/data/options.json`): `BRIDGE_DATA`, `BRIDGE_OPTIONS`,
`BRIDGE_N8N_URL`, `BRIDGE_N8N_KEY`, `BRIDGE_CORE_WS`, `BRIDGE_DEV=1` (trust user headers from loopback, default user),
`BRIDGE_TIME_SCALE`, `BRIDGE_LOG_LEVEL`.

## Tests

```
/home/user/.local/opt/bridge-test-venv/bin/python -m pytest tests/bridge
```

They use a fast clock (logical seconds x 0.01): a 130 s chat times out at 120 s in about 1.2 s.
