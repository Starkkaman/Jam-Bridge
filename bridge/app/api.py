"""The page <-> server API (docs/BRIDGE.md §6). Relative URLs under api/.

Every mutating request needs `X-Bridge: 1`, a JSON object body <= 64 KB, a `clientId` and an HA admin user
(X-Remote-User-Id + Groups containing system-admin, set by the Supervisor). Every write lands in `actions`.
Errors are {error, code}. Data is text: nothing here renders HTML.
"""

import re

from aiohttp import web

from . import agents as agents_mod
from .conversation import CURSOR_RE, GONE_ERRORS, ConversationError
from .crew import COMMAND_ACTIONS, CrewError
from .db import RETRYABLE, THEMES, TICKET_STATUSES, USER_TEXT_MAX
from .n8n import N8nError

APP_VERSION = "0.2.1"

APPROVE_COMMENT = "Approved"
LIMITS = {"title": 120, "details": 4000, "comment": 4000, "resolution": 2000}
CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
THREAD_ID_RE = re.compile(r"^th_[0-9a-f]{32}$")
WORD_RE = re.compile(r"^[a-z][a-z0-9_-]{0,19}$")
WORKFLOW_RE = re.compile(r"^[A-Za-z0-9]{1,40}$")
EXEC_RE = re.compile(r"^[0-9]{1,20}$")
MACHINE_RE = re.compile(r"^[\w.-]{1,64}$")
SESSION_RE = re.compile(r"^[\w.:-]{1,128}$")
CONFIRM_ACTIONS = ("interrupt", "stop")
RUNS_TTL = 60
OUTPUT_TTL = 60
RUN_TTL_LIVE = 15
RUN_TTL_DONE = 86400
AGENT_RUN_TTL = 60  # api/agents/{id}/runs/{runId}
AGENT_OUTPUT_TTL = 30  # api/agents/{id}/output
OUTPUT_FRESH_MIN = 5  # Refresh asks n8n again unless the copy is younger than this
CHAT_CONTROLS = ("send", "transcript")
SAFE_FIX = ("low", "replace_trailer")  # the owner's decision 1: "Apply all safe"
NOT_ADMIN = "Open The Bridge from Home Assistant as an admin to make changes"


class ApiError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _k(name):
    from . import server

    return getattr(server, name)


def ctx(request):
    app = request.app
    return (app[_k("K_DB")], app[_k("K_N8N")], app[_k("K_CREW")], app[_k("K_JOBS")], app[_k("K_HUB")],
            app[_k("K_CLOCK")])


def conversations(request):
    return request.app[_k("K_CONV")]


async def audit(db, user, kind, target, summary, outcome="ok", error=None):
    """One audit row (actions) for a read that reaches n8n or a machine: who, what, when, how it went."""
    action_id, _ = await db.a("begin_action", user, kind, target, summary, None)
    await db.a("finish_action", action_id, outcome, error)


def req_user(request):
    return request[_k("K_USER")]


async def not_found(request):
    raise ApiError(404, "not_found", "Not found")


# ---- input helpers ----------------------------------------------------------------------------------------------------
def clean_text(value, field, limit, required=False, reject_long=True):
    """Strings only; control characters other than newline and tab removed; over-limit is an error (or clipped)."""
    if value is None or value == "":
        if required:
            raise ApiError(400, "missing", f"{field} is required")
        return None
    if not isinstance(value, str):
        raise ApiError(400, "invalid", f"{field} must be text")
    value = "".join(ch for ch in value if ch in "\n\t" or ch.isprintable()).strip()
    if not value:
        if required:
            raise ApiError(400, "missing", f"{field} is required")
        return None
    if len(value) > limit:
        if reject_long:
            raise ApiError(400, "too_long", f"{field} is longer than {limit} characters")
        value = value[:limit]
    return value


def as_bool(value, field):
    if value is None:
        return False
    if not isinstance(value, bool):
        raise ApiError(400, "invalid", f"{field} must be true or false")
    return value


def int_param(request, name, default=None, lo=1, hi=2 ** 53):
    raw = request.query.get(name)
    if raw in (None, ""):
        return default
    try:
        v = int(raw)
    except ValueError:
        raise ApiError(400, "invalid", f"{name} must be a number") from None
    return max(lo, min(hi, v))


async def read_body(request):
    if request.content_length is not None and request.content_length > _k("MAX_BODY"):
        raise ApiError(413, "too_large", "Request body is larger than 64 KB")
    raw = await request.read()
    if not raw:
        return {}
    try:
        import json

        body = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise ApiError(400, "bad_json", "Body is not JSON") from None
    if not isinstance(body, dict):
        raise ApiError(400, "bad_json", "Body must be a JSON object")
    return body


def write(need_client_id=True):
    """Guard for mutating handlers: X-Bridge CSRF header, an admin HA user, a JSON body, a clientId."""

    def deco(fn):
        async def wrapper(request):
            if request.headers.get("X-Bridge") != "1":
                raise ApiError(403, "csrf", "Missing X-Bridge header")
            user = req_user(request)
            if not user.can_write:
                raise ApiError(403, "not_admin", NOT_ADMIN)
            body = await read_body(request)
            cid = body.get("clientId")
            if need_client_id or cid is not None:
                if not isinstance(cid, str) or not CLIENT_ID_RE.match(cid):
                    raise ApiError(400, "client_id", "clientId (a uuid) is required")
            return await fn(request, body, user)

        wrapper.__name__ = fn.__name__
        return wrapper

    return deco


def duplicate(existing):
    return web.json_response({"duplicate": True, "outcome": existing.get("outcome"), "error": existing.get("error")})


def n8n_error(err):
    if err.code == "key":
        return ApiError(502, "n8n_key", err.message)
    if err.code == "timeout":
        return ApiError(504, "n8n_timeout", err.message)
    if err.code == "not_configured":
        return ApiError(503, "n8n_not_configured", err.message)
    return ApiError(502, "n8n_" + err.code, err.message)


