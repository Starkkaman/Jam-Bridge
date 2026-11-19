"""Claude conversations (docs/BRIDGE.md §13): fetched from the machine on demand through crew_hub/transcript, shaped for
the page and held in memory only.

Nothing here touches SQLite. A session's conversation lives in `Conversations.cache` for at most CACHE_TTL seconds,
and is dropped earlier when the page closes it (api .../conversation/close), when the last page disconnects (SSE
unsubscribe) and on restart (it is process memory). A Bridge thread for a Claude session is a pointer
(machine/session); only what the user sends is stored, as before.

Machine turns (docs/COMMANDS.md § transcript) become page turns:
  {id, k: user|assistant|report, at, text, via?: bridge|machine, steps?: {verb: n}, agentId?, title?, masked?}
Masked runs ("••••" in a turn marked masked) become the single token MASK_TOKEN, which the page draws as •••• + a tag.
"""

import asyncio
import hashlib
import re

CACHE_TTL = 60  # logical seconds a session's conversation may stay in memory
LIMIT = 20  # turns per request (the contract allows 1..40)
TEXT_MAX = 4000
MASK_TOKEN = "⟦masked⟧"
MACHINE_MASK = "••••"
STEP_VERBS = ("read", "search", "edit", "write", "run", "web", "agent", "other")
BRIDGE_PREFIX = re.compile(r"^\[From [^\]\n]{1,60} via The Bridge\] ?")
CURSOR_RE = re.compile(r"^[\x21-\x7e]{1,256}$")
GONE_ERRORS = frozenset({"not_live", "transcript_unavailable", "unknown_session"})


def _text(value, limit=TEXT_MAX):
    if not isinstance(value, str):
        return ""
    value = "".join(ch for ch in value if ch in "\n\t" or ch.isprintable())
    return value if len(value) <= limit else value[:limit - 1] + "…"


def _at(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str) and len(value) <= 40:
        return value
    return None


def _steps(value):
    if not isinstance(value, dict):
        return {}
    out = {}
    for verb in STEP_VERBS:
        n = value.get(verb)
        if isinstance(n, int) and not isinstance(n, bool) and n > 0:
            out[verb] = min(n, 9999)
    return out


def turn_id(machine, session, kind, at, text):
    h = hashlib.sha1(f"{machine}\n{session}\n{kind}\n{at}\n{text[:200]}".encode("utf-8", "replace"))
    return "t" + h.hexdigest()[:12]


def page_turn(machine, session, raw):
    """One machine turn -> one page turn (None for anything that isn't one of the three kinds)."""
    if not isinstance(raw, dict):
        return None
    kind = raw.get("kind")
    if kind not in ("user", "assistant", "report"):
        return None
    text = _text(raw.get("text"))
    masked = raw.get("masked") is True
    if masked:
        text = text.replace(MACHINE_MASK, MASK_TOKEN)
    at = _at(raw.get("at"))
    t = {"id": turn_id(machine, session, kind, at, text), "k": kind, "at": at, "text": text}
    if masked:
        t["masked"] = True
    if kind == "user":
        m = BRIDGE_PREFIX.match(text)
        t["via"] = "bridge" if m else "machine"
        if m:
            t["text"] = text[m.end():]
    elif kind == "assistant":
        t["steps"] = _steps(raw.get("steps"))
    else:
        t["agentId"] = _text(raw.get("agent"), 128) or None
        t["title"] = _text(raw.get("title"), 200) or None
    return t


def page_turns(machine, session, raw_turns):
    out = []
    for raw in raw_turns if isinstance(raw_turns, list) else []:
        t = page_turn(machine, session, raw)
        if t is not None:
            out.append(t)
    return out


class ConversationError(Exception):
    def __init__(self, code, message=""):
        super().__init__(message or code)
        self.code = code


class Conversations:
    """The in-memory cache: key "machine/session" -> {at, turns, cursor, more}."""

    def __init__(self, crew, clock, ttl=CACHE_TTL):
        self.crew = crew
        self.clock = clock
        self.ttl = ttl
        self.cache = {}
        self._inflight = {}
        self.fetches = 0  # requests sent to crew_hub (tests)

    # ---- cache --------------------------------------------------------------------------------------------------
    def sweep(self):
        limit = self.clock.now_ms() - self.ttl * 1000
        for k in [k for k, v in self.cache.items() if v["at"] < limit]:
            del self.cache[k]

    def drop(self, key):
        self.cache.pop(key, None)

    def clear(self):
        self.cache.clear()

    async def sweep_loop(self):
        while True:
            await self.clock.sleep(5)
            self.sweep()

    # ---- fetch --------------------------------------------------------------------------------------------------
    async def _ask(self, machine, session, after):
        self.fetches += 1
        res = await self.crew.transcript(machine, session, after=after, limit=LIMIT)
        if not res.get("ok"):
            raise ConversationError(str(res.get("error") or "failed")[:60])
        plain = res.get("result")
        if not isinstance(plain, dict):
            raise ConversationError("bad_result")
        cursor = plain.get("cursor")
        cursor = cursor if isinstance(cursor, str) and CURSOR_RE.match(cursor) else None
        return {"turns": page_turns(machine, session, plain.get("turns")), "cursor": cursor,
                "more": plain.get("more") is True}

    async def _shared(self, key, coro_fn):
        fut = self._inflight.get(key)
        if fut is None:
            fut = asyncio.ensure_future(coro_fn())
            self._inflight[key] = fut
            fut.add_done_callback(lambda f, k=key: self._inflight.pop(k, None))
        return await asyncio.shield(fut)

    async def get(self, machine, session, after=None, fresh=False):
        """{turns, cursor, more, at, cached}. `after` returns only newer turns (and appends them to a live cache
        entry whose cursor it continues); without it the whole recent conversation, from the cache when < ttl old."""
        key = f"{machine}/{session}"
        self.sweep()
        now = self.clock.now_ms()
        if after:
            data = await self._shared(f"{key}?{after}", lambda: self._ask(machine, session, after))
            c = self.cache.get(key)
            if c is not None and c["cursor"] == after and data["cursor"]:
                seen = {t["id"] for t in c["turns"]}
                c["turns"] = c["turns"] + [t for t in data["turns"] if t["id"] not in seen]
                c["cursor"] = data["cursor"]
            return {"turns": data["turns"], "cursor": data["cursor"] or after, "more": False, "at": now,
                    "cached": False}
        c = self.cache.get(key)
        if c is not None and not fresh:
            return {"turns": list(c["turns"]), "cursor": c["cursor"], "more": c["more"], "at": c["at"], "cached": True}
        data = await self._shared(key, lambda: self._ask(machine, session, None))
        self.cache[key] = {"at": now, "turns": list(data["turns"]), "cursor": data["cursor"], "more": data["more"]}
        return {"turns": data["turns"], "cursor": data["cursor"], "more": data["more"], "at": now, "cached": False}
