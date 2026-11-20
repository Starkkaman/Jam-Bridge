"""Agents by job + OpenCode agents (0.3.0, docs/BRIDGE.md §14): grouping and SANDBOX hiding in api/crew, the four
OpenCode endpoints with their caches, the write guards (Bridge sessions only, before n8n), the send rate limit, the
busy answers, and transcripts that never reach SQLite."""

import json

import pytest

from bridge.app import agents as agents_mod
from bridge.app import server
from bridge.app.clock import Clock
from conftest import ADMIN, W, cid, wait_for
from fake_n8n import NOT_BRIDGE, oc_seed

VIEWER = {"X-Remote-User-Id": "u-guest", "X-Remote-User-Name": "guest", "X-Remote-User-Groups": "system-users"}
SENTINEL = "SENTINEL-OC-4K2P-opencode-text-never-stored"
ENG = "/api/opencode/agents/oc:engineer"


@pytest.fixture
async def ocb(bridge_factory, fake_n8n):
    """The Bridge with the fake n8n only; the OpenCode cache on a real-speed clock so 10-30 s windows are testable."""
    b = await bridge_factory(n8n=fake_n8n)
    clock = Clock(1.0)
    b.oc = b.app[server.K_OC]
    b.oc.clock = clock
    b.occlock = clock
    return b


async def status(bridge):
    await bridge.jobs.pollers["status"].tick()


async def audits(bridge, kind):
    return [a for a in await bridge.db.a("list_actions", 500) if a["kind"] == kind]


def msg(text="Please look at the trailer fixes", **kw):
    return {"text": text, "clientId": cid(), **kw}


# ---- agents by job ----------------------------------------------------------------------------------------------------
def test_job_sections_order_and_other():
    assert agents_mod.JOBS == ("engineering", "home", "security", "downloads", "library", "general", "service")
    job = agents_mod.job_of
    assert job({"id": "oc:engineer", "role": "engineering"}) == "engineering"
    assert job({"id": "oc:ha-builder", "role": "home"}) == "home"
    assert job({"id": "n8n:x", "role": "general", "name": "Phone Reply Agent"}) == "general"  # role wins over the name
    assert job({"id": "n8n:x", "role": "system"}) == "system"
    assert job({"id": "n8n:x", "name": "Agent Hub: Status"}) == "system"  # no role: the system name list
    assert job({"id": "n8n:x", "role": "gardening"}) == "other"
    assert job({"id": "n8n:x", "role": None, "name": "Something"}) == "other"
    assert job({"id": "svc:x"}) == "other"


async def test_crew_groups_by_job_and_hides_sandbox_copies(ocb, fake_n8n):
    fake_n8n.status["agents"].append({"id": "n8n:wfOdd", "workflowId": "wfOdd", "name": "Odd one", "kind": "n8n workflow",
                                      "role": "gardening", "state": "error", "chat": True})
    fake_n8n.status["agents"].append({"id": "n8n:wfSandbox9", "name": "sandbox: lower case", "role": "home",
                                      "state": "error"})
    await status(ocb)
    r, d = await ocb.get("/api/crew")
    every = [a for g in d["agents"].values() for a in g]
    assert d["sandboxHidden"] == 3
    assert not any(a["name"].lower().startswith("sandbox:") for a in every)
    by = {a["id"]: a for a in every}
    assert by["oc:engineer"]["job"] == "engineering" and by["oc:engineer"]["kind"] == "opencode agent"
    assert by["oc:ha-builder"]["job"] == "home" and by["oc:engineer"]["chat"] is True
    assert by["n8n:wfOdd"]["job"] == "other"
    assert by["svc:llm"]["job"] == "service" and by["svc:media-agent"]["job"] == "library"
    assert by["n8n:wfExample0000005"]["job"] == "system"
    # a failing SANDBOX copy never reaches Now's needs-you
    r, o = await ocb.get("/api/overview")
    assert [a["id"] for a in o["needsYou"]["agents"]] == ["n8n:wfOdd"]