# ---- shared views -----------------------------------------------------------------------------------------------------
def n8n_health(n8n):
    h = dict(n8n.health)
    h["configured"] = bool(n8n.base)
    if h.get("code") == "key":
        h["setup"] = "n8n key missing or wrong"
    return h


def crew_health(crew):
    return {"connected": crew.connected, "at": crew.connected_at, "error": crew.error}


async def cached_status(db):
    c = await db.a("cache_get", "n8n.status")
    return (c or {}).get("data") or {}, c


def claude_sessions(crew):
    for mid, m in (crew.machines or {}).items():
        if not isinstance(m, dict):
            continue
        for s in m.get("sessions") or []:
            if isinstance(s, dict):
                yield mid, m, s


def session_brief(mid, s):
    return {"machine": mid, "session": s.get("id"), "name": s.get("name"), "status": s.get("status"),
            "summary": s.get("summary"), "waiting": s.get("waiting"), "kind": s.get("kind"),
            "controls": s.get("controls")}


def machine_commands_on(m):
    return bool(((m or {}).get("machine") or {}).get("commands", {}).get("enabled")) if isinstance(m, dict) else False


def session_chat(m, s):
    """(True, None) when a Claude session is two-way (send + transcript, commands on, not ended), else (False, reason)."""
    if not isinstance(s, dict):
        return False, "gone"
    if s.get("status") == "ended":
        return False, "ended"
    if not machine_commands_on(m):
        return False, "commands_off"
    controls = s.get("controls") or []
    if "send" not in controls:
        return False, "no_send"
    if "transcript" not in controls:
        return False, "no_transcript"
    return True, None


def thread_listed(t, status, crew):
    """Chat lists only two-way threads (the owner's decision 1): Agent Hub, n8n agents with chat: true (on or off) and
    Claude sessions that can both take messages and send their conversation. Unknown (no status yet, crew_hub not
    connected) keeps a thread listed."""
    if t["kind"] == "n8n":
        if t["target"] == "crew":
            return True
        known = [a for a in (status or {}).get("agents") or [] if isinstance(a, dict)]
        if not known:
            return True
        agent = agents_mod.find_agent(status, t["target"])
        return bool(agent and agent.get("chat"))
    machine, _, session = (t["target"] or "").partition("/")
    if not crew.machines and not crew.connected:
        return True
    m = crew.machines.get(machine)
    return session_chat(m, crew.find_session(machine, session))[0]


def chat_sessions(crew, threads):
    """The Claude sessions Chat offers as two-way threads, with the open pointer thread when there is one."""
    by_target = {t["target"]: t["id"] for t in threads if t["kind"] == "claude"}
    out = []
    for mid, m, s in claude_sessions(crew):
        if not session_chat(m, s)[0] or not isinstance(s.get("id"), str):
            continue
        target = f"{mid}/{s['id']}"
        out.append({"machine": mid, "session": s["id"], "name": s.get("name"), "status": s.get("status"),
                    "online": m.get("status") == "online", "updatedAt": s.get("updatedAt"),
                    "threadId": by_target.get(target)})
    return out


def safe_ids(lib):
    return [f["id"] for f in (lib or {}).get("findings") or []
            if not f["applied"] and (f["severity"], f["fix"]) == SAFE_FIX]


async def unread(db, user):
    threads = await db.a("list_threads", user.id)
    tickets = await db.a("tickets_with_new_agent_comment", user.id)
    lib = await db.a("get_library")
    lib_unread = 0
    if lib and user.id:
        since = await db.a("read_at", user.id, "library")
        if (lib.get("fetchedAt") or 0) > since:
            lib_unread = sum(1 for f in lib["findings"] if not f["applied"])
    return {"threads": sum(t["unread"] for t in threads), "inbox": await db.a("inbox_unread", user.id),
            "tickets": len(tickets), "library": lib_unread,
            "ticketsNeedYou": len(await db.a("tickets_needing_you"))}


async def user_settings(db, user):
    s = await db.a("get_setting", f"user:{user.id}", {}) if user.id else {}
    return {"theme": s.get("theme") if s.get("theme") in THEMES else "follow_ha",
            "replayAfterHours": await db.a("get_setting", "replay_after_hours"), "chatRetentionDays": 90}


# ---- GET handlers -----------------------------------------------------------------------------------------------------
async def get_state(request):
    db, n8n, crew, jobs, hub, clock = ctx(request)
    user = req_user(request)
    return web.json_response({
        "version": APP_VERSION, "user": user.as_dict(), "serverTime": clock.now_ms(),
        "health": {"n8n": n8n_health(n8n), "crew": crew_health(crew)},
        "unread": await unread(db, user), "settings": await user_settings(db, user),
        "applyJob": _job_view(jobs.apply_job)})


def _job_view(job):
    from .jobs import job_event

    return job_event(job) if job else None


async def get_overview(request):
    db, n8n, crew, jobs, hub, clock = ctx(request)
    user = req_user(request)
    status, cache = await cached_status(db)
    agents = [a for a in status.get("agents") or [] if isinstance(a, dict)]
    lib = await db.a("get_library")
    waiting = [session_brief(m, s) for m, _, s in claude_sessions(crew) if s.get("status") == "waiting"]
    working = [session_brief(m, s) for m, _, s in claude_sessions(crew) if s.get("status") == "working"]
    return web.json_response({
        "health": {"n8n": n8n_health(n8n), "crew": crew_health(crew)},
        "status": {k: status.get(k) for k in ("overall", "updated", "counts")}, "statusAt": (cache or {}).get("at"),
        "needsYou": {
            "claude": waiting,
            "problems": status.get("problems") or [],
            "agents": [a for a in agents if a.get("state") in ("error", "down")],
            "tickets": await db.a("tickets_with_new_agent_comment", user.id),
            "ticketsNeedYou": await db.a("tickets_needing_you"),
            "findings": sum(1 for f in (lib or {}).get("findings", []) if not f["applied"]),
        },
        "running": {"n8n": status.get("running") or [], "claude": working},
        "unread": await unread(db, user)})


