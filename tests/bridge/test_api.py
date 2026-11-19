"""The page <-> server API contract (docs/BRIDGE.md §6), SSE, chat jobs, tickets, library apply, Claude commands."""

import asyncio
import json

import pytest

from bridge.app import api
from conftest import ADMIN, W, cid, wait_for
from fake_n8n import FIX


class SSE:
    """Reads api/events in the background and collects (event, data) pairs."""

    def __init__(self, bridge, headers=None):
        self.bridge = bridge
        self.headers = ADMIN if headers is None else headers
        self.events = []
        self.comments = 0
        self.resp = None
        self.task = None

    async def __aenter__(self):
        self.resp = await self.bridge.client.get("/api/events", headers=self.headers)
        assert self.resp.status == 200
        assert self.resp.headers["Content-Type"].startswith("text/event-stream")
        assert self.resp.headers["X-Accel-Buffering"] == "no"
        self.task = asyncio.ensure_future(self._read())
        await wait_for(lambda: self.events)
        return self

    async def _read(self):
        event = None
        async for raw in self.resp.content:
            line = raw.decode().rstrip("\n")
            if line.startswith(":"):
                self.comments += 1
            elif line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: "):
                self.events.append((event, json.loads(line[6:])))

    def of(self, name):
        return [d for e, d in self.events if e == name]

    async def __aexit__(self, *exc):
        self.task.cancel()
        self.resp.close()


async def open_thread(bridge, kind="n8n", target="crew"):
    r, d = await bridge.post("/api/threads", {"kind": kind, "target": target, "clientId": cid()})
    assert r.status in (200, 201), d
    return d["thread"]


async def send(bridge, tid, text="hello", **extra):
    r, d = await bridge.post(f"/api/threads/{tid}/messages", {"text": text, "clientId": cid(), **extra})
    assert r.status == 202, d
    return d["message"]


async def state_of(bridge, mid):
    return bridge.db.get_message(mid)["state"]


# ---- read endpoints --------------------------------------------------------------------------------------------------
async def test_state_shape(bridge, fake_n8n):
    r, d = await bridge.get("/api/state")
    assert set(d) >= {"version", "user", "health", "unread", "settings"}
    assert d["version"] == api.APP_VERSION
    assert d["user"] == {"id": "u-alex", "name": "alex", "display": "Alex", "canWrite": True}
    assert set(d["health"]["n8n"]) >= {"ok", "at", "error"} and d["health"]["crew"]["connected"] is True
    assert set(d["unread"]) == {"threads", "inbox", "tickets", "library", "ticketsNeedYou"}
    assert d["settings"]["theme"] == "follow_ha"


async def test_overview_crew_tasks_after_polls(bridge, fake_n8n):
    for p in bridge.jobs.pollers.values():
        assert await p.tick() == "ok"
    r, d = await bridge.get("/api/overview")
    assert r.status == 200
    waiting = d["needsYou"]["claude"]
    assert {w["status"] for w in waiting} == {"waiting"} and len(waiting) == 2
    assert d["needsYou"]["problems"] == [] and d["needsYou"]["agents"] == []
    assert d["needsYou"]["tickets"] == [1]  # ticket 1 has a "NAS agent" comment
    assert d["needsYou"]["ticketsNeedYou"] == []
    assert d["needsYou"]["findings"] == 12
    assert d["running"]["n8n"] == [] and len(d["running"]["claude"]) == 3
    r, d = await bridge.get("/api/crew")
    assert "nas" in d["machines"] and d["connected"] is True
    assert {"agents", "services", "system"} == set(d["agents"])
    r, d = await bridge.get("/api/tasks")
    assert d["library"]["total"] == 25 and d["library"]["unapplied"] == 12
    assert d["library"]["agent"]["id"] == "svc:media-agent"
    assert any(w["workflowId"] == "wfExample0000007" for w in d["workflows"])
    assert d["plans"]
    r, d = await bridge.get("/api/inbox")
    assert [i["id"] for i in d["items"]] == [4, 3, 2, 1]
    r, d = await bridge.get("/api/inbox?before=3")
    assert [i["id"] for i in d["items"]] == [2, 1]
    r, d = await bridge.get("/api/library")
    assert d["report"]["date"] == "2026-11-18" and len(d["safeIds"]) == 12
    assert d["quietHours"] == {"from": "17:00", "to": "01:00"}
    r, d = await bridge.get("/api/state")
    assert d["unread"] == {"threads": 0, "inbox": 4, "tickets": 1, "library": 12, "ticketsNeedYou": 0}
    r, _ = await bridge.post("/api/read", {"scope": "inbox"})
    assert r.status == 200
    r, d = await bridge.get("/api/state")
    assert d["unread"]["inbox"] == 0


async def test_tickets_list_matching(bridge, fake_n8n):
    await bridge.jobs.pollers["status"].tick()
    await bridge.jobs.pollers["tickets"].tick()
    r, d = await bridge.get("/api/tickets")
    t = {x["id"]: x for x in d["tickets"]}
    assert t[2]["agentMatch"] == "svc:media-agent"  # "Jellyfin library agent"
    assert t[1]["agentMatch"] is None and t[3]["agentMatch"] is None
    assert t[1]["comments"][0]["author"] == "NAS agent"
    assert d["limits"] == {"title": 120, "details": 4000, "comment": 4000, "resolution": 2000}
    r, d = await bridge.get("/api/tickets?status=open")
    assert d["tickets"] == []
    r, d = await bridge.get("/api/tickets?status=<b>")
    assert r.status == 400