# ---- reads and caches -------------------------------------------------------------------------------------------------
async def test_agents_cached_30s_shared_and_fresh_rate_limited(ocb, fake_n8n):
    r, d = await ocb.get("/api/opencode/agents")
    assert r.status == 200 and d["cached"] is False
    eng = next(a for a in d["agents"] if a["id"] == "oc:engineer")
    assert eng["short_name"] == "the Engineer" and eng["role"] == "engineering" and eng["busy"] is True
    assert eng["lastSession"]["id"] == "ses_t7" and eng["description"].startswith("Works through tickets")
    hab = next(a for a in d["agents"] if a["id"] == "oc:ha-builder")
    assert hab["needsApproval"] is True
    r, d = await ocb.get("/api/opencode/agents", headers=VIEWER)  # another viewer: the same copy
    assert d["cached"] is True and fake_n8n.count("bridge/opencode/agents") == 1
    r, d = await ocb.get("/api/opencode/agents?fresh=1")  # younger than 5 s: still the copy
    assert d["cached"] is True and fake_n8n.count("bridge/opencode/agents") == 1
    ocb.occlock.advance(6)
    r, d = await ocb.get("/api/opencode/agents?fresh=1")
    assert d["cached"] is False and fake_n8n.count("bridge/opencode/agents") == 2
    ocb.occlock.advance(29)
    await ocb.get("/api/opencode/agents")
    assert fake_n8n.count("bridge/opencode/agents") == 2
    ocb.occlock.advance(2)
    await ocb.get("/api/opencode/agents")
    assert fake_n8n.count("bridge/opencode/agents") == 3


async def test_sessions_list_and_bad_ids(ocb, fake_n8n):
    r, d = await ocb.get(ENG + "/sessions")
    assert r.status == 200 and d["agent"] == "oc:engineer"
    ids = [s["id"] for s in d["sessions"]]
    assert ids[0] == "ses_t7" and set(ids) == {"ses_b1", "ses_t7", "ses_t5", "ses_o1"}
    s = {x["id"]: x for x in d["sessions"]}
    assert s["ses_b1"]["origin"] == "bridge" and s["ses_b1"]["title"] == "Bridge: Why do the trailer fixes stall?"
    assert s["ses_t7"]["origin"] == "ticket" and s["ses_t7"]["ticketId"] == 7 and s["ses_t7"]["busySince"]
    assert fake_n8n.requests[-1]["query"] == {"agent": "oc:engineer", "limit": "20"}
    r, d = await ocb.get(ENG + "/sessions")
    assert d["cached"] is True and fake_n8n.count("bridge/opencode/sessions") == 1
    r, d = await ocb.get("/api/opencode/agents/n8n:wfExample0000002/sessions")
    assert r.status == 404 and fake_n8n.count("bridge/opencode/sessions") == 1
    r, d = await ocb.get("/api/opencode/agents/oc:nobody/sessions")  # n8n: 400 unknown agent
    assert r.status == 404 and d["code"] == "not_found"
    assert ocb.n8n.health["ok"] is True  # an answer, not an n8n failure


async def test_session_detail_cache_not_found_and_admin_only(ocb, fake_n8n):
    r, d = await ocb.get("/api/opencode/sessions/ses_h1")
    assert r.status == 200 and d["cached"] is False
    s = d["session"]
    assert s["agent"] == "oc:ha-builder" and s["origin"] == "bridge" and s["writable"] is True
    assert s["needsApproval"] is True and s["approvalText"].startswith("homeassistant: ")
    assert [m["role"] for m in s["messages"]] == ["user", "assistant"]
    assert {t["status"] for t in s["tools"]} == {"done", "pending"} and all(t["at"] for t in s["tools"])
    r, d = await ocb.get("/api/opencode/sessions/ses_h1")
    assert d["cached"] is True and fake_n8n.count("bridge/opencode/session") == 1
    ocb.occlock.advance(16)
    r, d = await ocb.get("/api/opencode/sessions/ses_h1")
    assert d["cached"] is False and fake_n8n.count("bridge/opencode/session") == 2
    r, d = await ocb.get("/api/opencode/sessions/ses_t7")
    assert d["session"]["writable"] is False and d["session"]["ticketId"] == 7
    r, d = await ocb.get("/api/opencode/sessions/ses_nope")  # n8n: 400 "session ses_nope not found"
    assert r.status == 404 and d["code"] == "not_found"
    assert ocb.n8n.health["ok"] is True
    r, d = await ocb.get("/api/opencode/sessions/bad%20id")
    assert r.status == 404
    r, d = await ocb.get("/api/opencode/sessions/ses_h1", headers=VIEWER)
    assert r.status == 403 and d["code"] == "not_admin"
    a = await audits(ocb, "opencode.session")
    assert a and all(x["user_name"] == "Alex" for x in a)
    assert any(x["summary"] == "open · cached" for x in a) and any(x["outcome"] == "refused" for x in a)