async def get_crew(request):
    db, n8n, crew, jobs, hub, clock = ctx(request)
    status, cache = await cached_status(db)
    return web.json_response({"connected": crew.connected, "at": crew.connected_at, "machines": crew.machines,
                              "agents": agents_mod.grouped(status), "agentsAt": (cache or {}).get("at"),
                              "n8n": n8n_health(n8n)})


async def get_threads(request):
    db, n8n, crew, *_ = ctx(request)
    archived = request.query.get("archived") == "1"
    status, _ = await cached_status(db)
    threads = [t for t in await db.a("list_threads", req_user(request).id, archived) if thread_listed(t, status, crew)]
    return web.json_response({"threads": threads, "claude": [] if archived else chat_sessions(crew, threads)})


async def get_messages(request):
    db = ctx(request)[0]
    thread = await _thread(db, request.match_info["tid"])
    before = int_param(request, "before")
    limit = int_param(request, "limit", 50, 1, 200)
    msgs = await db.a("list_messages", thread["id"], before, limit)
    return web.json_response({"thread": db.thread_dict(thread), "messages": msgs,
                              "more": bool(msgs) and len(msgs) == limit})


async def get_tickets(request):
    db = ctx(request)[0]
    status = request.query.get("status") or None
    if status and status not in TICKET_STATUSES:
        raise ApiError(400, "invalid", "status is not valid")
    c = await db.a("cache_get", "n8n.status")
    return web.json_response({"tickets": await db.a("list_tickets", status),
                              "newAgentComments": await db.a("tickets_with_new_agent_comment", req_user(request).id),
                              "limits": LIMITS, "statusAt": (c or {}).get("at")})


async def get_tasks(request):
    db, n8n, crew, jobs, hub, clock = ctx(request)
    status, cache = await cached_status(db)
    g = agents_mod.grouped(status)
    lib = await db.a("get_library")
    plans = [{"machine": m, "session": s.get("id"), "name": s.get("name"), "status": s.get("status"),
              "plan": s.get("plan"), "tasks": s.get("tasks")}
             for m, _, s in claude_sessions(crew) if s.get("plan") or s.get("tasks")]
    lib_agent = next((a for a in g["services"] if a.get("role") == "library"), None)
    return web.json_response({
        "running": status.get("running") or [],
        "workflows": [a for a in g["agents"] + g["system"] if a.get("workflowId")],
        "library": {"agent": lib_agent, "date": (lib or {}).get("date"), "finished": (lib or {}).get("finished"),
                    "total": len((lib or {}).get("findings") or []),
                    "unapplied": sum(1 for f in (lib or {}).get("findings") or [] if not f["applied"]),
                    "applyJob": _job_view(jobs.apply_job)},
        "plans": plans, "at": (cache or {}).get("at")})


async def _cached_proxy(request, key, ttl, fetch, fresh=False):
    """`fresh` skips a cached copy older than OUTPUT_FRESH_MIN (Refresh); a failed fetch still falls back to it."""
    db, n8n, crew, jobs, hub, clock = ctx(request)
    c = await db.a("cache_get", key)
    now = clock.now_ms()
    if c and c["data"] is not None and c["at"] and now - c["at"] < (OUTPUT_FRESH_MIN if fresh else ttl) * 1000:
        return {"data": c["data"], "at": c["at"], "cached": True}
    try:
        data = await fetch()
    except N8nError as err:
        if c and c["data"] is not None:
            return {"data": c["data"], "at": c["at"], "cached": True, "stale": True, "error": err.message}
        raise n8n_error(err) from None
    await db.a("cache_put", key, data)
    return {"data": data, "at": now, "cached": False}


async def get_runs(request):
    wf = request.query.get("workflowId") or ""
    if not WORKFLOW_RE.match(wf):
        raise ApiError(400, "no_runs", "Runs need an n8n workflowId (services have no runs)")
    n8n = ctx(request)[1]
    out = await _cached_proxy(request, f"runs:{wf}", RUNS_TTL, lambda: n8n.runs(wf))
    runs = out.pop("data")
    out["runs"] = (runs if isinstance(runs, list) else [])[:20]
    out["workflowId"] = wf
    return web.json_response(out)


async def get_run(request):
    rid = request.query.get("id") or ""
    if not EXEC_RE.match(rid):
        raise ApiError(400, "invalid", "id must be an n8n execution id")
    db, n8n, *_ = ctx(request)
    c = await db.a("cache_get", f"run:{rid}")
    live = (c or {}).get("data") and (c["data"].get("status") in ("running", "waiting", "new", None))
    out = await _cached_proxy(request, f"run:{rid}", RUN_TTL_LIVE if (live or not c) else RUN_TTL_DONE,
                              lambda: n8n.run(rid))
    out["run"] = out.pop("data")
    return web.json_response(out)


async def get_agent_output(request):
    aid = request.query.get("id") or ""
    if not aid.startswith("svc:") or not agents_mod.AGENT_ID_RE.match(aid):
        raise ApiError(400, "invalid", "id must be a service id (svc:…)")
    n8n = ctx(request)[1]
    out = await _cached_proxy(request, f"output:{aid}", OUTPUT_TTL, lambda: n8n.agent_output(aid))
    out["output"] = out.pop("data")
    return web.json_response(out)


def _known_agent(status, aid):
    if not agents_mod.AGENT_ID_RE.match(aid or ""):
        raise ApiError(404, "not_found", "No such agent")
    agent = agents_mod.find_agent(status, aid)
    if agent is None:
        raise ApiError(404, "not_found", "No such agent")
    return agent


