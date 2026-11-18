"""Ingress trust, admin-only writes, CSRF, body limit, headers, packaging and the n8n key never leaking."""

import ipaddress
import pathlib
import re
import subprocess

import aiohttp
import pytest
import yaml
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from bridge.app import api, server as srv
from bridge.app.config import Config, load_config
from conftest import ADMIN, KEY, ROOT, TOKEN, W, cid, make_config, wait_for


# ---- ingress trust -----------------------------------------------------------------------------------------------------
async def test_other_peer_refused_even_with_spoofed_headers(bridge):
    """A real TCP peer that is not the Supervisor or loopback (127.0.0.5 is on lo but not allowed) gets 403."""
    url = bridge.client.make_url("/api/state")
    connector = aiohttp.TCPConnector(local_addr=("127.0.0.5", 0))
    async with aiohttp.ClientSession(connector=connector) as s:
        spoof = {**W, "X-Forwarded-For": "172.30.32.2", "X-Real-IP": "172.30.32.2"}
        async with s.get(url, headers=spoof) as r:
            assert r.status == 403
            assert (await r.json())["code"] == "forbidden_peer"
        async with s.post(bridge.client.make_url("/api/threads"), json={"kind": "n8n", "target": "crew",
                                                                       "clientId": cid()}, headers=spoof) as r:
            assert r.status == 403
        async with s.get(bridge.client.make_url("/")) as r:
            assert r.status == 403
    assert await bridge.db.a("list_actions") == []


def _mocked(app, ip, headers):
    transport = type("T", (), {"get_extra_info": lambda self, k, d=None: (ip, 5555) if k == "peername" else d,
                               "is_closing": lambda self: False})()
    return make_mocked_request("GET", "/api/state", headers=headers, app=app, transport=transport)


@pytest.mark.parametrize("ip,trusted,allowed", [
    ("172.30.32.2", True, True),  # the Supervisor: headers trusted
    ("127.0.0.1", False, True),  # loopback outside dev mode: allowed, but anonymous
    ("::1", False, True),
    ("172.30.33.7", False, False),  # another app on the hassio network
    ("192.0.2.10", False, False),
])
async def test_user_headers_trusted_only_from_supervisor(tmp_path, ip, trusted, allowed):
    cfg = Config(data_dir=tmp_path)  # production defaults
    app = web.Application()
    app[srv.K_CFG] = cfg
    seen = {}

    async def handler(request):
        seen["user"] = request[srv.K_USER]
        return web.json_response({})

    req = _mocked(app, ip, ADMIN)
    resp = await srv.security_mw(req, handler)
    if not allowed:
        assert resp.status == 403 and "user" not in seen
        return
    assert resp.status == 200
    assert seen["user"].can_write is trusted
    assert (seen["user"].id == "u-alex") is trusted


async def test_ipv4_mapped_supervisor_is_recognised(tmp_path):
    app = web.Application()
    app[srv.K_CFG] = Config(data_dir=tmp_path)
    req = _mocked(app, "::ffff:172.30.32.2", ADMIN)
    assert srv.peer_ip(req) == ipaddress.ip_address("172.30.32.2")


def test_dev_mode_is_opt_in(monkeypatch, tmp_path):
    cfg = load_config({"BRIDGE_DATA": str(tmp_path)})
    assert ipaddress.ip_address("127.0.0.1") not in cfg.trust_user_from
    assert not cfg.dev
    cfg = load_config({"BRIDGE_DATA": str(tmp_path), "BRIDGE_DEV": "1"})
    assert ipaddress.ip_address("127.0.0.1") in cfg.trust_user_from


async def test_loopback_without_dev_is_read_only(bridge_factory, fake_n8n, tmp_path):
    b = await bridge_factory(n8n=fake_n8n, trust_loopback=False)
    r, data = await b.get("/api/state")
    assert r.status == 200 and data["user"]["canWrite"] is False and data["user"]["id"] is None
    r, data = await b.post("/api/threads", {"kind": "n8n", "target": "crew", "clientId": cid()})
    assert r.status == 403 and data["code"] == "not_admin"