async def test_runs_run_and_output_are_cached(bridge, fake_n8n):
    r, d = await bridge.get("/api/tasks/runs?workflowId=wfExample0000007")
    assert r.status == 200 and d["runs"][0]["id"] == "140" and d["cached"] is False
    r, d = await bridge.get("/api/tasks/runs?workflowId=wfExample0000007")
    assert d["cached"] is True and fake_n8n.count("bridge/runs") == 1
    assert fake_n8n.requests[-1]["query"] == {"workflowId": "wfExample0000007"}
    bridge.clock.advance(61)
    fake_n8n.fail["bridge/runs"] = 500
    r, d = await bridge.get("/api/tasks/runs?workflowId=wfExample0000007")
    assert r.status == 200 and d["stale"] is True and d["runs"]  # last good copy, with the error
    r, d = await bridge.get("/api/tasks/runs?workflowId=svc:media-agent")
    assert r.status == 400 and d["code"] == "no_runs"
    r, d = await bridge.get("/api/tasks/run?id=140")
    assert d["run"]["summary"].startswith("Attention") and d["run"]["status"] == "success"
    r, d = await bridge.get("/api/tasks/run?id=140")
    assert d["cached"] is True
    r, d = await bridge.get("/api/tasks/run?id=999")
    assert r.status == 502
    r, d = await bridge.get("/api/tasks/run?id=1;drop")
    assert r.status == 400
    r, d = await bridge.get("/api/agents/output?id=svc:media-agent")
    assert d["output"]["text"].startswith("State: idle")
    r, d = await bridge.get("/api/agents/output?id=n8n:abc")
    assert r.status == 400


async def test_precheck(bridge, fake_n8n):
    fake_n8n.precheck = {"playing": 1, "sessions": 1, "titles": ["Inside Out"], "evening": True, "busy": False,
                         "undo": True, "undo_note": "kept"}
    r, d = await bridge.get("/api/library/precheck")
    assert r.status == 200 and d["precheck"]["titles"] == ["Inside Out"]
    fake_n8n.precheck = {"playing": None, "sessions": None, "titles": [], "error": "Jellyfin unreachable"}
    r, d = await bridge.get("/api/library/precheck")
    assert d["precheck"]["playing"] is None and d["precheck"]["error"]


async def test_settings_per_user(bridge):
    r, d = await bridge.post("/api/settings", {"theme": "night", "clientId": cid()})
    assert r.status == 200 and d["theme"] == "night"
    other = {**ADMIN, "X-Remote-User-Id": "u-other"}
    r, d = await bridge.get("/api/settings", headers=other)
    assert d["theme"] == "follow_ha"
    r, d = await bridge.get("/api/settings")
    assert d["theme"] == "night" and d["chatRetentionDays"] == 90
    r, d = await bridge.post("/api/settings", {"theme": "dark", "clientId": cid()})
    assert r.status == 400
    acts = await bridge.db.a("list_actions")
    assert acts[0]["kind"] == "settings.update" and acts[0]["user_name"] == "Alex"


# ---- threads and chat ------------------------------------------------------------------------------------------------
async def test_threads_open_reuse_rename_archive(bridge, fake_n8n, fake_core):
    await bridge.jobs.pollers["status"].tick()
    t = await open_thread(bridge)
    again = await open_thread(bridge)
    assert again["id"] == t["id"] and t["targetName"] == "Crew"
    agent = await open_thread(bridge, "n8n", "svc:media-agent")
    assert agent["targetName"] == "Library agent (Jellyfin)"
    r, d = await bridge.post("/api/threads", {"kind": "n8n", "target": "svc:llm", "clientId": cid()})
    assert r.status == 400 and d["code"] == "not_chat"
    r, d = await bridge.post("/api/threads", {"kind": "claude", "target": "nas/nope", "clientId": cid()})
    assert r.status == 400 and d["code"] == "unknown_session"
    claude = await open_thread(bridge, "claude", "nas/8c1f2a4e-3b7d-4e19-9a61-0f2d5c7b9e10")
    assert claude["targetName"] == next(s["name"] for s in fake_core.machines["nas"]["sessions"]
                                         if s["id"] == "8c1f2a4e-3b7d-4e19-9a61-0f2d5c7b9e10")
    r, d = await bridge.post(f"/api/threads/{t['id']}", {"title": "Ops <b>", "clientId": cid()}, method="PATCH")
    assert r.status == 200 and d["thread"]["title"] == "Ops <b>"  # text, stored as is, never rendered as HTML
    r, d = await bridge.post(f"/api/threads/{t['id']}", {"archived": True, "clientId": cid()}, method="PATCH")
    assert d["thread"]["archived"] is True
    r, d = await bridge.get("/api/threads")
    assert t["id"] not in [x["id"] for x in d["threads"]]
    r, d = await bridge.get("/api/threads?archived=1")
    assert [x["id"] for x in d["threads"]] == [t["id"]]
    fresh = await open_thread(bridge)
    assert fresh["id"] != t["id"]  # archived threads are not reused
    r, d = await bridge.post(f"/api/threads/{t['id']}/messages", {"text": "x", "clientId": cid()})
    assert r.status == 409