async def test_n8n_down_keeps_the_last_copy(ocb, fake_n8n):
    await ocb.get(ENG + "/sessions")
    fake_n8n.fail["bridge/opencode/sessions"] = 500
    ocb.occlock.advance(31)
    r, d = await ocb.get(ENG + "/sessions")
    assert r.status == 200 and d["stale"] is True and d["sessions"]
    r, d = await ocb.get("/api/opencode/agents/oc:ha-builder/sessions")  # nothing in memory: an error
    assert r.status == 502


# ---- sends ------------------------------------------------------------------------------------------------------------
async def test_send_new_chat_then_busy_answers(ocb, fake_n8n):
    r, d = await ocb.post(ENG + "/messages", msg())
    assert r.status == 200 and d == {"ok": True, "sessionId": "ses_new1"}
    post = fake_n8n.posts[-1]
    assert post["path"] == "bridge/opencode/message"
    assert post["body"] == {"agent": "oc:engineer", "text": "Please look at the trailer fixes", "author": "Alex"}
    assert ("opencode", {"agent": "oc:engineer", "sessionId": "ses_new1"}) in ocb.hub.published
    # a second new chat while that one runs: agent_busy with the running session
    ocb.occlock.advance(11)
    r, d = await ocb.post(ENG + "/messages", msg())
    assert r.status == 409 and d["code"] == "agent_busy" and d["sessionId"] == "ses_new1"
    assert d["error"].startswith("The Engineer is busy")
    # the same session while it works: session_busy (no wait needed: the refusal didn't count)
    r, d = await ocb.post(ENG + "/messages", msg(sessionId="ses_new1"))
    assert r.status == 409 and d["code"] == "session_busy" and d["sessionId"] == "ses_new1"
    assert ocb.n8n.health["ok"] is True
    # it replies; then the chat takes another message
    await wait_for(lambda: not fake_n8n.oc_sessions["ses_new1"]["busy"])
    r, d = await ocb.post(ENG + "/messages", msg("And the nightly run?", sessionId="ses_new1"))
    assert r.status == 200 and d["sessionId"] == "ses_new1"
    a = await audits(ocb, "opencode.message")
    assert [x["outcome"] for x in reversed(a)] == ["ok", "refused", "refused", "ok"]
    assert [x["error"] for x in reversed(a)][1:3] == ["agent_busy", "session_busy"]
    assert all(x["target"] == "oc:engineer" and x["user_id"] == "u-alex" for x in a)
    assert "trailer" not in json.dumps(a) and "nightly" not in json.dumps(a)  # never the text


