"""Agent output + Talk (0.2.0): the run and service-output proxies, the Claude conversation proxy (in memory only),
the Chat list rules (two-way threads only) and Talk for agents that are off."""

import asyncio
import json

from bridge.app import server
from conftest import ADMIN, cid, wait_for

CH = "b8e41f03-2c6a-47d9-8e15-3a90c6d2f7e1"  # Crew Hub (bg): send + transcript
JAMFIN = "8c1f2a4e-3b7d-4e19-9a61-0f2d5c7b9e10"
NO_TX = "e5c7a913-0b2d-4f6e-8a14-c3d5e7f9a1b2"  # "error demo": send, no transcript
MAC_ENDED = "f0a3c5e7-9b1d-4c2e-a6f8-0d1e2f3a4b5c"
SENTINEL = "SENTINEL-7Q3Z-transcript-text-never-stored"
CONV = f"/api/sessions/nas/{CH}/conversation"
VIEWER = {"X-Remote-User-Id": "u-guest", "X-Remote-User-Name": "guest", "X-Remote-User-Groups": "system-users"}


async def status(bridge):
    await bridge.jobs.pollers["status"].tick()


async def audits(bridge, kind):  # through the DB thread, like the server (no sync calls racing it)
    return [a for a in await bridge.db.a("list_actions", 500) if a["kind"] == kind]


# ---- n8n run detail ---------------------------------------------------------------------------------------------------
async def test_agent_run_proxy_cache_and_rules(bridge, fake_n8n):
    await status(bridge)
    r, d = await bridge.get("/api/agents/n8n:wfExample0000007/runs/140")
    assert r.status == 200 and d["run"]["id"] == "140" and d["cached"] is False and d["agentId"] == "n8n:wfExample0000007"
    assert d["run"]["summary"].startswith("Attention")
    r, d = await bridge.get("/api/agents/n8n%3AwfExample0000007/runs/140")  # the page encodes the colon
    assert r.status == 200 and d["cached"] is True
    assert fake_n8n.count("bridge/run") == 1
    bridge.clock.advance(61)
    r, d = await bridge.get("/api/agents/n8n:wfExample0000007/runs/140")
    assert d["cached"] is False and fake_n8n.count("bridge/run") == 2
    # a run of another workflow, an unknown run, a system agent, an unknown agent, a bad id
    r, d = await bridge.get("/api/agents/n8n:wfExample0000007/runs/178")
    assert r.status == 404 and d["code"] == "no_run"
    r, d = await bridge.get("/api/agents/n8n:wfExample0000007/runs/999")
    assert r.status == 404 and d["code"] == "no_run"
    r, d = await bridge.get("/api/agents/n8n:wfExample0000001/runs/140")
    assert r.status == 404 and d["code"] == "no_runs"
    r, d = await bridge.get("/api/agents/n8n:wfNope/runs/140")
    assert r.status == 404 and d["code"] == "not_found"
    r, d = await bridge.get("/api/agents/n8n:wfExample0000007/runs/1;drop")
    assert r.status == 400
    a = await audits(bridge, "agent.run")
    assert a and all(x["user_id"] == "u-alex" and x["user_name"] == "Alex" for x in a)
    assert any(x["summary"] == "run 140 · cached" for x in a) and any(x["outcome"] == "refused" for x in a)


async def test_agent_run_untracked_has_none(bridge, fake_n8n):
    agent = next(a for a in fake_n8n.status["agents"] if a["id"] == "n8n:wfExample0000007")
    agent["tracked"] = False
    await status(bridge)
    r, d = await bridge.get("/api/agents/n8n:wfExample0000007/runs/140")
    assert r.status == 404 and d["code"] == "no_runs" and fake_n8n.count("bridge/run") == 0