async def get_agent_run(request):
    """api/agents/{id}/runs/{runId}: n8n's /bridge/run for a run of this agent's workflow (60 s cache; a live run
    15 s). System and untracked agents have none."""
    db, n8n, *_ = ctx(request)
    user = req_user(request)
    aid, rid = request.match_info["aid"], request.match_info["rid"]
    status, _ = await cached_status(db)
    agent = _known_agent(status, aid)
    if not EXEC_RE.match(rid):
        raise ApiError(400, "invalid", "runId must be an n8n execution id")
    wf = agents_mod.bare_workflow_id(agent.get("workflowId"))
    if agents_mod.group_of(agent) == "system" or agent.get("tracked") is False or not wf:
        raise ApiError(404, "no_runs", "This agent keeps no runs")
    c = await db.a("cache_get", f"run:{rid}")
    live = (c or {}).get("data") and isinstance(c["data"], dict) and c["data"].get("status") in (
        "running", "waiting", "new", None)
    try:
        out = await _cached_proxy(request, f"run:{rid}", RUN_TTL_LIVE if live else AGENT_RUN_TTL, lambda: n8n.run(rid))
    except ApiError as err:
        await audit(db, user, "agent.run", aid, f"run {rid}", "error", err.code)
        if err.code == "n8n_http":
            raise ApiError(404, "no_run", f"n8n has no run {rid}") from None
        raise
    run = out.pop("data")
    if not isinstance(run, dict) or (run.get("workflowId") and agents_mod.bare_workflow_id(run["workflowId"]) != wf):
        await audit(db, user, "agent.run", aid, f"run {rid}", "refused", "other workflow")
        raise ApiError(404, "no_run", f"Run {rid} is not a run of this agent")
    await audit(db, user, "agent.run", aid, f"run {rid}" + (" · cached" if out["cached"] else ""))
    out["run"] = run
    out["agentId"] = aid
    return web.json_response(out)


async def get_agent_output2(request):
    """api/agents/{id}/output[?fresh=1]: a service's report from n8n's /bridge/agent-output (30 s cache; Refresh asks
    again). Workflows have none (404 no_output), unknown ids are 404 not_found, and a known service that n8n has no
    report for answers 200 {output: null, reason: "no_output"} (an expected state, not an error on the page)."""
    db, n8n, *_ = ctx(request)
    user = req_user(request)
    aid = request.match_info["aid"]
    status, _ = await cached_status(db)
    if not aid.startswith("svc:") or not agents_mod.AGENT_ID_RE.match(aid):
        raise ApiError(404, "no_output", "Only services write a report")
    if [a for a in status.get("agents") or [] if isinstance(a, dict)]:
        _known_agent(status, aid)
    fresh = request.query.get("fresh") == "1"
    try:
        out = await _cached_proxy(request, f"output:{aid}", AGENT_OUTPUT_TTL, lambda: n8n.agent_output(aid), fresh)
    except ApiError as err:
        await audit(db, user, "agent.output", aid, "refresh" if fresh else "open", "error", err.code)
        if err.code == "n8n_http":
            return web.json_response({"id": aid, "output": None, "reason": "no_output", "cached": False})
        raise
    await audit(db, user, "agent.output", aid, ("refresh" if fresh else "open") + (" · cached" if out["cached"] else ""),
                "stale" if out.get("stale") else "ok")
    out["output"] = out.pop("data")
    return web.json_response(out)


# ---- Claude conversations (in memory only, docs/BRIDGE.md §13) -------------------------------------------------------
def _session_target(request):
    machine, session = request.match_info["machine"], request.match_info["session"]
    if not MACHINE_RE.match(machine) or not SESSION_RE.match(session):
        raise ApiError(404, "not_found", "No such session")
    return machine, session


async def get_conversation(request):
    """api/sessions/{machine}/{session}/conversation[?after=<cursor>][&fresh=1] -> {turns, cursor, more, at, cached}
    or {gone: true, reason}. Asked of the machine through crew_hub/transcript; kept in memory <= 60 s, never stored."""
    db, n8n, crew, jobs, hub, clock = ctx(request)
    user = req_user(request)
    if not user.can_write:
        raise ApiError(403, "not_admin", "Open The Bridge from Home Assistant as an admin to read conversations")
    machine, session = _session_target(request)
    after = request.query.get("after") or None
    if after is not None and not CURSOR_RE.match(after):
        raise ApiError(400, "invalid", "after must be a cursor from an earlier answer")
    fresh = request.query.get("fresh") == "1"
    if not crew.connected:
        raise ApiError(503, "crew_disconnected", "crew_hub is not reachable")
    m = crew.machines.get(machine)
    s = crew.find_session(machine, session)
    if not isinstance(m, dict) or s is None:
        raise ApiError(404, "unknown_session", "That session is gone")
    if m.get("status") != "online":
        raise ApiError(409, "offline", f"{machine} is offline")
    if not machine_commands_on(m) or "transcript" not in (s.get("controls") or []):
        raise ApiError(409, "no_transcript", "This session can't send its conversation")
    target = f"{machine}/{session}"
    what = "poll" if after else "refresh" if fresh else "open"
    try:
        data = await conversations(request).get(machine, session, after=after, fresh=fresh)
    except CrewError as err:
        await audit(db, user, "claude.transcript", target, what, "timeout" if err.code == "timeout" else "error",
                    err.code)
        if err.code == "timeout":
            raise ApiError(504, "crew_timeout", f"{machine} didn't answer") from None
        raise ApiError(503, "crew_" + err.code, "crew_hub is not reachable" if err.code == "disconnected"
                       else f"crew_hub: {err.code}") from None
    except ConversationError as err:
        await audit(db, user, "claude.transcript", target, what, "refused", err.code)
        if err.code in GONE_ERRORS:
            return web.json_response({"machine": machine, "session": session, "gone": True, "reason": err.code,
                                      "turns": []})
        raise ApiError(502, err.code, f"{machine} didn't send it ({err.code})") from None
    await audit(db, user, "claude.transcript", target, what + (" · cached" if data["cached"] else ""))
    return web.json_response({"machine": machine, "session": session, **data, "cut": bool(data["more"])})


@write(need_client_id=False)
async def post_conversation_close(request, body, user):
    """The page left the conversation: forget it now rather than at the 60 s mark."""
    machine, session = _session_target(request)
    conversations(request).drop(f"{machine}/{session}")
    return web.json_response({"ok": True})