async def test_send_guards_before_n8n(ocb, fake_n8n):
    def posts():
        return fake_n8n.count("bridge/opencode/message")

    # ticket and OpenCode sessions are read-only here: 403 before n8n is asked
    r, d = await ocb.post(ENG + "/messages", msg(sessionId="ses_t7"))
    assert r.status == 403 and d["code"] == "not_bridge_session" and "ticket #7" in d["error"] and d["ticketId"] == 7
    r, d = await ocb.post(ENG + "/messages", msg(sessionId="ses_o1"))
    assert r.status == 403 and d["code"] == "not_bridge_session" and "OpenCode" in d["error"]
    r, d = await ocb.post("/api/opencode/agents/oc:ha-builder/messages", msg(sessionId="ses_b1"))  # another agent's
    assert r.status == 404
    r, d = await ocb.post(ENG + "/messages", msg(sessionId="ses_gone"))
    assert r.status == 404 and d["code"] == "not_found"
    assert posts() == 0
    a = await audits(ocb, "opencode.message")
    assert len(a) == 2 and all(x["outcome"] == "refused" and x["error"] == "not_bridge_session" for x in a)
    # validation
    for body, code in (({"clientId": cid()}, "missing"), (msg("x" * 4001), "too_long"),
                       (msg(sessionId="bad id"), "invalid"), (msg(sessionId=5), "invalid")):
        r, d = await ocb.post(ENG + "/messages", body)
        assert r.status == 400 and d["code"] == code, body
    r, d = await ocb.post("/api/opencode/agents/oc:nobody/messages", msg())
    assert r.status == 404
    r, d = await ocb.post("/api/opencode/agents/n8n:wfExample0000002/messages", msg())
    assert r.status == 404
    # write rules: X-Bridge, an admin HA user, a clientId
    r, d = await ocb.post(ENG + "/messages", msg(), headers=ADMIN)
    assert r.status == 403 and d["code"] == "csrf"
    r, d = await ocb.post(ENG + "/messages", msg(), headers={**VIEWER, "X-Bridge": "1"})
    assert r.status == 403 and d["code"] == "not_admin"
    r, d = await ocb.post(ENG + "/messages", {"text": "hi"})
    assert r.status == 400 and d["code"] == "client_id"
    assert posts() == 0


async def test_n8n_refusal_for_another_origin_is_mapped(ocb, fake_n8n):
    """Our guard reads the cached session; if n8n still refuses (a stale copy), its 400 becomes 403, not an error."""
    await ocb.get("/api/opencode/sessions/ses_b1")
    fake_n8n.oc_sessions["ses_b1"]["origin"] = "opencode"
    r, d = await ocb.post(ENG + "/messages", msg(sessionId="ses_b1"))
    assert r.status == 403 and d["code"] == "not_bridge_session" and NOT_BRIDGE in d["error"]
    assert ocb.n8n.health["ok"] is True
    fake_n8n.oc_sessions.pop("ses_b1")
    r, d = await ocb.post(ENG + "/messages", msg(sessionId="ses_b1"))
    assert r.status == 404 and d["code"] == "not_found"  # n8n: "session ses_b1 not found"


async def test_rate_limit_one_send_per_10s_per_user(ocb, fake_n8n):
    fake_n8n.oc_reply_after = 0
    r, d = await ocb.post(ENG + "/messages", msg())
    assert r.status == 200
    sid = d["sessionId"]
    await wait_for(lambda: not fake_n8n.oc_sessions[sid]["busy"])
    r, d = await ocb.post(ENG + "/messages", msg(sessionId=sid))
    assert r.status == 429 and d["code"] == "rate_limited" and 1 <= d["retryAfter"] <= 10
    other = {"X-Remote-User-Id": "u-sam", "X-Remote-User-Name": "sam", "X-Remote-User-Groups": "system-admin",
             "X-Bridge": "1"}
    r, d = await ocb.post("/api/opencode/agents/oc:ha-builder/messages", msg(sessionId="ses_h3"), headers=other)
    assert r.status == 200  # per user
    assert fake_n8n.posts[-1]["body"]["author"] == "sam"
    ocb.occlock.advance(10)
    r, d = await ocb.post(ENG + "/messages", msg(sessionId=sid))
    assert r.status == 200
    assert fake_n8n.count("bridge/opencode/message") == 3
    assert any(x["error"] == "rate_limited" for x in await audits(ocb, "opencode.message"))


async def test_repeated_client_id_is_answered_once(ocb, fake_n8n):
    body = msg()
    r, d = await ocb.post(ENG + "/messages", body)
    assert r.status == 200
    r2, d2 = await ocb.post(ENG + "/messages", body)
    assert r2.status == 200 and d2["duplicate"] is True and d2["sessionId"] == d["sessionId"] and d2["ok"] is True
    assert fake_n8n.count("bridge/opencode/message") == 1