# ---- service output ---------------------------------------------------------------------------------------------------
async def test_service_output_cache_refresh_and_404(bridge, fake_n8n):
    await status(bridge)
    url = "/api/agents/svc:media-agent/output"
    r, d = await bridge.get(url)
    assert r.status == 200 and d["output"]["text"].startswith("State: idle") and d["cached"] is False
    r, d = await bridge.get(url)
    assert d["cached"] is True and fake_n8n.count("bridge/agent-output") == 1
    bridge.clock.advance(31)
    r, d = await bridge.get(url)
    assert d["cached"] is False and fake_n8n.count("bridge/agent-output") == 2
    r, d = await bridge.get(url + "?fresh=1")  # Refresh right after a fetch: the 5 s copy is fresh enough
    assert d["cached"] is True and fake_n8n.count("bridge/agent-output") == 2
    bridge.clock.advance(6)
    r, d = await bridge.get(url + "?fresh=1")  # Refresh bypasses the 30 s cache
    assert d["cached"] is False and fake_n8n.count("bridge/agent-output") == 3
    r, d = await bridge.get("/api/agents/svc:llm/output")  # n8n has no output for it (500): a state, not 500
    assert r.status == 200 and d["output"] is None and d["reason"] == "no_output"
    r, d = await bridge.get("/api/agents/n8n:wfExample0000007/output")
    assert r.status == 404 and d["code"] == "no_output"
    r, d = await bridge.get("/api/agents/svc:nope/output")
    assert r.status == 404 and d["code"] == "not_found"
    a = await audits(bridge, "agent.output")
    assert {x["summary"] for x in a} >= {"open", "open · cached", "refresh", "refresh · cached"}
    assert all(x["user_id"] == "u-alex" for x in a)


# ---- Claude conversation ----------------------------------------------------------------------------------------------
async def test_conversation_shapes_cache_and_poll(bridge, fake_core):
    r, d = await bridge.get(CONV)
    assert r.status == 200, d
    assert d["cached"] is False and d["machine"] == "nas" and d["session"] == CH and d["cursor"] == "c6"
    tx = fake_core.transcript_requests()
    assert len(tx) == 1 and tx[0]["machine"] == "nas" and tx[0]["session"] == CH and tx[0]["limit"] == 20
    assert "after" not in tx[0]
    k = [t["k"] for t in d["turns"]]
    assert k == ["user", "assistant", "report", "assistant", "user", "assistant"]
    u0, a1, rep, a3, u4, a5 = d["turns"]
    assert u0["via"] == "machine" and u4["via"] == "bridge"
    assert u4["text"] == "Does the card still jump when an agent finishes?"  # the Bridge prefix is a marker, not text
    assert a3["steps"] == {"read": 3, "search": 1, "agent": 1}
    assert rep["agentId"] == "c2e3f40516273849" and rep["title"] == "Write the snapshot fixture #1"
    assert a5["masked"] is True and "⟦masked⟧" in a5["text"] and "••••" not in a5["text"]
    assert len({t["id"] for t in d["turns"]}) == 6
    # cached for the next open
    r, d2 = await bridge.get(CONV)
    assert d2["cached"] is True and d2["turns"] == d["turns"] and len(fake_core.transcript_requests()) == 1
    # polling: only newer turns, from the cursor
    r, p = await bridge.get(CONV + "?after=c6")
    assert p["turns"] == [] and p["cursor"] == "c6"
    fake_core.add_turn(f"nas/{CH}", {"kind": "assistant", "at": "2026-10-26T14:30:00Z", "text": "Checks pass.",
                                     "steps": {"run": 2, "bogus": 3, "edit": -1}})
    r, p = await bridge.get(CONV + "?after=c6")
    assert [t["text"] for t in p["turns"]] == ["Checks pass."] and p["turns"][0]["steps"] == {"run": 2}
    assert p["cursor"] == "c7" and fake_core.transcript_requests()[-1]["after"] == "c6"
    r, d3 = await bridge.get(CONV)  # the live cache entry took the new turn
    assert d3["cached"] is True and len(d3["turns"]) == 7 and d3["cursor"] == "c7"
    # fresh asks again; after 60 s the copy is gone
    n = len(fake_core.transcript_requests())
    r, d4 = await bridge.get(CONV + "?fresh=1")
    assert d4["cached"] is False and len(fake_core.transcript_requests()) == n + 1
    bridge.clock.advance(61)
    conv_store(bridge).sweep()
    assert not conv_store(bridge).cache
    r, d5 = await bridge.get(CONV)
    assert d5["cached"] is False
    a = await audits(bridge, "claude.transcript")
    assert {x["summary"] for x in a} >= {"open", "open · cached", "poll", "refresh"}
    assert all(x["user_id"] == "u-alex" and x["target"] == f"nas/{CH}" for x in a)