async def test_chat_job_lifecycle_and_sse(bridge, fake_n8n):
    await bridge.jobs.pollers["status"].tick()
    t = await open_thread(bridge, "n8n", "svc:media-agent")
    async with SSE(bridge) as sse:
        assert sse.events[0][0] == "hello"
        hello = sse.events[0][1]
        assert hello["version"] == api.APP_VERSION and hello["user"]["id"] == "u-alex" and hello["serverTime"]
        fake_n8n.chat_reply = "There are 128 trailer fixes ready."
        m = await send(bridge, t["id"], "How many fixes?")
        assert m["state"] == "queued" and m["authorName"] == "Alex"
        await wait_for(lambda: bridge.db.get_message(m["id"])["state"] == "replied")
        await wait_for(lambda: len(sse.of("message")) >= 4)
        states = [d["state"] for d in sse.of("message") if d["id"] == m["id"]]
        assert states == ["queued", "sending", "replied"]
        reply = [d for d in sse.of("message") if d["role"] == "agent"][0]
        assert reply["text"] == "There are 128 trailer fixes ready." and reply["source"] == "n8n"
        assert sse.of("thread")
    post = [p for p in fake_n8n.posts if p["path"] == "bridge/chat"][-1]
    th = bridge.db.get_thread(t["id"])
    assert post["body"] == {"query": "How many fixes?", "conversation_id": th["n8n_conversation_id"],
                            "user_name": "Alex", "agent_id": "svc:media-agent"}
    r, d = await bridge.get(f"/api/threads/{t['id']}/messages")
    assert [x["role"] for x in d["messages"]] == ["user", "agent"]  # newest last
    assert d["messages"][0]["durationMs"] is not None
    r, d = await bridge.get("/api/threads")
    assert d["threads"][0]["unread"] == 1
    r, _ = await bridge.post("/api/read", {"scope": f"thread:{t['id']}"})
    r, d = await bridge.get("/api/threads")
    assert d["threads"][0]["unread"] == 0
    # crew thread: no agent_id
    crew = await open_thread(bridge)
    m = await send(bridge, crew["id"], "hi crew")
    await wait_for(lambda: bridge.db.get_message(m["id"])["state"] == "replied")
    assert "agent_id" not in [p for p in fake_n8n.posts if p["path"] == "bridge/chat"][-1]["body"]
    acts = [a for a in await bridge.db.a("list_actions") if a["kind"] == "chat.send"]
    assert len(acts) == 2 and all(a["user_id"] == "u-alex" and "hello" not in (a["summary"] or "") for a in acts)


async def test_chat_dedupe(bridge, fake_n8n):
    t = await open_thread(bridge)
    c = cid()
    r1, d1 = await bridge.post(f"/api/threads/{t['id']}/messages", {"text": "once", "clientId": c})
    r2, d2 = await bridge.post(f"/api/threads/{t['id']}/messages", {"text": "once", "clientId": c})
    assert r1.status == r2.status == 202 and d1["message"]["id"] == d2["message"]["id"] and d2["duplicate"]
    await wait_for(lambda: bridge.db.get_message(d1["message"]["id"])["state"] == "replied")
    assert fake_n8n.count("bridge/chat") == 1


async def test_chat_timeout_130s_then_retry(bridge, fake_n8n):
    t = await open_thread(bridge)
    fake_n8n.delay_once["bridge/chat"] = [130]  # 1.3 s real with the fast clock; the server gives up at 120
    m = await send(bridge, t["id"], "slow one")
    await wait_for(lambda: bridge.db.get_message(m["id"])["state"] == "sending")
    second = await send(bridge, t["id"], "behind it")
    assert bridge.db.get_message(second["id"])["state"] == "queued"  # one in flight per thread
    await wait_for(lambda: bridge.db.get_message(m["id"])["state"] == "timeout", timeout=4)
    row = bridge.db.get_message(m["id"])
    assert row["error"] == "No reply after 2 min" and 115_000 <= row["duration_ms"] <= 135_000  # logical ms
    await wait_for(lambda: bridge.db.get_message(second["id"])["state"] == "replied", timeout=4)
    assert bridge.db.get_message(m["id"])["state"] == "timeout"  # never resent automatically
    assert fake_n8n.count("bridge/chat") == 2
    r, d = await bridge.post(f"/api/threads/{t['id']}/messages/{second['id']}/retry", {"clientId": cid()})
    assert r.status == 409 and d["code"] == "not_retryable"
    r, d = await bridge.post(f"/api/threads/{t['id']}/messages/{m['id']}/retry", {"clientId": cid()})
    assert r.status == 202 and d["message"]["state"] == "queued"
    await wait_for(lambda: bridge.db.get_message(m["id"])["state"] == "replied")
    assert fake_n8n.count("bridge/chat") == 3


async def test_chat_failure_and_key_error(bridge, fake_n8n):
    t = await open_thread(bridge)
    fake_n8n.fail["bridge/chat"] = 401
    m = await send(bridge, t["id"])
    await wait_for(lambda: bridge.db.get_message(m["id"])["state"] == "failed")
    assert bridge.db.get_message(m["id"])["error"] == "n8n key missing or wrong"


async def test_queued_message_dropped_after_10_min(bridge, fake_n8n):
    t = await open_thread(bridge)
    fake_n8n.delay["bridge/chat"] = 100
    m1 = await send(bridge, t["id"], "first")
    await wait_for(lambda: bridge.db.get_message(m1["id"])["state"] == "sending")
    m2 = await send(bridge, t["id"], "second")
    bridge.clock.advance(11 * 60)
    await wait_for(lambda: bridge.db.get_message(m2["id"])["state"] == "dropped", timeout=4)
    assert [p["body"]["query"] for p in fake_n8n.posts if p["path"] == "bridge/chat"] == ["first"]


