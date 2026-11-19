"""n8n adapter: an allow-list of (method, path) pairs under `n8n_url`/webhook/, nothing else.

- `X-Bridge-Key` (the `n8n_key` option) is added to every call when set. It is never logged, stored or returned;
  401/403 become the setup banner "n8n key missing or wrong".
- No redirects, no environment proxies, bodies <= 1 MB (agent-hub/status 2 MB). Timeouts: GETs 15 s, chat 120 s,
  ticket POSTs 20 s, apply 900 s (logical seconds; the clock scales them in tests).
- The first response after a start is logged once with its HTTP status only (`n8n.firstContact`).
"""

import asyncio
import json
import logging

import aiohttp

LOG = logging.getLogger("bridge.n8n")

ALLOWED = frozenset({
    ("GET", "agent-hub/status"),
    ("GET", "bridge/tickets"),
    ("GET", "bridge/inbox"),
    ("GET", "bridge/library"),
    ("GET", "bridge/library/precheck"),
    ("GET", "bridge/runs"),
    ("GET", "bridge/run"),
    ("GET", "bridge/agent-output"),
    ("POST", "bridge/chat"),
    ("POST", "agent-hub/ticket"),
    ("POST", "bridge/ticket-update"),
    ("POST", "bridge/library/apply"),
    ("POST", "bridge/run-now"),
    ("POST", "bridge/library/scan"),
})
TIMEOUTS = {"bridge/chat": 120, "agent-hub/ticket": 20, "bridge/ticket-update": 20, "bridge/library/apply": 900, "bridge/run-now": 30,
            "bridge/library/scan": 30}
GET_TIMEOUT = 15
MAX_BODY = 1024 * 1024
MAX_BODY_STATUS = 2 * 1024 * 1024
KEY_ERROR = "n8n key missing or wrong"


