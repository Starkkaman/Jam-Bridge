"""Local dev run for The Bridge: the real server plus the fake n8n and the fake Core websocket, all on 127.0.0.1.

    /home/user/.local/opt/bridge-test-venv/bin/python tests/bridge/dev_server.py [--port 8099] [--data DIR] [--scale 1]

- http://127.0.0.1:8099/                                the server directly (dev mode: requests without
                                                         X-Remote-User-* act as the admin "Alex")
- http://127.0.0.1:8100/api/hassio_ingress/dev-token/    the same through a mini ingress proxy (prefix stripped,
                                                         X-Remote-User-* injected, SSE streamed): checks relative URLs
- fake n8n on :8098 (fixtures from tests/bridge/fixtures/n8n, plus the invented Engineer tickets in
  tickets-engineer.json), fake Core websocket on :8097

See bridge/DEV.md. Nothing here is used by the add-on image.
"""

import argparse
import asyncio
import json
import os
import pathlib
import sys
import tempfile

import aiohttp
from aiohttp import web

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path[:0] = [str(ROOT), str(HERE)]

from fake_core_ws import FakeCore  # noqa: E402
from fake_n8n import FakeN8n  # noqa: E402

DEV_KEY = "dev-bridge-key"
DEV_TOKEN = "dev-token"
INGRESS_USER = {"X-Remote-User-Id": "dev-user", "X-Remote-User-Name": "alex",
                "X-Remote-User-Display-Name": "Alex", "X-Remote-User-Groups": "system-users,system-admin"}
HOP = {"connection", "keep-alive", "transfer-encoding", "te", "upgrade", "proxy-authorization", "trailer", "host",
       "content-length"}


def ingress_proxy(upstream):
    """Strips /api/hassio_ingress/<token>/, drops client X-Remote-User-* and sets its own (like the Supervisor)."""
    session_box = {}

    async def handle(request):
        if "s" not in session_box:
            session_box["s"] = aiohttp.ClientSession(auto_decompress=False)
        session = session_box["s"]
        tail = request.match_info["tail"]
        headers = {k: v for k, v in request.headers.items()
                   if k.lower() not in HOP and not k.lower().startswith("x-remote-user-")}
        headers.update(INGRESS_USER)
        url = f"{upstream}/{tail}" + (f"?{request.query_string}" if request.query_string else "")
        async with session.request(request.method, url, headers=headers, data=await request.read(),
                                   allow_redirects=False) as up:
            resp = web.StreamResponse(status=up.status, headers={k: v for k, v in up.headers.items()
                                                                 if k.lower() not in HOP})
            await resp.prepare(request)
            async for chunk in up.content.iter_any():
                await resp.write(chunk)
            return resp

    app = web.Application()
    app.router.add_route("*", "/api/hassio_ingress/{token}/{tail:.*}", handle)

    async def close(app):
        if "s" in session_box:
            await session_box["s"].close()

    app.on_cleanup.append(close)
    return app


async def serve(app, port):
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    return runner


async def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8099)
    ap.add_argument("--data", help="data folder (default: a new temp folder)")
    ap.add_argument("--scale", type=float, default=1.0, help="time scale (0.1 = ten times faster)")
    ap.add_argument("--require-key", action="store_true", help="fake n8n answers 401 without the dev key")
    a = ap.parse_args()
    data = pathlib.Path(a.data or tempfile.mkdtemp(prefix="bridge-dev-"))
    data.mkdir(parents=True, exist_ok=True)
    n8n_port, core_port, proxy_port = a.port - 1, a.port - 2, a.port + 1

    fake_n8n = FakeN8n(scale=a.scale, require_key=DEV_KEY if a.require_key else None)
    fake_n8n.tickets[:0] = json.loads((HERE / "fixtures" / "n8n" / "tickets-engineer.json").read_text())
    fake_core = FakeCore(token=DEV_TOKEN, scale=a.scale)
    os.environ.update({
        "BRIDGE_DEV": "1", "BRIDGE_DATA": str(data), "BRIDGE_OPTIONS": str(data / "options.json"),
        "BRIDGE_N8N_URL": f"http://127.0.0.1:{n8n_port}", "BRIDGE_N8N_KEY": DEV_KEY,
        "BRIDGE_CORE_WS": f"ws://127.0.0.1:{core_port}/core/websocket", "SUPERVISOR_TOKEN": DEV_TOKEN,
        "BRIDGE_TIME_SCALE": str(a.scale), "BRIDGE_LOG_LEVEL": "info",
    })
    from bridge.app import server
    from bridge.app.config import load_config, setup_logging

    cfg = load_config()
    setup_logging(cfg)
    runners = [await serve(fake_n8n.app(), n8n_port), await serve(fake_core.app(), core_port)]
    runners.append(await serve(server.build_app(cfg), a.port))
    runners.append(await serve(ingress_proxy(f"http://127.0.0.1:{a.port}"), proxy_port))
    print(f"The Bridge (dev): http://127.0.0.1:{a.port}/  |  via ingress proxy: "
          f"http://127.0.0.1:{proxy_port}/api/hassio_ingress/dev-token/  |  data: {data}", flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        for r in reversed(runners):
            await r.cleanup()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