async def get_library(request):
    db, n8n, crew, jobs, hub, clock = ctx(request)
    lib = await db.a("get_library")
    status, _ = await cached_status(db)
    lib_agent = next((a for a in agents_mod.grouped(status)["services"] if a.get("role") == "library"), None)
    return web.json_response({"report": lib, "safeIds": safe_ids(lib), "applyJob": _job_view(jobs.apply_job),
                              "quietHours": {"from": "17:00", "to": "01:00"}, "agent": lib_agent})


async def get_precheck(request):
    db, n8n, crew, jobs, hub, clock = ctx(request)
    try:
        data = await n8n.precheck()
    except N8nError as err:
        raise n8n_error(err) from None
    return web.json_response({"precheck": data, "at": clock.now_ms()})


async def get_inbox(request):
    db = ctx(request)[0]
    before = int_param(request, "before")
    limit = int_param(request, "limit", 50, 1, 200)
    items = await db.a("list_inbox", before, limit)
    return web.json_response({"items": items, "more": len(items) == limit})


async def get_job(request):
    jobs = ctx(request)[3]
    job = jobs.jobs.get(request.match_info["jid"])
    if job is None:
        raise ApiError(404, "not_found", "No such job")
    return web.json_response({"job": _job_view(job)})


async def get_settings(request):
    db = ctx(request)[0]
    return web.json_response(await user_settings(db, req_user(request)))


# ---- threads and chat -------------------------------------------------------------------------------------------------
async def _thread(db, tid):
    if not THREAD_ID_RE.match(tid or ""):
        raise ApiError(404, "not_found", "No such thread")
    row = await db.a("get_thread", tid)
    if row is None:
        raise ApiError(404, "not_found", "No such thread")
    return row


@write()
async def post_threads(request, body, user):
    db, n8n, crew, jobs, hub, clock = ctx(request)
    kind = body.get("kind")
    target = body.get("target")
    if kind not in ("n8n", "claude") or not isinstance(target, str):
        raise ApiError(400, "invalid", "kind must be n8n or claude, with a target")
    if kind == "n8n":
        if target == "crew":
            name = "Crew"
        else:
            if not agents_mod.AGENT_ID_RE.match(target):
                raise ApiError(400, "invalid", "target must be 'crew' or an agent id")
            status, _ = await cached_status(db)
            agent = agents_mod.find_agent(status, target)
            if agent is None or not agent.get("chat"):
                raise ApiError(400, "not_chat", "This agent is not set up for chat")
            name = clean_text(agent.get("name"), "name", 120, reject_long=False) or target
    else:
        machine, _, session = target.partition("/")
        if not MACHINE_RE.match(machine) or not SESSION_RE.match(session):
            raise ApiError(400, "invalid", "target must be <machine>/<session id>")
        s = crew.find_session(machine, session)
        if s is None and crew.machines:
            raise ApiError(400, "unknown_session", "No such Claude session")
        if s is not None and not session_chat(crew.machines.get(machine), s)[0]:
            raise ApiError(400, "not_chat", "This session can't chat: " + session_chat(crew.machines.get(machine), s)[1])
        name = clean_text((s or {}).get("name"), "name", 120, reject_long=False) or session[:12]
    new = as_bool(body.get("new"), "new")
    action_id, existing = await db.a("begin_action", user, "thread.open", target, f"{kind} {target}", body["clientId"])
    if existing is not None:  # a repeated clientId: same answer, no second thread
        new = False
    row, created = await db.a("open_thread", kind, target, name, user, new)
    if existing is None:
        await db.a("finish_action", action_id, "ok")
    view = db.thread_dict(row, unread=await db.a("thread_unread", user.id, row["id"]))
    if created:
        hub.publish("thread", view)
    return web.json_response({"thread": view, "created": created}, status=201 if created else 200)


@write()
async def patch_thread(request, body, user):
    db, n8n, crew, jobs, hub, clock = ctx(request)
    thread = await _thread(db, request.match_info["tid"])
    title = body.get("title")
    if title is not None:
        title = clean_text(title, "title", 80, reject_long=False) or ""
    archived = None if body.get("archived") is None else as_bool(body.get("archived"), "archived")
    rotate = as_bool(body.get("resetMemory"), "resetMemory")
    if title is None and archived is None and not rotate:
        raise ApiError(400, "invalid", "Nothing to change")
    action_id, existing = await db.a("begin_action", user, "thread.update", thread["id"],
                                     ",".join(k for k in ("title", "archived", "resetMemory") if k in body),
                                     body["clientId"])
    if existing is not None:
        return duplicate(existing)
    row = await db.a("update_thread", thread["id"], title, archived, rotate)
    await db.a("finish_action", action_id, "ok")
    view = db.thread_dict(row)
    hub.publish("thread", view)
    return web.json_response({"thread": view})


@write()
async def post_message(request, body, user):
    db, n8n, crew, jobs, hub, clock = ctx(request)
    thread = await _thread(db, request.match_info["tid"])
    if thread["archived"]:
        raise ApiError(409, "archived", "This chat is archived")
    text = clean_text(body.get("text"), "text", USER_TEXT_MAX, required=True)
    urgent = as_bool(body.get("urgent"), "urgent")
    kind = "chat.send" if thread["kind"] == "n8n" else "claude.send"
    untitled = thread["title"] is None
    row, created = await db.a("add_user_message", thread["id"], user, text, body["clientId"], urgent)
    if created and untitled:
        hub.publish("thread", db.thread_dict(await db.a("get_thread", thread["id"])))
    if created:
        action_id, _ = await db.a("begin_action", user, kind, thread["target"], f"message {row['id']}",
                                  body["clientId"])
        await db.a("finish_action", action_id, "ok")
        hub.publish("message", db.message_dict(row))
        jobs.enqueue(thread["id"])
    return web.json_response({"message": db.message_dict(row), "duplicate": not created}, status=202)