async def test_message_limits(bridge):
    t = await open_thread(bridge)
    r, d = await bridge.post(f"/api/threads/{t['id']}/messages", {"text": "x" * 4001, "clientId": cid()})
    assert r.status == 400 and d["code"] == "too_long"
    r, d = await bridge.post(f"/api/threads/{t['id']}/messages", {"text": "   ", "clientId": cid()})
    assert r.status == 400 and d["code"] == "missing"
    r, d = await bridge.post(f"/api/threads/{t['id']}/messages", {"text": 5, "clientId": cid()})
    assert r.status == 400
    r, d = await bridge.post("/api/threads/th_nope/messages", {"text": "x", "clientId": cid()})
    assert r.status == 404


async def test_restart_marks_sending_unknown(bridge_factory, fake_n8n, tmp_path):
    b = await bridge_factory(n8n=fake_n8n)
    t = await open_thread(b)
    fake_n8n.delay["bridge/chat"] = 100
    m = await send(b, t["id"])
    await wait_for(lambda: b.db.get_message(m["id"])["state"] == "sending")
    await b.client.close()  # the app stops mid-call
    b2 = await bridge_factory(n8n=fake_n8n)
    r, d = await b2.get(f"/api/threads/{t['id']}/messages")
    assert d["messages"][0]["state"] == "unknown" and "restarted" in d["messages"][0]["error"]
    assert (b2.db.path.parent / "snapshot" / "bridge.db").exists()  # snapshot written on the clean stop


async def test_replay_after_gap(bridge, fake_n8n):
    t = await open_thread(bridge)
    m = await send(bridge, t["id"], "first question")
    await wait_for(lambda: bridge.db.get_message(m["id"])["state"] == "replied")
    r, d = await bridge.post("/api/settings", {"replayAfterHours": 6, "clientId": cid()})
    assert d["replayAfterHours"] == 6
    bridge.clock.advance(7 * 3600)
    m = await send(bridge, t["id"], "follow up")
    await wait_for(lambda: bridge.db.get_message(m["id"])["state"] == "replied")
    q = [p for p in fake_n8n.posts if p["path"] == "bridge/chat"][-1]["body"]["query"]
    assert "Earlier in this chat" in q and "first question" in q and q.endswith("follow up")


# ---- Claude ----------------------------------------------------------------------------------------------------------
SID = "8c1f2a4e-3b7d-4e19-9a61-0f2d5c7b9e10"
BG = "b8e41f03"


def bg_session(bridge):
    return next(s["id"] for s in bridge.crew.machines["nas"]["sessions"] if s["id"].startswith(BG))


async def test_claude_thread_send_is_one_way(bridge, fake_core):
    t = await open_thread(bridge, "claude", f"nas/{SID}")
    m = await send(bridge, t["id"], "please check", urgent=True)
    await wait_for(lambda: bridge.db.get_message(m["id"])["state"] == "delivered")
    cmd = fake_core.commands()[-1]
    assert cmd["machine"] == "nas" and cmd["action"] == "send"
    assert cmd["args"] == {"session": SID, "text": "please check", "urgent": True}
    fake_core.command_result = lambda f: {"ok": False, "error": "no_ack"}
    m = await send(bridge, t["id"], "again")
    await wait_for(lambda: bridge.db.get_message(m["id"])["state"] == "failed")
    assert bridge.db.get_message(m["id"])["error"] == "no_ack"


async def test_claude_session_idle_writes_no_status_row(bridge, fake_core):
    """0.2.0: a Claude thread is a pointer; Claude's text (even its summary) is never stored."""
    t = await open_thread(bridge, "claude", f"nas/{SID}")
    nas = json.loads(json.dumps(fake_core.machines["nas"]))
    s = next(x for x in nas["sessions"] if x["id"] == SID)
    s["status"] = "idle"
    s["summary"] = "Done: all 7 fixes accepted. " + "x" * 300
    await fake_core.push_machine("nas", nas)
    await wait_for(lambda: bridge.crew.find_session("nas", SID)["status"] == "idle")
    await asyncio.sleep(0.1)
    assert bridge.db.list_messages(t["id"]) == []


async def test_claude_command_confirms_and_controls(bridge, fake_core):
    bg = bg_session(bridge)
    body = {"machine": "nas", "action": "stop", "args": {"session": bg}, "clientId": cid()}
    r, d = await bridge.post("/api/claude/command", body)
    assert r.status == 400 and d["code"] == "confirm_required"
    r, d = await bridge.post("/api/claude/command", {**body, "action": "interrupt", "clientId": cid()})
    assert r.status == 400 and d["code"] == "confirm_required"
    assert not fake_core.commands()
    r, d = await bridge.post("/api/claude/command", {**body, "confirm": True, "clientId": cid()})
    assert r.status == 200 and d == {"ok": True}
    r, d = await bridge.post("/api/claude/command", {"machine": "nas", "action": "stop", "args": {"session": SID},
                                                     "confirm": True, "clientId": cid()})
    assert r.status == 400 and d["code"] == "not_allowed"  # interactive session: no stop control
    r, d = await bridge.post("/api/claude/command", {"machine": "nas", "action": "start", "args": {"session": SID},
                                                     "clientId": cid()})
    assert r.status == 400  # start stays in the Lovelace card
    r, d = await bridge.post("/api/claude/command", {
        "machine": "nas", "action": "answer", "clientId": cid(),
        "args": {"session": SID, "requestId": "r1", "decision": "allow", "message": "ok"}})
    assert r.status == 200 and fake_core.commands()[-1]["args"]["decision"] == "allow"
    r, d = await bridge.post("/api/claude/command", {"machine": "mars", "action": "send", "clientId": cid(),
                                                     "args": {"session": SID, "text": "hi"}})
    assert d == {"ok": False, "error": "not_configured"}
    r, d = await bridge.post("/api/claude/dismiss", {"machine": "nas", "session": SID, "agents": ["a1"],
                                                     "clientId": cid()})
    assert d == {"ok": True}
    kinds = [a["kind"] for a in await bridge.db.a("list_actions")]
    assert {"claude.stop", "claude.answer", "claude.send", "claude.dismiss"} <= set(kinds)