async def test_send_forgets_the_cached_copies(ocb, fake_n8n):
    await ocb.get(ENG + "/sessions")
    await ocb.get("/api/opencode/agents")
    r, d = await ocb.post(ENG + "/messages", msg())
    r, d = await ocb.get(ENG + "/sessions")
    assert d["cached"] is False and d["sessions"][0]["id"] == "ses_new1" and d["sessions"][0]["busy"] is True
    r, d = await ocb.get("/api/opencode/agents")
    assert d["cached"] is False


# ---- memory only ------------------------------------------------------------------------------------------------------
async def test_opencode_text_never_stored(ocb, fake_n8n):
    fake_n8n.oc_sessions["ses_b1"]["messages"].append({"role": "assistant", "text": f"Here is {SENTINEL}.", "at": None})
    fake_n8n.oc_sessions["ses_b1"]["title"] = f"Bridge: {SENTINEL}"
    r, d = await ocb.get("/api/opencode/sessions/ses_b1")
    assert SENTINEL in d["session"]["messages"][-1]["text"]
    r, d = await ocb.get(ENG + "/sessions")
    assert any(SENTINEL in (s["title"] or "") for s in d["sessions"])
    assert any(SENTINEL in json.dumps(v["data"]) for v in ocb.oc.cache.values())  # memory only
    r, d = await ocb.post(ENG + "/messages", msg(f"my words {SENTINEL}", sessionId="ses_b1"))
    assert r.status == 200
    data_dir = ocb.app[server.K_CFG].data_dir
    await ocb.db.a("snapshot", data_dir / "snapshot" / "bridge.db")
    files = [p for p in data_dir.rglob("*") if p.is_file()]
    assert any(p.name.startswith("bridge.db") for p in files)
    for p in files:
        assert SENTINEL.encode() not in p.read_bytes(), p
    assert SENTINEL not in json.dumps(ocb.hub.published)
    assert SENTINEL not in json.dumps(await ocb.db.a("list_actions", 500))


async def test_memory_cleared_when_the_last_page_leaves(ocb, fake_n8n):
    await ocb.get("/api/opencode/sessions/ses_b1")
    assert ocb.oc.cache
    q = ocb.hub.add()
    ocb.hub.remove(q)
    assert ocb.oc.cache == {}
    await ocb.get("/api/opencode/sessions/ses_b1")
    ocb.occlock.advance(121)
    ocb.oc.sweep()
    assert ocb.oc.cache == {}


async def test_state_carries_the_opencode_url(bridge_factory, fake_n8n):
    b = await bridge_factory(n8n=fake_n8n)
    r, d = await b.get("/api/state")
    assert d["opencodeUrl"] is None
    b.app[server.K_CFG].opencode_url = "http://opencode.example:4096"
    r, d = await b.get("/api/state")
    assert d["opencodeUrl"] == "http://opencode.example:4096" and d["version"] == "0.3.0"


def test_opencode_url_option(tmp_path):
    from bridge.app.config import load_config

    opts = tmp_path / "options.json"
    opts.write_text(json.dumps({"n8n_url": "http://n8n.example:5678", "opencode_url": "https://oc.example/ui/"}))
    assert load_config({"BRIDGE_OPTIONS": str(opts), "BRIDGE_DATA": str(tmp_path)}).opencode_url == "https://oc.example/ui"
    opts.write_text(json.dumps({"n8n_url": "http://n8n.example:5678", "opencode_url": "javascript:alert(1)"}))
    assert load_config({"BRIDGE_OPTIONS": str(opts), "BRIDGE_DATA": str(tmp_path)}).opencode_url is None
    opts.write_text(json.dumps({"n8n_url": "http://n8n.example:5678"}))
    assert load_config({"BRIDGE_OPTIONS": str(opts), "BRIDGE_DATA": str(tmp_path)}).opencode_url is None


def test_fake_scenes_are_consistent():
    for scene in ("default", "bridge-busy", "approval"):
        ses = oc_seed(scene)
        for s in ses.values():
            assert s["origin"] in ("bridge", "ticket", "opencode")
            assert s["origin"] != "bridge" or s["title"].startswith("Bridge: ")
            assert not s["busy"] or s["busySince"]
        assert ses["ses_h2"]["busy"] and ses["ses_h2"]["needsApproval"]  # the ~25 h ticket approval