@write()
async def retry_message(request, body, user):
    db, n8n, crew, jobs, hub, clock = ctx(request)
    thread = await _thread(db, request.match_info["tid"])
    try:
        mid = int(request.match_info["mid"])
    except ValueError:
        raise ApiError(404, "not_found", "No such message") from None
    row = await db.a("get_message", mid)
    if row is None or row["thread_id"] != thread["id"] or row["role"] != "user":
        raise ApiError(404, "not_found", "No such message")
    action_id, existing = await db.a("begin_action", user, "chat.retry", thread["target"], f"message {mid}",
                                     body["clientId"])
    if existing is not None:
        return web.json_response({"message": db.message_dict(row), "duplicate": True}, status=202)
    if row["state"] not in RETRYABLE:
        await db.a("finish_action", action_id, "refused", row["state"])
        raise ApiError(409, "not_retryable", "Only a failed, timed out, dropped or unknown message can be resent")
    row = await db.a("requeue", mid)
    await db.a("finish_action", action_id, "ok")
    hub.publish("message", db.message_dict(row))
    jobs.enqueue(thread["id"])
    return web.json_response({"message": db.message_dict(row)}, status=202)


@write(need_client_id=False)
async def post_read(request, body, user):
    db, n8n, crew, jobs, hub, clock = ctx(request)
    scope = body.get("scope")
    if not isinstance(scope, str) or not (scope in ("inbox", "tickets", "library") or (
            scope.startswith("thread:") and THREAD_ID_RE.match(scope[7:]))):
        raise ApiError(400, "invalid", "scope must be inbox, tickets, library or thread:<id>")
    now = clock.now_ms()
    at = body.get("at", now)
    if not isinstance(at, int) or isinstance(at, bool) or at < 0:
        raise ApiError(400, "invalid", "at must be epoch ms")
    await db.a("mark_read", user.id, scope, min(at, now))
    return web.json_response({"ok": True})


# ---- tickets ----------------------------------------------------------------------------------------------------------
@write()
async def post_ticket(request, body, user):
    db, n8n, crew, jobs, hub, clock = ctx(request)
    title = clean_text(body.get("title"), "title", LIMITS["title"], required=True)
    details = clean_text(body.get("details"), "details", LIMITS["details"])
    ttype = body.get("type") or "bug"
    if not isinstance(ttype, str) or not WORD_RE.match(ttype):
        raise ApiError(400, "invalid", "type is not valid")
    agent_id = body.get("agentId")
    agent_name = clean_text(body.get("agent"), "agent", 80, reject_long=False)
    if agent_id is not None:
        if not isinstance(agent_id, str) or not agents_mod.AGENT_ID_RE.match(agent_id):
            raise ApiError(400, "invalid", "agentId is not a valid agent id")
        status, _ = await cached_status(db)
        a = agents_mod.find_agent(status, agent_id)
        if a and a.get("name"):
            agent_name = str(a["name"])[:80]
    payload = {"action": "create", "type": ttype, "title": title, "details": details, "agent": agent_name,
               "agent_id": agent_id, "source": f"The Bridge · {user.display}"}
    return await _ticket_call(request, user, "ticket.create", "new", title, payload, body["clientId"])


@write()
async def update_ticket(request, body, user):
    db, n8n, crew, jobs, hub, clock = ctx(request)
    try:
        tid = int(request.match_info["id"])
    except ValueError:
        raise ApiError(404, "not_found", "No such ticket") from None
    status = body.get("status")
    if status is not None and status not in TICKET_STATUSES:
        raise ApiError(400, "invalid", "status must be one of " + ", ".join(TICKET_STATUSES))
    resolution = clean_text(body.get("resolution"), "resolution", LIMITS["resolution"])
    comment = clean_text(body.get("comment"), "comment", LIMITS["comment"])
    approve = as_bool(body.get("approve", False), "approve")
    if approve:
        # Approve = a ticket update with the comment "Approved" (author = the HA user); only a fix awaiting review.
        # Promotion to live still needs the deploy approved in OpenCode.
        if status is not None or resolution is not None or comment is not None:
            raise ApiError(400, "invalid", "approve goes alone")
        t = next((x for x in await db.a("list_tickets") if x["id"] == tid), None)
        if t is None or t["status"] != "awaiting_approval":
            raise ApiError(409, "not_awaiting", "That ticket is not waiting for approval")
        comment = APPROVE_COMMENT
    if status is None and resolution is None and comment is None:
        raise ApiError(400, "invalid", "Nothing to change")
    # Updates go to bridge/ticket-update: {id, status?, resolution?, comment?, author?} (agent-hub/ticket only creates)
    payload = {"id": tid, "author": user.display}
    for k, v in (("status", status), ("resolution", resolution), ("comment", comment)):
        if v is not None:
            payload[k] = v
    summary = "approve" if approve else ",".join(k for k in ("status", "resolution", "comment") if k in payload)
    return await _ticket_call(request, user, "ticket.update", str(tid), summary, payload, body["clientId"])


async def _ticket_call(request, user, kind, target, summary, payload, client_id):
    db, n8n, crew, jobs, hub, clock = ctx(request)
    action_id, existing = await db.a("begin_action", user, kind, target, summary, client_id)
    if existing is not None:
        return duplicate(existing)
    send = n8n.ticket_update if kind == "ticket.update" else n8n.ticket
    try:
        res = await send({k: v for k, v in payload.items() if v is not None})
    except N8nError as err:
        if err.code == "http" and err.status and 400 <= err.status < 500:
            # n8n refused the write and said why: show its words
            await db.a("finish_action", action_id, "refused", err.message)
            raise ApiError(409, "refused", err.message) from None
        await db.a("finish_action", action_id, "timeout" if err.code == "timeout" else "error", err.message)
        raise n8n_error(err) from None
    if isinstance(res, dict) and res.get("ok") is False:
        error = str(res.get("error") or "refused")[:300]
        await db.a("finish_action", action_id, "refused", error)
        raise ApiError(409, "refused", error)
    await db.a("finish_action", action_id, "ok")
    jobs.kick("tickets", "status")
    result = {k: v for k, v in res.items() if isinstance(v, (int, float, bool, str))} if isinstance(res, dict) else {}
    return web.json_response({"ok": True, "result": result})