# ---- tickets ---------------------------------------------------------------------------------------------------------
async def test_ticket_create_update_and_limits(bridge, fake_n8n):
    await bridge.jobs.pollers["status"].tick()
    for field, n in (("title", 121), ("details", 4001)):
        body = {"title": "ok", "type": "bug", "clientId": cid(), field: "x" * n}
        r, d = await bridge.post("/api/tickets", body)
        assert r.status == 400 and d["code"] == "too_long", field
    r, d = await bridge.post("/api/tickets", {"type": "bug", "clientId": cid()})
    assert r.status == 400 and d["code"] == "missing"
    r, d = await bridge.post("/api/tickets", {"title": "t", "agentId": "bad id", "clientId": cid()})
    assert r.status == 400
    assert not [p for p in fake_n8n.posts if p["path"] == "agent-hub/ticket"]  # limits are checked first
    c = cid()
    r, d = await bridge.post("/api/tickets", {"title": "Group trailers", "details": "please", "type": "feature",
                                              "agentId": "svc:media-agent", "clientId": c})
    assert r.status == 200 and d["ok"] is True
    body = [p for p in fake_n8n.posts if p["path"] == "agent-hub/ticket"][-1]["body"]
    assert body == {"action": "create", "type": "feature", "title": "Group trailers", "details": "please",
                    "agent": "Library agent (Jellyfin)", "agent_id": "svc:media-agent",
                    "source": "The Bridge · Alex"}
    r, d = await bridge.post("/api/tickets", {"title": "Group trailers", "clientId": c})
    assert d["duplicate"] is True and d["outcome"] == "ok"
    assert len([p for p in fake_n8n.posts if p["path"] == "agent-hub/ticket"]) == 1
    for field, n in (("comment", 4001), ("resolution", 2001)):
        r, d = await bridge.post("/api/tickets/1", {field: "x" * n, "clientId": cid()})
        assert r.status == 400 and d["code"] == "too_long", field
    r, d = await bridge.post("/api/tickets/1", {"clientId": cid()})
    assert r.status == 400
    r, d = await bridge.post("/api/tickets/1", {"comment": "thanks", "status": "closed", "clientId": cid()})
    assert r.status == 200
    body = [p for p in fake_n8n.posts if p["path"] == "agent-hub/ticket"][-1]["body"]
    assert body["action"] == "update" and body["id"] == 1 and body["author"] == "Alex" and body["comment"] == "thanks"
    assert bridge.jobs.pollers["tickets"]._kick.is_set()  # "right after our ticket writes" (pollers off: tick by hand)
    await bridge.jobs.pollers["tickets"].tick()
    r, d = await bridge.get("/api/tickets")
    t1 = next(t for t in d["tickets"] if t["id"] == 1)
    assert t1["comments"][-1] == {"at": "2026-11-18T18:00:00.000Z", "author": "Alex", "text": "thanks"}
    new = next(t for t in d["tickets"] if t["title"] == "Group trailers")
    assert new["agentId"] == new["agentMatch"] == "svc:media-agent"
    r, d = await bridge.post("/api/tickets/99", {"comment": "x", "clientId": cid()})
    assert r.status == 409 and d["code"] == "refused"
    kinds = [(a["kind"], a["outcome"]) for a in await bridge.db.a("list_actions")]
    assert ("ticket.create", "ok") in kinds and ("ticket.update", "ok") in kinds and ("ticket.update", "refused") in kinds


def engineer_tickets(fake_n8n):
    fake_n8n.tickets[:0] = json.loads((FIX / "tickets-engineer.json").read_text())


async def test_engineer_tickets_need_you(bridge, fake_n8n):
    engineer_tickets(fake_n8n)
    await bridge.jobs.pollers["tickets"].tick()
    r, d = await bridge.get("/api/tickets")
    t = {x["id"]: x for x in d["tickets"]}
    assert t[11]["group"] == t[12]["group"] == "needs" and t[1]["group"] == "done"
    assert set(d["newAgentComments"]) == {1, 11, 12}  # "Engineer" comments are agent replies, like "NAS agent"
    r, d = await bridge.get("/api/tickets?status=awaiting_approval")
    assert [x["id"] for x in d["tickets"]] == [12]
    r, d = await bridge.get("/api/overview")
    need = d["needsYou"]["ticketsNeedYou"]
    assert [x["id"] for x in need] == [11, 12]  # oldest update first
    assert need[0]["status"] == "needs_isaac" and need[0]["agentId"] == "svc:media-agent"
    assert need[0]["last"]["author"] == "Engineer" and need[0]["last"]["text"].startswith("Two ways")
    assert need[1]["status"] == "awaiting_approval" and need[1]["last"]["text"].startswith("Fix ready")
    r, d = await bridge.get("/api/state")
    assert d["unread"]["ticketsNeedYou"] == 2


async def test_ticket_status_values(bridge, fake_n8n):
    engineer_tickets(fake_n8n)
    for st in ("open", "in_progress", "needs_isaac", "awaiting_approval", "done", "closed"):
        r, d = await bridge.post("/api/tickets/11", {"status": st, "clientId": cid()})
        assert r.status == 200, st
        assert [p for p in fake_n8n.posts if p["path"] == "agent-hub/ticket"][-1]["body"]["status"] == st
    for st in ("resolved", "waiting", "Open", 3):
        r, d = await bridge.post("/api/tickets/11", {"status": st, "clientId": cid()})
        assert r.status == 400 and d["code"] == "invalid", st


