"""OpenCode agents through n8n (docs/BRIDGE.md §14): shaped answers held in process memory only.

Nothing here touches SQLite. n8n's answers are shaped (types checked, text clipped) and kept in `OpenCode.cache` for
30 s (agents, an agent's sessions) or 15 s (one session), shared by every viewer. `fresh` asks n8n again unless the
copy is younger than FRESH_MIN seconds, so a Refresh can't hammer n8n. Entries older than KEEP seconds are swept, all
of them go when the last page disconnects (SSE unsubscribe) and on restart. Sends are limited to one per SEND_EVERY
seconds per HA user.
"""

import collections
import re

OC_ID_RE = re.compile(r"^oc:[\w.-]{1,80}$")
OC_SESSION_RE = re.compile(r"^[\w.:-]{1,128}$")
AGENTS_TTL = 30
SESSIONS_TTL = 30
SESSION_TTL = 15
FRESH_MIN = 5
KEEP = 120  # logical seconds an entry may stay in memory at all
SEND_EVERY = 10
TEXT_MAX = 4000
SESSIONS_LIMIT = 20
ORIGINS = ("bridge", "ticket", "opencode")
TOOL_STATUSES = ("done", "running", "pending", "error")
MAX_MESSAGES = 200
MAX_TOOLS = 500
BUSY_ERRORS = ("agent_busy", "session_busy")
NOT_FOUND_RE = re.compile(r"\bnot found\b", re.I)


def _text(value, limit):
    if not isinstance(value, str):
        return None
    value = "".join(ch for ch in value if ch in "\n\t" or ch.isprintable()).strip()
    if not value:
        return None
    return value if len(value) <= limit else value[:limit - 1] + "…"


def _at(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str) and 0 < len(value) <= 40:
        return value
    return None