# ---- library ----------------------------------------------------------------------------------------------------------
@write()
async def post_apply(request, body, user):
    db, n8n, crew, jobs, hub, clock = ctx(request)
    if body.get("confirm") is not True:
        raise ApiError(400, "confirm_required", "Apply needs confirm: true from the dialog")
    force = as_bool(body.get("force"), "force")
    if force and body.get("forceConfirm") is not True:
        raise ApiError(400, "force_confirm_required", "Apply anyway needs a second confirm (forceConfirm: true)")
    ids = body.get("ids")
    lib = await db.a("get_library")
    if ids == "safe":
        ids = safe_ids(lib)
        if not ids:
            raise ApiError(409, "nothing_to_apply", "No safe findings left to apply")
    elif ids != "all":
        if not isinstance(ids, list) or not ids or len(ids) > 1000 or not all(
                isinstance(i, str) and 0 < len(i) <= 80 for i in ids):
            raise ApiError(400, "invalid", 'ids must be "all", "safe" or a list of finding ids')
        ids = list(dict.fromkeys(ids))
    if not n8n.base:
        raise ApiError(503, "n8n_not_configured", n8n.config_error or "n8n_url is not set")
    if jobs.apply_running:
        raise ApiError(409, "busy", "An apply is already running")
    action_id, existing = await db.a("begin_action", user, "library.apply", "all" if ids == "all" else f"{len(ids)} ids",
                                     "force" if force else "normal", body["clientId"])
    if existing is not None:
        return duplicate(existing)
    targets = [f["id"] for f in (lib or {}).get("findings") or [] if not f["applied"]] if ids == "all" else ids
    job = jobs.start_apply(ids, force, user, action_id, targets)
    return web.json_response({"job": _job_view(job)}, status=202)


# ---- "Try again" and library scan -----------------------------------------------------------------------------------
@write()
async def post_rerun(request, body, user):
    """{agentId, confirm: true} -> 202 {job}. Only agents that n8n's status marks rerun: true (the cached copy)."""
    db, n8n, crew, jobs, hub, clock = ctx(request)
    agent_id = body.get("agentId")
    if not isinstance(agent_id, str) or not agents_mod.AGENT_ID_RE.match(agent_id):
        raise ApiError(400, "invalid", "agentId is required")
    status, _ = await cached_status(db)
    agent = agents_mod.find_agent(status, agent_id)
    if agent is None:
        raise ApiError(404, "not_found", "No such agent")
    plan = agents_mod.rerun_plan(agent)
    if plan is None:
        raise ApiError(409, "not_rerunnable", "This agent can't be run again from The Bridge")
    if body.get("confirm") is not True:
        raise ApiError(400, "confirm_required", plan[1])
    if not n8n.base:
        raise ApiError(503, "n8n_not_configured", n8n.config_error or "n8n_url is not set")
    kind = "library.scan" if plan[0] == "scan" else "agent.rerun"
    if any(j["state"] == "running" and j["kind"] == kind and j["detail"].get("agentId") == agent_id
           for j in jobs.jobs.values()):
        raise ApiError(409, "busy", "This agent is already running again")
    action_id, existing = await db.a("begin_action", user, kind, agent_id, plan[0], body["clientId"])
    if existing is not None:
        return duplicate(existing)
    if plan[0] == "scan":
        job = jobs.start_scan(24, user, action_id, agent_id)
    else:
        job = jobs.start_rerun(agent, user, action_id, plan[1])
    return web.json_response({"job": _job_view(job)}, status=202)


@write()
async def post_library_scan(request, body, user):
    """{hours?: 1-168 (default 24)} -> 202 {job}. Read-only on the n8n side, allowed any time; no confirm."""
    db, n8n, crew, jobs, hub, clock = ctx(request)
    hours = body.get("hours", 24)
    if not isinstance(hours, int) or isinstance(hours, bool) or not 1 <= hours <= 168:
        raise ApiError(400, "invalid", "hours must be 1-168")
    if not n8n.base:
        raise ApiError(503, "n8n_not_configured", n8n.config_error or "n8n_url is not set")
    action_id, existing = await db.a("begin_action", user, "library.scan", "library", f"{hours} h", body["clientId"])
    if existing is not None:
        return duplicate(existing)
    job = jobs.start_scan(hours, user, action_id)
    return web.json_response({"job": _job_view(job)}, status=202)


# ---- Claude (crew_hub) ------------------------------------------------------------------------------------------------
def _claude_args(action, args):
    if not isinstance(args, dict):
        raise ApiError(400, "invalid", "args must be an object")
    session = args.get("session")
    if not isinstance(session, str) or not SESSION_RE.match(session):
        raise ApiError(400, "invalid", "args.session is required")
    out = {"session": session}
    if action == "send":
        out["text"] = clean_text(args.get("text"), "text", USER_TEXT_MAX, required=True)
        out["urgent"] = as_bool(args.get("urgent"), "urgent")
    elif action == "answer":
        req = args.get("requestId")
        if not isinstance(req, str) or not 0 < len(req) <= 128:
            raise ApiError(400, "invalid", "args.requestId is required")
        out["requestId"] = req
        if args.get("decision") is not None:
            if args["decision"] not in ("allow", "deny"):
                raise ApiError(400, "invalid", "decision must be allow or deny")
            out["decision"] = args["decision"]
        msg = clean_text(args.get("message"), "message", 2000)
        if msg:
            out["message"] = msg
        answers = args.get("answers")
        if answers is not None:
            if not isinstance(answers, dict) or len(answers) > 20 or not all(
                    isinstance(k, str) and isinstance(v, str) for k, v in answers.items()):
                raise ApiError(400, "invalid", "answers must map question text to a label")
            out["answers"] = {k[:2000]: v[:200] for k, v in answers.items()}
    return out