async def test_ticket_approve(bridge, fake_n8n):
    engineer_tickets(fake_n8n)
    await bridge.jobs.pollers["tickets"].tick()
    posts = lambda: [p for p in fake_n8n.posts if p["path"] == "agent-hub/ticket"]  # noqa: E731
    r, d = await bridge.post("/api/tickets/11", {"approve": True, "clientId": cid()})  # needs_isaac: nothing to approve
    assert r.status == 409 and d["code"] == "not_awaiting" and not posts()
    r, d = await bridge.post("/api/tickets/12", {"approve": True, "comment": "x", "clientId": cid()})
    assert r.status == 400 and not posts()
    r, d = await bridge.post("/api/tickets/12", {"approve": True, "clientId": cid()})
    assert r.status == 200 and d["ok"] is True
    assert posts()[-1]["body"] == {"action": "update", "id": 12, "author": "Alex", "source": "The Bridge · Alex",
                                   "comment": "Approved"}
    acts = await bridge.db.a("list_actions")
    assert any(a["kind"] == "ticket.update" and a["summary"] == "approve" and a["outcome"] == "ok" for a in acts)
    # Request changes is a plain comment
    r, d = await bridge.post("/api/tickets/12", {"comment": "Keep the last 200 ids", "clientId": cid()})
    assert r.status == 200 and posts()[-1]["body"]["comment"] == "Keep the last 200 ids"
    assert "status" not in posts()[-1]["body"]


async def test_ticket_n8n_down(bridge, fake_n8n):
    fake_n8n.fail["agent-hub/ticket"] = 500
    r, d = await bridge.post("/api/tickets", {"title": "x", "clientId": cid()})
    assert r.status == 502 and d["code"] == "n8n_http"


# ---- library apply ---------------------------------------------------------------------------------------------------
async def test_apply_needs_confirm_then_force_confirm(bridge, fake_n8n):
    await bridge.jobs.pollers["library"].tick()
    fake_n8n.quiet_hours = True
    r, d = await bridge.post("/api/library/apply", {"ids": "safe", "clientId": cid()})
    assert r.status == 400 and d["code"] == "confirm_required"
    r, d = await bridge.post("/api/library/apply", {"ids": "safe", "confirm": True, "force": True, "clientId": cid()})
    assert r.status == 400 and d["code"] == "force_confirm_required"
    assert not [p for p in fake_n8n.posts if p["path"] == "bridge/library/apply"]
    async with SSE(bridge) as sse:
        r, d = await bridge.post("/api/library/apply", {"ids": "safe", "confirm": True, "clientId": cid()})
        assert r.status == 202 and d["job"]["state"] == "running" and d["job"]["kind"] == "library.apply"
        jid = d["job"]["id"]
        await wait_for(lambda: bridge.jobs.jobs[jid]["state"] != "running")
        job = bridge.jobs.jobs[jid]
        assert job["state"] == "refused" and job["detail"]["quietHours"] and job["detail"]["canForce"]
        assert "evening quiet hours" in job["detail"]["error"]  # shown as is
        await wait_for(lambda: [j for j in sse.of("job") if j["id"] == jid and j["state"] == "refused"])
        r, d = await bridge.get(f"/api/jobs/{jid}")
        assert d["job"]["state"] == "refused"
        r, d = await bridge.post("/api/library/apply", {"ids": "safe", "confirm": True, "force": True,
                                                        "forceConfirm": True, "clientId": cid()})
        assert r.status == 202
        jid2 = d["job"]["id"]
        await wait_for(lambda: bridge.jobs.jobs[jid2]["state"] == "done")
        assert bridge.jobs.jobs[jid2]["detail"]["precheck"]["undo"] is True  # the precheck ran as a hint
        post = [p for p in fake_n8n.posts if p["path"] == "bridge/library/apply"][-1]["body"]
        assert post["force"] is True and post["by"] == "Alex" and len(post["ids"]) == 12
        await bridge.jobs.pollers["library"].tick()
        await wait_for(lambda: sse.of("library"))
        assert len(sse.of("library")[-1]["applied"]) == 12
    acts = [(a["kind"], a["outcome"]) for a in await bridge.db.a("list_actions") if a["kind"] == "library.apply"]
    assert acts == [("library.apply", "ok"), ("library.apply", "refused")]


async def test_apply_single_job_and_ids(bridge, fake_n8n):
    await bridge.jobs.pollers["library"].tick()
    fake_n8n.delay["bridge/library/apply"] = 50
    r, d = await bridge.post("/api/library/apply", {"ids": ["2026-11-18-14"], "confirm": True, "clientId": cid()})
    assert r.status == 202
    assert bridge.jobs.pollers["library"].interval() == 15
    r, d = await bridge.post("/api/library/apply", {"ids": "all", "confirm": True, "clientId": cid()})
    assert r.status == 409 and d["code"] == "busy"
    r, d = await bridge.post("/api/library/apply", {"ids": [1, 2], "confirm": True, "clientId": cid()})
    assert r.status == 400


async def test_apply_timeout_900s(bridge, fake_n8n):
    fake_n8n.delay["bridge/library/apply"] = 905
    r, d = await bridge.post("/api/library/apply", {"ids": "all", "confirm": True, "clientId": cid()})
    jid = d["job"]["id"]
    await wait_for(lambda: bridge.jobs.jobs[jid]["state"] != "running", timeout=12)
    assert bridge.jobs.jobs[jid]["state"] == "timeout"