async def test_conversation_more_means_cut(bridge, fake_core):
    fake_core.transcripts[f"nas/{JAMFIN}"] = [{"kind": "user", "at": i, "text": f"q{i}"} for i in range(30)]
    r, d = await bridge.get(f"/api/sessions/nas/{JAMFIN}/conversation")
    assert d["more"] is True and d["cut"] is True and len(d["turns"]) == 20 and d["turns"][-1]["text"] == "q29"


def conv_store(bridge):
    return bridge.app[server.K_CONV]


async def test_conversation_never_stored(bridge, fake_core):
    """Sentinel text from a fake transcript must not reach SQLite (db, wal, snapshot), the SSE log or the audit."""
    fake_core.add_turn(f"nas/{CH}", {"kind": "assistant", "at": "2026-10-26T14:40:00Z",
                                     "text": f"Here is {SENTINEL} in a reply.", "steps": {}})
    fake_core.add_turn(f"nas/{CH}", {"kind": "report", "at": "2026-10-26T14:41:00Z", "agent": "a1",
                                     "title": "t", "text": f"report {SENTINEL}"})
    r, d = await bridge.get(CONV)
    assert any(SENTINEL in t["text"] for t in d["turns"])
    t = (await bridge.post("/api/threads", {"kind": "claude", "target": f"nas/{CH}", "clientId": cid()}))[1]["thread"]
    r, m = await bridge.post(f"/api/threads/{t['id']}/messages", {"text": "my own words", "clientId": cid()})
    assert r.status == 202
    await wait_for(lambda: bridge.db.get_message(m["message"]["id"])["state"] == "delivered")
    await bridge.get(CONV + "?after=c8")
    data_dir = bridge.app[server.K_CFG].data_dir
    await bridge.db.a("snapshot", data_dir / "snapshot" / "bridge.db")
    files = [p for p in data_dir.rglob("*") if p.is_file()]
    assert any(p.name.startswith("bridge.db") for p in files)
    for p in files:
        assert SENTINEL.encode() not in p.read_bytes(), p
    assert b"my own words" in b"".join(p.read_bytes() for p in files)  # the user's message is stored, as before
    assert SENTINEL not in json.dumps(bridge.hub.published)
    assert SENTINEL not in json.dumps(await bridge.db.a("list_actions", 500))
    rows = await bridge.db.a("list_messages", t["id"])
    assert [x["role"] for x in rows] == ["user"]


async def test_conversation_cleared_on_close_and_sse_unsubscribe(bridge, fake_core):
    store = conv_store(bridge)
    await bridge.get(CONV)
    assert f"nas/{CH}" in store.cache
    r, d = await bridge.post(f"/api/sessions/nas/{CH}/conversation/close", {})
    assert r.status == 200 and f"nas/{CH}" not in store.cache
    resp = await bridge.client.get("/api/events", headers=ADMIN)
    await resp.content.readline()
    await wait_for(lambda: bridge.hub.visible)
    await bridge.get(CONV)
    assert store.cache
    resp.close()
    await wait_for(lambda: not bridge.hub.visible)
    assert store.cache == {}