@write()
async def post_claude_command(request, body, user):
    db, n8n, crew, jobs, hub, clock = ctx(request)
    machine = body.get("machine")
    action = body.get("action")
    if not isinstance(machine, str) or not MACHINE_RE.match(machine):
        raise ApiError(400, "invalid", "machine is required")
    if action not in COMMAND_ACTIONS:
        raise ApiError(400, "invalid", "action must be send, answer, interrupt or stop")
    args = _claude_args(action, body.get("args"))
    if action in CONFIRM_ACTIONS and body.get("confirm") is not True:
        raise ApiError(400, "confirm_required", f"{action} needs confirm: true from the dialog")
    s = crew.find_session(machine, args["session"])
    if s is not None and action in CONFIRM_ACTIONS and action not in (s.get("controls") or []):
        raise ApiError(400, "not_allowed", f"This session does not allow {action}")
    action_id, existing = await db.a("begin_action", user, f"claude.{action}", f"{machine}/{args['session']}",
                                     action, body["clientId"])
    if existing is not None:
        return duplicate(existing)
    try:
        res = await crew.command(machine, action, args)
    except CrewError as err:
        await db.a("finish_action", action_id, "timeout" if err.code == "timeout" else "error", err.code)
        raise ApiError(503, "crew_" + err.code, "crew_hub is not reachable" if err.code == "disconnected"
                       else f"crew_hub: {err.code}") from None
    ok = bool(res.get("ok"))
    await db.a("finish_action", action_id, "ok" if ok else "refused", None if ok else str(res.get("error")))
    out = {"ok": ok}
    if not ok:
        out["error"] = str(res.get("error") or "failed")[:60]
    if res.get("detail"):
        out["detail"] = str(res["detail"])[:200]
    return web.json_response(out)


@write()
async def post_claude_dismiss(request, body, user):
    db, n8n, crew, jobs, hub, clock = ctx(request)
    machine, session = body.get("machine"), body.get("session")
    if not isinstance(machine, str) or not MACHINE_RE.match(machine) or not isinstance(session, str) \
            or not SESSION_RE.match(session):
        raise ApiError(400, "invalid", "machine and session are required")
    agents = body.get("agents") or []
    if not isinstance(agents, list) or len(agents) > 200 or not all(
            isinstance(a, str) and 0 < len(a) <= 128 for a in agents):
        raise ApiError(400, "invalid", "agents must be a list of ids")
    sd = as_bool(body.get("sessionDismissed"), "sessionDismissed")
    action_id, existing = await db.a("begin_action", user, "claude.dismiss", f"{machine}/{session}",
                                     f"{len(agents)} agents" + (", session" if sd else ""), body["clientId"])
    if existing is not None:
        return duplicate(existing)
    try:
        res = await crew.dismiss(machine, session, agents, sd)
    except CrewError as err:
        await db.a("finish_action", action_id, "error", err.code)
        raise ApiError(503, "crew_" + err.code, "crew_hub is not reachable") from None
    ok = bool(res.get("ok"))
    await db.a("finish_action", action_id, "ok" if ok else "refused", None if ok else str(res.get("error")))
    return web.json_response({"ok": ok, **({} if ok else {"error": str(res.get("error"))[:60]})})


# ---- settings ---------------------------------------------------------------------------------------------------------
@write()
async def post_settings(request, body, user):
    db = ctx(request)[0]
    changed = []
    if "theme" in body:
        if body["theme"] not in THEMES:
            raise ApiError(400, "invalid", "theme must be follow_ha, night or day")
        changed.append("theme")
    if "replayAfterHours" in body:
        v = body["replayAfterHours"]
        if v is not None and (not isinstance(v, int) or isinstance(v, bool) or not 1 <= v <= 720):
            raise ApiError(400, "invalid", "replayAfterHours must be 1-720 or null")
        changed.append("replayAfterHours")
    if not changed:
        raise ApiError(400, "invalid", "Nothing to change")
    action_id, existing = await db.a("begin_action", user, "settings.update", user.id, ",".join(changed),
                                     body["clientId"])
    if existing is not None:
        return duplicate(existing)
    if "theme" in body:
        cur = await db.a("get_setting", f"user:{user.id}", {})
        await db.a("put_setting", f"user:{user.id}", {**cur, "theme": body["theme"]})
    if "replayAfterHours" in body:
        await db.a("put_setting", "replay_after_hours", body["replayAfterHours"])
    await db.a("finish_action", action_id, "ok")
    out = await user_settings(db, user)
    ctx(request)[4].publish("settings", {"userId": user.id, **out})  # other devices of this user follow
    return web.json_response(out)


def register(app):
    r = app.router
    r.add_get("/api/state", get_state)
    r.add_get("/api/overview", get_overview)
    r.add_get("/api/crew", get_crew)
    r.add_get("/api/threads", get_threads)
    r.add_post("/api/threads", post_threads)
    r.add_patch("/api/threads/{tid}", patch_thread)
    r.add_get("/api/threads/{tid}/messages", get_messages)
    r.add_post("/api/threads/{tid}/messages", post_message)
    r.add_post("/api/threads/{tid}/messages/{mid}/retry", retry_message)
    r.add_post("/api/read", post_read)
    r.add_get("/api/tickets", get_tickets)
    r.add_post("/api/tickets", post_ticket)
    r.add_post("/api/tickets/{id}", update_ticket)
    r.add_get("/api/tasks", get_tasks)
    r.add_get("/api/tasks/runs", get_runs)
    r.add_get("/api/tasks/run", get_run)
    r.add_get("/api/agents/output", get_agent_output)
    r.add_get("/api/agents/{aid}/runs/{rid}", get_agent_run)
    r.add_get("/api/agents/{aid}/output", get_agent_output2)
    r.add_get("/api/sessions/{machine}/{session}/conversation", get_conversation)
    r.add_post("/api/sessions/{machine}/{session}/conversation/close", post_conversation_close)
    r.add_get("/api/library", get_library)
    r.add_get("/api/library/precheck", get_precheck)
    r.add_post("/api/library/apply", post_apply)
    r.add_post("/api/library/scan", post_library_scan)
    r.add_post("/api/agents/rerun", post_rerun)
    r.add_get("/api/jobs/{jid}", get_job)
    r.add_get("/api/inbox", get_inbox)
    r.add_post("/api/claude/command", post_claude_command)
    r.add_post("/api/claude/dismiss", post_claude_dismiss)
    r.add_get("/api/settings", get_settings)
    r.add_post("/api/settings", post_settings)