# ---- admin-only writes, CSRF, limits -------------------------------------------------------------------------------
async def test_writes_need_admin_user(bridge):
    body = {"kind": "n8n", "target": "crew", "clientId": cid()}
    r, d = await bridge.post("/api/threads", body, headers={"X-Bridge": "1"})
    assert r.status == 403 and d["code"] == "not_admin" and "Home Assistant" in d["error"]
    non_admin = {**ADMIN, "X-Remote-User-Groups": "system-users", "X-Bridge": "1"}
    r, d = await bridge.post("/api/threads", body, headers=non_admin)
    assert r.status == 403 and d["code"] == "not_admin"
    r, d = await bridge.get("/api/state", headers={**ADMIN, "X-Remote-User-Groups": "system-users"})
    assert r.status == 200 and d["user"]["canWrite"] is False
    r, d = await bridge.post("/api/threads", body)
    assert r.status == 201


async def test_csrf_header_required(bridge):
    r, d = await bridge.post("/api/threads", {"kind": "n8n", "target": "crew", "clientId": cid()}, headers=ADMIN)
    assert r.status == 403 and d["code"] == "csrf"


async def test_client_id_required(bridge):
    r, d = await bridge.post("/api/threads", {"kind": "n8n", "target": "crew"})
    assert r.status == 400 and d["code"] == "client_id"


async def test_body_limit_64k(bridge):
    big = {"kind": "n8n", "target": "crew", "clientId": cid(), "pad": "x" * (64 * 1024)}
    r, d = await bridge.post("/api/threads", big)
    assert r.status == 413 and d["code"] == "too_large"


async def test_bad_json(bridge):
    r = await bridge.req("POST", "/api/threads", data=b"{nope", headers=W)
    assert r.status == 400 and (await r.json())["code"] == "bad_json"


async def test_security_headers_and_errors_are_json(bridge):
    r = await bridge.req("GET", "/api/state", headers=ADMIN)
    assert r.headers["Content-Security-Policy"] == srv.CSP
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["Referrer-Policy"] == "no-referrer"
    assert r.headers["Cache-Control"] == "no-cache"
    r, d = await bridge.get("/api/nope")
    assert r.status == 404 and d == {"error": "Not found", "code": "not_found"}


async def test_static_no_traversal_no_redirect(bridge_factory, tmp_path):
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text('<link href="app.css?v={{version}}">')
    (static / "app.css").write_text("body{}")
    (tmp_path / "secret.txt").write_text("nope")
    b = await bridge_factory(static_dir=static)
    r = await b.req("GET", "/")
    assert r.status == 200 and f"app.css?v={api.APP_VERSION}" in await r.text()
    r = await b.req("GET", "/app.css?v=0.1.3")
    assert r.status == 200 and "max-age=604800" in r.headers["Cache-Control"]
    for p in ("/../secret.txt", "/%2e%2e/secret.txt", "/static/../../secret.txt"):
        r = await b.req("GET", p, allow_redirects=False)
        assert r.status == 404, p
    r = await b.req("GET", "/api", allow_redirects=False)
    assert r.status not in (301, 302, 307, 308)


def test_static_files_have_no_absolute_urls():
    static = ROOT / "bridge" / "app" / "static"
    if not static.is_dir():
        pytest.skip("static/ is built by the frontend worker")
    bad = re.compile(r"""(?:src|href|action)\s*=\s*["']/(?!/)|fetch\(\s*["']/|EventSource\(\s*["']/|https?://""")
    for f in static.rglob("*"):
        if f.suffix in (".html", ".js", ".css"):
            for m in bad.finditer(f.read_text(errors="replace")):
                line = f.read_text(errors="replace")[:m.start()].count("\n") + 1
                ctx = f.read_text(errors="replace").splitlines()[line - 1]
                assert "www.w3.org" in ctx, f"{f.name}:{line}: absolute URL: {ctx.strip()[:120]}"


# ---- packaging -----------------------------------------------------------------------------------------------------
def test_config_yaml():
    cfg = yaml.safe_load((ROOT / "bridge" / "config.yaml").read_text())
    assert cfg["version"] == api.APP_VERSION
    assert cfg["name"] == "The Bridge" and cfg["slug"] == "the_bridge"
    assert cfg["ingress"] is True and cfg["ingress_port"] == 8099
    assert cfg["panel_admin"] is True and cfg["panel_icon"] == "mdi:rocket-launch-outline" and cfg["panel_title"] == "The Bridge"
    assert cfg["homeassistant_api"] is True
    assert cfg["backup"] == "hot" and cfg["backup_pre"] == "python3 /app/backup.py"
    assert set(cfg["backup_exclude"]) == {"bridge.db", "bridge.db-wal", "bridge.db-shm"}
    for forbidden in ("ports", "hassio_api", "map", "host_network"):
        assert forbidden not in cfg
    assert cfg["options"]["n8n_url"] is None  # required, no default: no LAN address in this public repo
    assert cfg["schema"]["n8n_url"] == "url"
    assert cfg["schema"]["n8n_key"] == "password?"
    assert "n8n_key" not in cfg["options"]
    changelog = (ROOT / "bridge" / "CHANGELOG.md").read_text()
    assert f"## {api.APP_VERSION}" in changelog


