"""The Bridge web server (aiohttp): static UI, api/* JSON, api/events SSE.

Ingress only: the listener accepts the Supervisor's ingress proxy (172.30.32.2) and loopback; everyone else gets 403.
`request.remote` is the TCP peer; X-Forwarded-For is never read. X-Remote-User-* are trusted only from the Supervisor
(and from loopback in dev mode). Relative URLs only: no redirects, no absolute links.
"""

import argparse
import asyncio
import contextlib
import ipaddress
import json
import logging
import os
import pathlib

from aiohttp import web

from . import api
from .clock import Clock
from .config import load_config, setup_logging
from .conversation import Conversations
from .crew import Crew
from .db import DB
from .jobs import Jobs
from .n8n import N8n
from .opencode import OpenCode

LOG = logging.getLogger("bridge")
HERE = pathlib.Path(__file__).resolve().parent
STATIC = HERE / "static"
MAX_BODY = 64 * 1024
PING_EVERY = 20
SSE_QUEUE = 1000

CSP = "default-src 'self'; img-src 'self' data:; frame-ancestors 'self'"
TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".js": "text/javascript; charset=utf-8", ".json": "application/json; charset=utf-8",
         ".svg": "image/svg+xml", ".png": "image/png", ".woff2": "font/woff2", ".woff": "font/woff",
         ".ttf": "font/ttf", ".txt": "text/plain; charset=utf-8", ".ico": "image/x-icon"}

K_CFG = web.AppKey("cfg", object)
K_DB = web.AppKey("db", object)
K_N8N = web.AppKey("n8n", object)
K_CREW = web.AppKey("crew", object)
K_JOBS = web.AppKey("jobs", object)
K_CONV = web.AppKey("conversations", object)
K_OC = web.AppKey("opencode", object)
K_HUB = web.AppKey("hub", object)
K_CLOCK = web.AppKey("clock", object)
K_STATIC = web.AppKey("static", object)
K_SWEEP = web.AppKey("conv_sweep", object)
K_USER = web.RequestKey("user", object)


# ---- users ------------------------------------------------------------------------------------------------------------
class User:
    __slots__ = ("id", "name", "display", "groups")

    def __init__(self, uid=None, name=None, display=None, groups=()):
        self.id = uid
        self.name = name
        self.display = display or name or "Someone"
        self.groups = frozenset(groups)

    @property
    def can_write(self):
        return bool(self.id) and "system-admin" in self.groups

    def as_dict(self):
        return {"id": self.id, "name": self.name, "display": self.display if self.id else None,
                "canWrite": self.can_write}


ANON = User()
DEV_USER = User("dev-user", "alex", "Alex", ("system-admin",))


def _hdr(request, name, n):
    v = request.headers.get(name)
    if not v:
        return None
    v = "".join(ch for ch in v if ch.isprintable())
    return v.strip()[:n] or None


def user_from_headers(request):
    uid = _hdr(request, "X-Remote-User-Id", 64)
    if not uid:
        return ANON
    groups = [g.strip() for g in (_hdr(request, "X-Remote-User-Groups", 1000) or "").split(",") if g.strip()]
    return User(uid, _hdr(request, "X-Remote-User-Name", 80), _hdr(request, "X-Remote-User-Display-Name", 80), groups)


def peer_ip(request):
    try:
        ip = ipaddress.ip_address(request.remote or "")
    except ValueError:
        return None
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip


# ---- SSE hub ----------------------------------------------------------------------------------------------------------
class Hub:
    def __init__(self):
        self.subs = set()
        self.on_visibility = None
        self.published = []  # last events, for tests and debugging (bounded)

    @property
    def visible(self):
        return bool(self.subs)

    def add(self):
        q = asyncio.Queue(maxsize=SSE_QUEUE)
        was = self.visible
        self.subs.add(q)
        if not was and self.on_visibility:
            self.on_visibility(True)
        return q

    def remove(self, q):
        self.subs.discard(q)
        if not self.subs and self.on_visibility:
            self.on_visibility(False)

    def publish(self, event, data):
        payload = json.dumps(data, separators=(",", ":"))
        self.published.append((event, data))
        del self.published[:-200]
        for q in list(self.subs):
            try:
                q.put_nowait((event, payload))
            except asyncio.QueueFull:  # a stuck reader: close it; the page reconnects and refetches on `hello`
                with contextlib.suppress(asyncio.QueueFull):
                    q.get_nowait()
                    q.put_nowait(None)


