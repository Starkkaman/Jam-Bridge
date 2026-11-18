"""Fixtures for The Bridge server tests: a fake n8n, a fake Core websocket and the server, all on 127.0.0.1, with a
fast clock (logical seconds x SCALE)."""

import asyncio
import io
import logging
import pathlib
import sys
import uuid

import pytest
from aiohttp.test_utils import TestClient, TestServer

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for p in (str(ROOT), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

from bridge.app import server as srv  # noqa: E402
from bridge.app.clock import Clock  # noqa: E402
from bridge.app.config import Config, RedactFilter, parse_ips  # noqa: E402
from fake_core_ws import FakeCore  # noqa: E402
from fake_n8n import FakeN8n  # noqa: E402

SCALE = 0.01  # 120 logical s = 1.2 real s
KEY = "test-bridge-key-7f3a9c"
TOKEN = "test-supervisor-token-91b2"
ADMIN = {"X-Remote-User-Id": "u-alex", "X-Remote-User-Name": "alex", "X-Remote-User-Display-Name": "Alex",
         "X-Remote-User-Groups": "system-users,system-admin"}
W = {**ADMIN, "X-Bridge": "1"}


def cid():
    return str(uuid.uuid4())


async def wait_for(pred, timeout=5.0, step=0.01):
    """Polls an (async or sync) predicate in real seconds."""
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while True:
        v = pred()
        if asyncio.iscoroutine(v):
            v = await v
        if v:
            return v
        if loop.time() > end:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(step)


@pytest.fixture
def log_capture():
    """Everything logged during the test, through the same redaction filter the server installs."""
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    handler.addFilter(RedactFilter([KEY, TOKEN]))
    root = logging.getLogger()
    old = root.level
    root.setLevel(logging.DEBUG)
    root.addHandler(handler)
    yield buf
    root.removeHandler(handler)
    root.setLevel(old)


@pytest.fixture
async def fake_n8n():
    fake = FakeN8n(scale=SCALE)
    server = TestServer(fake.app(), host="127.0.0.1")
    await server.start_server()
    fake.url = str(server.make_url("")).rstrip("/")
    yield fake
    await server.close()


@pytest.fixture
async def fake_core(monkeypatch):
    monkeypatch.setenv("SUPERVISOR_TOKEN", TOKEN)
    fake = FakeCore(token=TOKEN, scale=SCALE)
    server = TestServer(fake.app(), host="127.0.0.1")
    await server.start_server()
    fake.url = str(server.make_url("/core/websocket")).replace("http://", "ws://")
    fake.server = server
    yield fake
    await server.close()


class Bridge:
    """The server under test plus handles to its parts."""

    def __init__(self, client, app):
        self.client = client
        self.app = app
        self.db = app[srv.K_DB]
        self.jobs = app[srv.K_JOBS]
        self.hub = app[srv.K_HUB]
        self.crew = app[srv.K_CREW]
        self.n8n = app[srv.K_N8N]
        self.clock = app[srv.K_CLOCK]
        self.responses = []  # every response body text, for the no-leak check

    async def req(self, method, path, **kw):
        resp = await self.client.request(method, path, **kw)
        text = await resp.text()
        self.responses.append(text + "\n" + "\n".join(f"{k}: {v}" for k, v in resp.headers.items()))
        return resp

    async def get(self, path, headers=None, **kw):
        resp = await self.req("GET", path, headers=ADMIN if headers is None else headers, **kw)
        return resp, (await resp.json() if resp.content_type == "application/json" else None)

    async def post(self, path, body=None, headers=None, method="POST"):
        resp = await self.req(method, path, json=body or {}, headers=W if headers is None else headers)
        return resp, (await resp.json() if resp.content_type == "application/json" else None)


def make_config(tmp_path, n8n_url=None, core_url="ws://127.0.0.1:9/core/websocket", key=KEY, trust_loopback=True):
    cfg = Config(data_dir=tmp_path / "data", n8n_url=n8n_url, n8n_key=key, core_ws_url=core_url, time_scale=SCALE)
    if trust_loopback:  # tests talk from 127.0.0.1 and set the Supervisor's headers themselves
        cfg.trust_user_from = parse_ips(["172.30.32.2", "127.0.0.1"])
    if not n8n_url:
        cfg.config_error = "n8n_url is not set"
    return cfg


@pytest.fixture
async def bridge_factory(tmp_path):
    made = []

    async def make(n8n=None, core=None, pollers=False, connect_crew=None, static_dir=None, **cfg_kw):
        cfg = make_config(tmp_path, n8n_url=n8n.url if n8n else None,
                          core_url=core.url if core else "ws://127.0.0.1:9/core/websocket", **cfg_kw)
        app = srv.build_app(cfg, clock=Clock(SCALE), pollers=pollers,
                            connect_crew=bool(core) if connect_crew is None else connect_crew, static_dir=static_dir)
        client = TestClient(TestServer(app, host="127.0.0.1"))
        await client.start_server()
        b = Bridge(client, app)
        made.append(b)
        if core is not None and (connect_crew is None or connect_crew):
            await wait_for(lambda: b.crew.connected and b.crew.machines)
        return b

    yield make
    for b in made:
        await b.client.close()


@pytest.fixture
async def bridge(bridge_factory, fake_n8n, fake_core):
    return await bridge_factory(n8n=fake_n8n, core=fake_core)