def _int(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and 0 < value < 2 ** 53:
        return value
    if isinstance(value, str) and value.isdigit() and len(value) <= 15:
        return int(value)
    return None


def _session_id(value):
    return value if isinstance(value, str) and OC_SESSION_RE.match(value) else None


def shape_last(value):
    if not isinstance(value, dict) or not _session_id(value.get("id")):
        return None
    return {"id": value["id"], "title": _text(value.get("title"), 200), "updated": _at(value.get("updated"))}


def shape_agent(a):
    if not isinstance(a, dict) or not isinstance(a.get("id"), str) or not OC_ID_RE.match(a["id"]):
        return None
    return {"id": a["id"], "name": _text(a.get("name"), 120) or a["id"], "short_name": _text(a.get("short_name"), 60),
            "description": _text(a.get("description"), 600), "role": _text(a.get("role"), 40),
            "busy": a.get("busy") is True, "busySince": _at(a.get("busySince")),
            "needsApproval": a.get("needsApproval") is True, "lastSession": shape_last(a.get("lastSession"))}


def _origin(value):
    return value if value in ORIGINS else "opencode"  # unknown origins are read-only


def shape_brief(s):
    if not isinstance(s, dict) or not _session_id(s.get("id")):
        return None
    return {"id": s["id"], "title": _text(s.get("title"), 200), "updated": _at(s.get("updated")),
            "busy": s.get("busy") is True, "busySince": _at(s.get("busySince")),
            "needsApproval": s.get("needsApproval") is True, "origin": _origin(s.get("origin")),
            "ticketId": _int(s.get("ticketId"))}


def shape_session(d, sid):
    if not isinstance(d, dict):
        return None
    msgs = []
    for m in d.get("messages") if isinstance(d.get("messages"), list) else []:
        if isinstance(m, dict) and m.get("role") in ("user", "assistant"):
            msgs.append({"role": m["role"], "text": _text(m.get("text"), TEXT_MAX) or "", "at": _at(m.get("at"))})
    tools = []
    for t in d.get("tools") if isinstance(d.get("tools"), list) else []:
        if isinstance(t, dict) and _text(t.get("name"), 60):
            tools.append({"name": _text(t.get("name"), 60), "status": t.get("status") if t.get(
                "status") in TOOL_STATUSES else "done", "at": _at(t.get("at"))})
    agent = d.get("agent")
    return {"id": _session_id(d.get("id")) or sid, "agent": agent if isinstance(agent, str) and OC_ID_RE.match(
                agent) else None,
            "title": _text(d.get("title"), 200), "origin": _origin(d.get("origin")), "ticketId": _int(d.get("ticketId")),
            "busy": d.get("busy") is True, "busySince": _at(d.get("busySince")),
            "needsApproval": d.get("needsApproval") is True, "approvalText": _text(d.get("approvalText"), 300),
            "messages": msgs[-MAX_MESSAGES:], "clipped": len(msgs) > MAX_MESSAGES, "tools": tools[-MAX_TOOLS:]}


class OpenCode:
    """key -> {at, data}: "agents", "sessions:<agent>", "session:<id>"."""

    def __init__(self, n8n, clock):
        self.n8n = n8n
        self.clock = clock
        self.cache = {}
        self.last_send = {}  # HA user id -> logical ms of the last accepted send
        self.sent = collections.OrderedDict()  # clientId -> sessionId (a repeated clientId gets the same answer)
        self.fetches = 0  # requests sent to n8n (tests)

    def sweep(self):
        limit = self.clock.now_ms() - KEEP * 1000
        for k in [k for k, v in self.cache.items() if v["at"] < limit]:
            del self.cache[k]

    def clear(self):
        self.cache.clear()

    def forget(self, agent=None, session=None):
        self.cache.pop("agents", None)
        if agent:
            self.cache.pop(f"sessions:{agent}", None)
        if session:
            self.cache.pop(f"session:{session}", None)

    async def _get(self, key, ttl, fetch, shape, fresh):
        """{data, at, cached[, stale, error]}; a failed fetch falls back to a copy that is still in memory."""
        self.sweep()
        now = self.clock.now_ms()
        c = self.cache.get(key)
        if c is not None and now - c["at"] < (FRESH_MIN if fresh else ttl) * 1000:
            return {"data": c["data"], "at": c["at"], "cached": True}
        from .n8n import N8nError

        self.fetches += 1
        try:
            data = shape(await fetch())
        except N8nError as err:
            if c is not None and not (err.code == "http" and err.status and 400 <= err.status < 500):
                return {"data": c["data"], "at": c["at"], "cached": True, "stale": True, "error": err.message}
            raise
        self.cache[key] = {"at": now, "data": data}
        return {"data": data, "at": now, "cached": False}

    async def agents(self, fresh=False):
        def shape(raw):
            items = raw.get("agents") if isinstance(raw, dict) else raw
            return [x for x in (shape_agent(a) for a in (items if isinstance(items, list) else [])) if x]

        return await self._get("agents", AGENTS_TTL, self.n8n.oc_agents, shape, fresh)

    async def sessions(self, agent, fresh=False):
        def shape(raw):
            items = raw.get("sessions") if isinstance(raw, dict) else raw
            return [x for x in (shape_brief(s) for s in (items if isinstance(items, list) else [])) if x]

        return await self._get(f"sessions:{agent}", SESSIONS_TTL, lambda: self.n8n.oc_sessions(agent, SESSIONS_LIMIT),
                               shape, fresh)

    async def session(self, sid, fresh=False):
        return await self._get(f"session:{sid}", SESSION_TTL, lambda: self.n8n.oc_session(sid),
                               lambda raw: shape_session(raw, sid), fresh)

    # ---- sends ----------------------------------------------------------------------------------------------------
    def wait_left(self, user_id):
        """Logical seconds until this user may send again (0 = now)."""
        last = self.last_send.get(user_id)
        if last is None:
            return 0
        left = SEND_EVERY - (self.clock.now_ms() - last) / 1000
        return max(0, left)

    def mark_sent(self, user_id):
        self.last_send[user_id] = self.clock.now_ms()

    def unmark(self, user_id):
        self.last_send.pop(user_id, None)

    def remember(self, client_id, session_id):
        self.sent[client_id] = session_id
        while len(self.sent) > 500:
            self.sent.popitem(last=False)