# ---- middlewares ------------------------------------------------------------------------------------------------------
def json_error(status, code, message, extra=None):
    return web.json_response({**(extra or {}), "error": message, "code": code}, status=status)


@web.middleware
async def security_mw(request, handler):
    cfg = request.app[K_CFG]
    ip = peer_ip(request)
    if ip is None or ip not in cfg.allow:
        resp = json_error(403, "forbidden_peer", "Forbidden")
    else:
        if ip in cfg.trust_user_from:
            user = user_from_headers(request)
            if cfg.dev and user is ANON:
                user = DEV_USER
        else:
            user = ANON  # headers from anyone but the Supervisor are ignored
        request[K_USER] = user
        try:
            resp = await handler(request)
        except web.HTTPRequestEntityTooLarge:
            resp = json_error(413, "too_large", "Request body is larger than 64 KB")
        except web.HTTPException as exc:
            if exc.status >= 300 and exc.status < 400:  # never redirect (absolute URLs break under ingress)
                resp = json_error(404, "not_found", "Not found")
            else:
                resp = json_error(exc.status, exc.reason.lower().replace(" ", "_"), exc.reason)
        except api.ApiError as exc:
            resp = json_error(exc.status, exc.code, exc.message, exc.extra)
        except Exception:  # noqa: BLE001
            LOG.exception("unhandled error on %s %s", request.method, request.path)
            resp = json_error(500, "internal", "Internal error")
    h = resp.headers
    h.setdefault("Content-Security-Policy", CSP)
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("Referrer-Policy", "no-referrer")
    h.setdefault("Cache-Control", "no-cache")
    return resp


# ---- static -----------------------------------------------------------------------------------------------------------
async def static_handler(request):
    root = request.app[K_STATIC]
    name = request.match_info.get("path") or "index.html"
    if name in ("", "/"):
        name = "index.html"
    if not root.is_dir():
        if name == "index.html":
            return web.Response(text="The Bridge server is running; the page is not built yet.",
                                content_type="text/plain")
        raise web.HTTPNotFound()
    try:
        path = (root / name).resolve()
        path.relative_to(root.resolve())
    except (ValueError, OSError):
        raise web.HTTPNotFound() from None
    if not path.is_file():
        raise web.HTTPNotFound()
    ctype = TYPES.get(path.suffix.lower(), "application/octet-stream")
    body = path.read_bytes()
    if path.name == "index.html":
        body = body.replace(b"{{version}}", api.APP_VERSION.encode())
    resp = web.Response(body=body, headers={"Content-Type": ctype})
    if request.query.get("v") and path.name != "index.html":
        resp.headers["Cache-Control"] = "public, max-age=604800"
    return resp


# ---- SSE --------------------------------------------------------------------------------------------------------------
async def events_handler(request):
    hub = request.app[K_HUB]
    clock = request.app[K_CLOCK]
    resp = web.StreamResponse(headers={"Content-Type": "text/event-stream; charset=utf-8", "Cache-Control": "no-cache",
                                       "X-Accel-Buffering": "no", "Content-Security-Policy": CSP,
                                       "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"})
    await resp.prepare(request)
    q = hub.add()
    try:
        hello = {"version": api.APP_VERSION, "user": request[K_USER].as_dict(), "serverTime": clock.now_ms()}
        await resp.write(sse("hello", json.dumps(hello)))
        while True:
            try:
                item = await asyncio.wait_for(q.get(), clock.secs(PING_EVERY))
            except TimeoutError:
                await resp.write(b": ping\n\n")
                continue
            if item is None:
                break
            await resp.write(sse(*item))
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    finally:
        hub.remove(q)
    return resp