async def test_conversation_refusals(bridge, fake_core):
    r, d = await bridge.get(CONV, headers=VIEWER)
    assert r.status == 403 and d["code"] == "not_admin"
    r, d = await bridge.get(f"/api/sessions/mac/{MAC_ENDED}/conversation")
    assert r.status == 409 and d["code"] == "offline"
    r, d = await bridge.get(f"/api/sessions/nas/{NO_TX}/conversation")
    assert r.status == 409 and d["code"] == "no_transcript"
    r, d = await bridge.get("/api/sessions/nas/nope/conversation")
    assert r.status == 404 and d["code"] == "unknown_session"
    r, d = await bridge.get(CONV + "?after=bad%20cursor")
    assert r.status == 400
    assert fake_core.transcript_requests() == []
    fake_core.transcript_error = "not_live"
    r, d = await bridge.get(CONV)
    assert r.status == 200 and d["gone"] is True and d["reason"] == "not_live" and d["turns"] == []
    fake_core.transcript_error = "bad_seal"
    r, d = await bridge.get(CONV)
    assert r.status == 502 and d["code"] == "bad_seal"
    assert {a["error"] for a in await audits(bridge, "claude.transcript")} == {"not_live", "bad_seal"}
    await fake_core.drop()
    await wait_for(lambda: not bridge.crew.connected)
    fake_core.refuse = True
    r, d = await bridge.get(CONV)
    assert r.status == 503 and d["code"] == "crew_disconnected"


# ---- Chat: two-way threads only ---------------------------------------------------------------------------------------
async def test_chat_list_rules(bridge, fake_n8n, fake_core):
    await status(bridge)

    async def open_(kind, target):
        return await bridge.post("/api/threads", {"kind": kind, "target": target, "clientId": cid()})

    r, crew_t = await open_("n8n", "crew")
    r, lib_t = await open_("n8n", "svc:media-agent")
    assert r.status == 201
    r, d = await open_("n8n", "svc:llm")  # chat: false
    assert r.status == 400 and d["code"] == "not_chat"
    r, cl_t = await open_("claude", f"nas/{CH}")
    assert r.status == 201
    r, d = await open_("claude", f"nas/{NO_TX}")  # can take messages but can't send its conversation
    assert r.status == 400 and d["code"] == "not_chat" and "no_transcript" in d["error"]
    r, d = await bridge.get("/api/threads")
    ids = {t["id"] for t in d["threads"]}
    assert ids == {crew_t["thread"]["id"], lib_t["thread"]["id"], cl_t["thread"]["id"]}
    offered = {c["session"]: c for c in d["claude"]}
    assert CH in offered and JAMFIN in offered and NO_TX not in offered and MAC_ENDED not in offered
    assert offered[CH]["threadId"] == cl_t["thread"]["id"] and offered[JAMFIN]["threadId"] is None
    # the agent stops chatting in n8n and the session loses transcript: both leave Chat
    next(a for a in fake_n8n.status["agents"] if a["id"] == "svc:media-agent")["chat"] = False
    bridge.clock.advance(31)
    await status(bridge)
    nas = json.loads(json.dumps(fake_core.machines["nas"]))
    next(s for s in nas["sessions"] if s["id"] == CH)["controls"] = ["send", "answer"]
    await fake_core.push_machine("nas", nas)
    await wait_for(lambda: "transcript" not in bridge.crew.find_session("nas", CH)["controls"])
    r, d = await bridge.get("/api/threads")
    assert {t["id"] for t in d["threads"]} == {crew_t["thread"]["id"]}
    assert CH not in {c["session"] for c in d["claude"]}


async def test_talk_for_an_agent_that_is_off(bridge, fake_n8n):
    await status(bridge)
    off = next(a for a in fake_n8n.status["agents"] if a["id"] == "n8n:wfExample0000006")
    assert off["state"] == "off" and off["active"] is False and off["chat"] is True
    r, d = await bridge.post("/api/threads", {"kind": "n8n", "target": off["id"], "clientId": cid()})
    assert r.status == 201 and d["thread"]["targetName"] == "Download Watcher"
    r, d2 = await bridge.get("/api/threads")
    assert d["thread"]["id"] in {t["id"] for t in d2["threads"]}
    r, m = await bridge.post(f"/api/threads/{d['thread']['id']}/messages", {"text": "are you there?", "clientId": cid()})
    assert r.status == 202
    await wait_for(lambda: [p for p in fake_n8n.posts if p["path"] == "bridge/chat"])
    body = [p for p in fake_n8n.posts if p["path"] == "bridge/chat"][-1]["body"]
    assert body["agent_id"] == off["id"] and body["query"] == "are you there?"
    await asyncio.sleep(0)