# ---- SSE -------------------------------------------------------------------------------------------------------------
async def test_sse_ping_visibility_and_crew_events(bridge, fake_core, fake_n8n):
    assert not bridge.hub.visible
    async with SSE(bridge) as sse:
        assert bridge.hub.visible
        await wait_for(lambda: sse.comments >= 1, timeout=2)  # ": ping" every 20 logical s
        await fake_core.push_machine("mac", {"status": "online", "sessions": []})
        await wait_for(lambda: sse.of("crew.machine"))
        assert sse.of("crew.machine")[-1] == {"id": "mac", "data": {"status": "online", "sessions": []}}
        await bridge.jobs.pollers["status"].tick()
        await wait_for(lambda: sse.of("n8n"))
        assert sse.of("n8n")[-1]["status"]["overall"] == "ok"
        await bridge.jobs.pollers["inbox"].tick()
        await wait_for(lambda: sse.of("inbox"))
        assert sse.of("inbox")[-1]["count"] == 4
        await bridge.jobs.pollers["tickets"].tick()
        await wait_for(lambda: sse.of("tickets"))
        fake_n8n.fail["agent-hub/status"] = 500
        await bridge.jobs.pollers["status"].tick()
        await wait_for(lambda: [e for e in sse.of("n8n") if e.get("error")])
        assert sse.of("n8n")[-1]["status"]["overall"] == "ok"  # the last good copy, with the error
    await wait_for(lambda: not bridge.hub.visible)


async def test_anonymous_can_read_but_not_write(bridge):
    r, d = await bridge.get("/api/state", headers={})
    assert r.status == 200 and d["user"]["canWrite"] is False
    async with SSE(bridge, headers={}) as sse:
        assert sse.events[0][1]["user"]["id"] is None
    r, d = await bridge.post("/api/read", {"scope": "inbox"}, headers={"X-Bridge": "1"})
    assert r.status == 403


# ---- frontend requests (several threads per target, auto titles, apply progress, settings event) ----------------------
async def test_new_thread_and_auto_title(bridge, fake_n8n):
    a = await open_thread(bridge)
    r, d = await bridge.post("/api/threads", {"kind": "n8n", "target": "crew", "new": True, "clientId": cid()})
    assert r.status == 201 and d["created"] and d["thread"]["id"] != a["id"]
    b = d["thread"]
    async with SSE(bridge) as sse:
        m = await send(bridge, b["id"], "\n  Why is SABnzbd idle today, and what is stuck in Sonarr right now?\nmore")
        await wait_for(lambda: [t for t in sse.of("thread") if t["id"] == b["id"] and t["title"]])
    th = bridge.db.get_thread(b["id"])
    assert th["title"].startswith("Why is SABnzbd idle today") and th["title"].endswith("…")
    assert len(th["title"]) == 60
    await send(bridge, b["id"], "second message does not retitle")
    assert bridge.db.get_thread(b["id"])["title"] == th["title"]
    r, d = await bridge.post(f"/api/threads/{b['id']}", {"title": "SAB", "clientId": cid()}, method="PATCH")
    assert d["thread"]["title"] == "SAB"
    again = await open_thread(bridge)  # without new: the most recently active open thread
    assert again["id"] == b["id"]
    await wait_for(lambda: bridge.db.get_message(m["id"])["state"] == "replied")


async def test_apply_job_progress_detail(bridge, fake_n8n):
    await bridge.jobs.pollers["library"].tick()
    fake_n8n.apply_flip_after = 1
    async with SSE(bridge) as sse:
        r, d = await bridge.post("/api/library/apply", {"ids": "all", "confirm": True, "clientId": cid()})
        det = d["job"]["detail"]
        assert det["total"] == 12 and det["done"] == 0 and det["ids"][0] == "2026-11-18-14"
        assert det["current"] == "2026-11-18-14" and det["all"] is True
        jid = d["job"]["id"]
        await wait_for(lambda: bridge.jobs.jobs[jid]["state"] == "done")
        assert [p for p in fake_n8n.posts if p["path"] == "bridge/library/apply"][-1]["body"]["ids"] == "all"
        await asyncio.sleep(0.05)
        await bridge.jobs.pollers["library"].tick()
        await wait_for(lambda: [j for j in sse.of("job") if j["id"] == jid and j["detail"]["done"] == 12])
        assert bridge.jobs.jobs[jid]["detail"]["current"] is None


async def test_settings_event(bridge):
    async with SSE(bridge) as sse:
        await bridge.post("/api/settings", {"theme": "day", "clientId": cid()})
        await wait_for(lambda: sse.of("settings"))
        assert sse.of("settings")[-1]["userId"] == "u-alex" and sse.of("settings")[-1]["theme"] == "day"


# ---- "Try again" (bridge/run-now) and library scan --------------------------------------------------------------------
DIGEST = "n8n:wfExample0000007"  # Download Daily Summary: rerun true in the fake status


async def test_rerun_flags_in_agent_payload(bridge, fake_n8n):
    await bridge.jobs.pollers["status"].tick()
    r, d = await bridge.get("/api/crew")
    agents = {a["id"]: a for g in d["agents"].values() for a in g}
    assert agents[DIGEST]["canRerun"] is True and "phone" in agents[DIGEST]["rerunHint"]
    assert agents["svc:media-agent"]["canRerun"] is True and "scan" in agents["svc:media-agent"]["rerunHint"]
    assert agents["n8n:wfExample0000006"]["canRerun"] is False and agents["n8n:wfExample0000006"]["rerunHint"] is None