class N8nError(Exception):
    """code: not_configured | not_allowed | timeout | unreachable | key | http | too_big | bad_json"""

    def __init__(self, code, message, status=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class N8n:
    def __init__(self, cfg, clock):
        self.base = cfg.n8n_url
        self._key = cfg.n8n_key
        self.config_error = cfg.config_error
        self.clock = clock
        self.session = None
        self.first_contact_logged = False
        self._inflight = {}
        # health: last outcome of any call
        self.health = {"ok": None, "at": None, "error": cfg.config_error, "code": "config" if cfg.config_error else None,
                       "since": None}
        self.on_health = None  # callback(health) when ok/error changes

    async def start(self):
        if self.session is None:
            self.session = aiohttp.ClientSession(trust_env=False, cookie_jar=aiohttp.DummyCookieJar())

    async def close(self):
        if self.session is not None:
            await self.session.close()
            self.session = None

    def url(self, path):
        return f"{self.base}/webhook/{path}"

    def _set_health(self, ok, error=None, code=None):
        now = self.clock.now_ms()
        prev = (self.health["ok"], self.health["error"])
        since = self.health.get("since")
        if ok:
            since = None
        elif self.health["ok"] is not False or since is None:
            since = now
        self.health = {"ok": ok, "at": now, "error": error, "code": code, "since": since}
        if (ok, error) != prev and self.on_health:
            try:
                self.on_health(dict(self.health))
            except Exception:  # noqa: BLE001
                LOG.exception("health callback failed")

    async def request(self, method, path, params=None, body=None):
        if (method, path) not in ALLOWED:
            raise N8nError("not_allowed", f"{method} {path} is not on the n8n allow-list")
        if not self.base:
            raise N8nError("not_configured", self.config_error or "n8n_url is not set")
        await self.start()
        limit = MAX_BODY_STATUS if path == "agent-hub/status" else MAX_BODY
        seconds = TIMEOUTS.get(path, GET_TIMEOUT)
        headers = {"Accept": "application/json"}
        if self._key:
            headers["X-Bridge-Key"] = self._key
        try:
            async with asyncio.timeout(self.clock.secs(seconds)):
                async with self.session.request(method, self.url(path), params=params, json=body, headers=headers,
                                                allow_redirects=False) as resp:
                    if not self.first_contact_logged:
                        self.first_contact_logged = True
                        LOG.info("n8n.firstContact HTTP %s", resp.status)
                    raw = await self._read_limited(resp, limit)
                    status = resp.status
        except TimeoutError:
            err = N8nError("timeout", f"n8n did not answer in {seconds} s")
            self._set_health(False, err.message, err.code)
            raise err from None
        except N8nError as err:
            self._set_health(False, err.message, err.code)
            raise
        except (aiohttp.ClientError, OSError) as exc:
            err = N8nError("unreachable", f"n8n unreachable ({type(exc).__name__})")
            self._set_health(False, err.message, err.code)
            raise err from None
        if status in (401, 403):
            err = N8nError("key", KEY_ERROR, status)
            self._set_health(False, err.message, err.code)
            raise err
        if not 200 <= status < 300:
            err = N8nError("http", f"n8n answered HTTP {status}", status)
            reason = self._error_text(raw)
            if reason and 400 <= status < 500:
                err.message = f"n8n: {reason}"
                if path in ("agent-hub/ticket", "bridge/ticket-update"):
                    # a refused ticket write (validation): n8n itself is fine
                    self._set_health(True)
                    raise err
            elif status >= 500 and path in ("agent-hub/ticket", "bridge/ticket-update"):
                # today's ticket workflows throw on bad input: n8n answers 500 "Error in workflow", reason only in its log
                err.message = "n8n refused the change (the reason is in n8n's execution log)"
            # an unknown id (agent-output, run), a workflow that can't re-run (500) or a scan already running (409):
            # n8n itself is fine
            if path in ("bridge/agent-output", "bridge/run", "bridge/run-now", "bridge/library/scan"):
                self._set_health(True)
            else:
                self._set_health(False, err.message, err.code)
            raise err
        try:
            data = json.loads(raw.decode("utf-8")) if raw.strip() else None
        except (ValueError, UnicodeDecodeError):
            err = N8nError("bad_json", "n8n sent something that is not JSON", status)
            self._set_health(False, err.message, err.code)
            raise err from None
        self._set_health(True)
        return data

    @staticmethod
    def _error_text(raw):
        """The reason in an n8n error body ({"error"|"message": str}), clipped; None if there isn't one."""
        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None
        if isinstance(data, dict):
            for k in ("error", "message"):
                if isinstance(data.get(k), str) and data[k].strip():
                    return data[k].strip()[:300]
        return None

    @staticmethod
    async def _read_limited(resp, limit):
        chunks, size = [], 0
        async for chunk in resp.content.iter_chunked(65536):
            size += len(chunk)
            if size > limit:
                raise N8nError("too_big", f"n8n response is larger than {limit // 1024} KB")
            chunks.append(chunk)
        return b"".join(chunks)

    async def shared(self, key, method, path, params=None):
        """One request in flight per key: concurrent callers share the same result."""
        fut = self._inflight.get(key)
        if fut is not None:
            return await asyncio.shield(fut)
        task = asyncio.ensure_future(self.request(method, path, params=params))
        self._inflight[key] = task
        task.add_done_callback(lambda t: (self._inflight.pop(key, None), t.cancelled() or t.exception()))
        return await asyncio.shield(task)

    def busy(self, key):
        return key in self._inflight

    # ---- endpoints ------------------------------------------------------------------------------------------------
    async def status(self):
        return await self.shared("status", "GET", "agent-hub/status")

    async def tickets(self):
        return await self.shared("tickets", "GET", "bridge/tickets")

    async def inbox(self):
        return await self.shared("inbox", "GET", "bridge/inbox")

    async def library(self):
        return await self.shared("library", "GET", "bridge/library")

    async def precheck(self):
        return await self.shared("precheck", "GET", "bridge/library/precheck")

    async def runs(self, workflow_id):
        return await self.shared(f"runs:{workflow_id}", "GET", "bridge/runs", {"workflowId": workflow_id})

    async def run(self, execution_id):
        return await self.shared(f"run:{execution_id}", "GET", "bridge/run", {"id": execution_id})

    async def agent_output(self, agent_id):
        return await self.shared(f"output:{agent_id}", "GET", "bridge/agent-output", {"id": agent_id})

    async def chat(self, body):
        return await self.request("POST", "bridge/chat", body=body)

    async def ticket(self, body):
        return await self.request("POST", "agent-hub/ticket", body=body)

    async def ticket_update(self, body):
        return await self.request("POST", "bridge/ticket-update", body=body)

    async def apply(self, body):
        return await self.request("POST", "bridge/library/apply", body=body)

    async def run_now(self, body):
        return await self.request("POST", "bridge/run-now", body=body)

    async def library_scan(self, body):
        return await self.request("POST", "bridge/library/scan", body=body)