def test_no_lan_addresses_in_the_addon():
    ip = re.compile(r"\b(?:192\.168|10\.\d+)\.\d+\.\d+\b")
    for f in list((ROOT / "bridge").rglob("*")) + [ROOT / "repository.yaml"]:
        if f.is_file() and f.suffix in (".py", ".yaml", ".md", ".sh", "") and "static" not in f.parts:
            assert not ip.search(f.read_text(errors="replace")), f


def test_no_stray_app_config_files():
    """The Supervisor globs **/config.{json,yaml,yml}: bridge/config.yaml must be the only one (git-tracked or new)."""
    out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=ROOT,
                         capture_output=True, text=True, check=True).stdout.split()
    hits = [p for p in out if re.search(r"(^|/)config\.(json|ya?ml)$", p) and not any(
        part.startswith(".") or part == "rootfs" for part in pathlib.PurePosixPath(p).parts)]
    assert hits == ["bridge/config.yaml"]


def test_repository_yaml():
    repo = yaml.safe_load((ROOT / "repository.yaml").read_text())
    assert repo == {"name": "The Bridge", "url": "https://github.com/Starkkaman/Jam-Bridge", "maintainer": "Starkkaman"}


def test_run_sh_and_dockerfile():
    run = (ROOT / "bridge" / "run.sh").read_bytes()
    assert b"\r" not in run and run.startswith(b"#!/command/with-contenv bashio\n")
    assert b"-m app" in run and b"backup.py --restore" in run
    docker = (ROOT / "bridge" / "Dockerfile").read_text()
    assert "ARG BUILD_FROM=ghcr.io/home-assistant/base-python:3.14-alpine3.24" in docker
    assert "aiohttp==3.14.5" in docker
    assert "aiohttp==3.14.5" in (ROOT / "tests" / "bridge" / "requirements.txt").read_text()


# ---- the n8n key never leaks -----------------------------------------------------------------------------------------
async def test_key_sent_to_n8n_but_never_leaks(bridge, fake_n8n, log_capture):
    fake_n8n.chat_reply = "fine"
    await bridge.jobs.pollers["status"].tick()
    await bridge.jobs.pollers["tickets"].tick()
    r, d = await bridge.post("/api/threads", {"kind": "n8n", "target": "crew", "clientId": cid()})
    tid = d["thread"]["id"]
    r, d = await bridge.post(f"/api/threads/{tid}/messages", {"text": "hello", "clientId": cid()})
    await wait_for(lambda: bridge.db.get_message(d["message"]["id"])["state"] == "replied")
    fake_n8n.fail["agent-hub/status"] = 401
    await bridge.jobs.pollers["status"].tick()
    for path in ("/api/state", "/api/overview", "/api/crew", "/api/tasks", "/api/library", "/api/tickets",
                 "/api/inbox", "/api/threads", f"/api/threads/{tid}/messages", "/api/settings"):
        r, _ = await bridge.get(path)
        assert r.status == 200, path
    r, d = await bridge.get("/api/state")
    assert d["health"]["n8n"]["setup"] == "n8n key missing or wrong"
    # every n8n call carried the key
    assert fake_n8n.requests and all(k == KEY for k in fake_n8n.keys_seen())
    # ... and it is nowhere else: responses, SSE log, logs, the database file
    blob = "\n".join(bridge.responses) + repr(bridge.hub.published) + log_capture.getvalue()
    assert KEY not in blob and TOKEN not in blob
    for f in (bridge.db.path.parent).glob("bridge.db*"):
        assert KEY.encode() not in f.read_bytes() and TOKEN.encode() not in f.read_bytes()
    assert KEY not in repr(make_config(pathlib.Path("/tmp")))


def test_redact_filter_masks_secrets(log_capture):
    import logging

    logging.getLogger("bridge.test").warning("oops %s", KEY)
    assert KEY not in log_capture.getvalue() and "***" in log_capture.getvalue()