async def test_rerun_success_watches_new_run(bridge, fake_n8n):
    await bridge.jobs.pollers["status"].tick()
    r, d = await bridge.post("/api/agents/rerun", {"agentId": DIGEST, "clientId": cid()})
    assert r.status == 400 and d["code"] == "confirm_required" and "phone" in d["error"]
    assert not [p for p in fake_n8n.posts if p["path"] == "bridge/run-now"]
    async with SSE(bridge) as sse:
        r, d = await bridge.post("/api/agents/rerun", {"agentId": DIGEST, "confirm": True, "clientId": cid()})
        assert r.status == 202 and d["job"]["kind"] == "agent.rerun" and d["job"]["state"] == "running"
        assert d["job"]["detail"]["hint"].startswith("This runs Download Daily Summary again")
        jid = d["job"]["id"]
        r, d = await bridge.post("/api/agents/rerun", {"agentId": DIGEST, "confirm": True, "clientId": cid()})
        assert r.status == 409 and d["code"] == "busy"
        await wait_for(lambda: bridge.jobs.jobs[jid]["state"] != "running")
        job = bridge.jobs.jobs[jid]
        assert job["state"] == "done", job
        assert job["detail"]["run"]["mode"] == "integrated" and job["detail"]["run"]["id"] == "901"
        await wait_for(lambda: [j for j in sse.of("job") if j["id"] == jid and j["state"] == "done"])
        steps = [j["detail"]["step"] for j in sse.of("job") if j["id"] == jid]
        assert "started" in steps and "running" in steps and steps[-1] == "finished"
    body = [p for p in fake_n8n.posts if p["path"] == "bridge/run-now"][-1]["body"]
    assert body == {"workflowId": "wfExample0000007", "by": "Alex"}
    assert fake_n8n.count("bridge/runs") >= 2
    acts = [(a["kind"], a["target"], a["outcome"], a["user_name"]) for a in await bridge.db.a("list_actions")
            if a["kind"] == "agent.rerun"]
    assert acts == [("agent.rerun", DIGEST, "ok", "Alex")]


async def test_rerun_500_is_not_rerunnable(bridge, fake_n8n):
    await bridge.jobs.pollers["status"].tick()
    fake_n8n.fail["bridge/run-now"] = 500
    r, d = await bridge.post("/api/agents/rerun", {"agentId": DIGEST, "confirm": True, "clientId": cid()})
    jid = d["job"]["id"]
    await wait_for(lambda: bridge.jobs.jobs[jid]["state"] != "running")
    job = bridge.jobs.jobs[jid]
    assert job["state"] == "error" and job["detail"]["code"] == "not_rerunnable"
    assert bridge.n8n.health["ok"] is not False  # a refused re-run doesn't mark n8n down
    acts = [a["outcome"] for a in await bridge.db.a("list_actions") if a["kind"] == "agent.rerun"]
    assert acts == ["refused"]


async def test_rerun_refused_without_rerun_flag(bridge, fake_n8n):
    await bridge.jobs.pollers["status"].tick()
    for agent_id in ("n8n:wfExample0000006", "svc:llm"):
        r, d = await bridge.post("/api/agents/rerun", {"agentId": agent_id, "confirm": True, "clientId": cid()})
        assert r.status == 409 and d["code"] == "not_rerunnable"
    r, d = await bridge.post("/api/agents/rerun", {"agentId": "n8n:nope", "confirm": True, "clientId": cid()})
    assert r.status == 404
    r, d = await bridge.post("/api/agents/rerun", {"agentId": DIGEST, "confirm": True, "clientId": cid()},
                             headers={**W, "X-Remote-User-Groups": "system-users"})
    assert r.status == 403 and d["code"] == "not_admin"
    assert not [p for p in fake_n8n.posts if p["path"] in ("bridge/run-now", "bridge/library/scan")]


async def test_library_scan_and_409(bridge, fake_n8n):
    await bridge.jobs.pollers["status"].tick()
    r, d = await bridge.post("/api/library/scan", {"clientId": cid()})
    assert r.status == 202 and d["job"]["kind"] == "library.scan"
    jid = d["job"]["id"]
    await wait_for(lambda: bridge.jobs.jobs[jid]["state"] != "running")
    assert bridge.jobs.jobs[jid]["state"] == "done" and bridge.jobs.jobs[jid]["detail"]["started"] is True
    assert [p for p in fake_n8n.posts if p["path"] == "bridge/library/scan"][-1]["body"] == {"hours": 24, "by": "Alex"}
    fake_n8n.scan_running = True
    # the library agent's "Try again" goes through rerun_via -> bridge/library/scan
    r, d = await bridge.post("/api/agents/rerun", {"agentId": "svc:media-agent", "confirm": True, "clientId": cid()})
    assert r.status == 202 and d["job"]["kind"] == "library.scan"
    jid = d["job"]["id"]
    await wait_for(lambda: bridge.jobs.jobs[jid]["state"] != "running")
    assert bridge.jobs.jobs[jid]["state"] == "refused" and bridge.jobs.jobs[jid]["detail"]["code"] == "scan_running"
    r, d = await bridge.post("/api/library/scan", {"hours": 0, "clientId": cid()})
    assert r.status == 400
    acts = [(a["target"], a["outcome"]) for a in await bridge.db.a("list_actions") if a["kind"] == "library.scan"]
    assert acts == [("svc:media-agent", "refused"), ("library", "ok")]