def sse(event, payload):
    return f"event: {event}\ndata: {payload}\n\n".encode()


# ---- app --------------------------------------------------------------------------------------------------------------
def build_app(cfg, clock=None, pollers=True, connect_crew=True, static_dir=None, crew=None):
    """Tests turn the pollers or the crew_hub connection off to drive them by hand."""
    clock = clock or Clock(cfg.time_scale)
    app = web.Application(middlewares=[security_mw], client_max_size=MAX_BODY)
    app[K_CFG] = cfg
    app[K_CLOCK] = clock
    app[K_STATIC] = pathlib.Path(static_dir) if static_dir else STATIC
    hub = Hub()
    app[K_HUB] = hub
    db = DB(cfg.db_path, clock)
    app[K_DB] = db
    n8n = N8n(cfg, clock)
    app[K_N8N] = n8n
    crew = crew or Crew(cfg.core_ws_url, clock)
    app[K_CREW] = crew
    jobs = Jobs(db, n8n, crew, hub, clock)
    app[K_JOBS] = jobs
    conv = Conversations(crew, clock)  # Claude conversations: process memory only, <= 60 s, never in SQLite
    app[K_CONV] = conv
    oc = OpenCode(n8n, clock)  # OpenCode agents and sessions: process memory only, never in SQLite
    app[K_OC] = oc

    def on_visibility(visible):
        if not visible:  # the last page went away (SSE unsubscribe): forget every conversation now
            conv.clear()
            oc.clear()
        jobs.on_visibility(visible)

    hub.on_visibility = on_visibility
    n8n.on_health = lambda h: hub.publish("health", {"n8n": api.n8n_health(n8n)})
    crew.on_full = lambda m: (hub.publish("crew", {"machines": m, "connected": crew.connected}), jobs.on_crew_full(m))
    crew.on_machine = lambda mid, d: (hub.publish("crew.machine", {"id": mid, "data": d}),
                                      jobs.on_crew_machine(mid, d))
    crew.on_connection = lambda c: hub.publish("health", {"crew": {"connected": c, "at": crew.connected_at}})

    async def on_startup(app):
        await db.a("open")
        await n8n.start()
        if connect_crew:
            crew.start()
        await jobs.start(pollers=pollers)
        app[K_SWEEP] = asyncio.ensure_future(conv.sweep_loop())

    async def on_cleanup(app):
        app[K_SWEEP].cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await app[K_SWEEP]
        conv.clear()
        oc.clear()
        await jobs.stop()
        await crew.stop()
        await n8n.close()
        try:
            await db.a("snapshot", cfg.data_dir / "snapshot" / "bridge.db")
        except Exception:  # noqa: BLE001
            LOG.exception("snapshot on stop failed")
        db.close()

    async def on_shutdown(app):
        for q in list(hub.subs):
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(None)

    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    app.on_cleanup.append(on_cleanup)

    api.register(app)
    app.router.add_get("/api/events", events_handler)
    app.router.add_route("*", "/api/{tail:.*}", api.not_found)
    app.router.add_get("/", static_handler)
    app.router.add_get("/{path:.+}", static_handler)
    return app


def main(argv=None):
    parser = argparse.ArgumentParser(description="The Bridge server")
    parser.add_argument("--host", default=os.environ.get("BRIDGE_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("BRIDGE_PORT", "8099")))
    args = parser.parse_args(argv)
    cfg = load_config()
    setup_logging(cfg)
    LOG.info("The Bridge %s starting (n8n %s, key %s, dev %s)", api.APP_VERSION,
             "configured" if cfg.n8n_url else f"NOT configured: {cfg.config_error}",
             "set" if cfg.n8n_key else "not set", cfg.dev)
    app = build_app(cfg)
    LOG.info("listening on %s:%s (ingress only)", args.host, args.port)
    web.run_app(app, host=args.host, port=args.port, access_log=None, print=None,
                handle_signals=True, shutdown_timeout=5)

