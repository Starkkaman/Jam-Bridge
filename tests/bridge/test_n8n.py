"""n8n adapter (allow-list, key header, timeouts, limits, redirects) and the pollers (cadence, single-flight, backoff)."""

import asyncio
import json
import pathlib

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from bridge.app import agents
from bridge.app.clock import Clock
from bridge.app.jobs import Poller
from bridge.app.n8n import ALLOWED, KEY_ERROR, N8n, N8nError
from conftest import KEY, SCALE, make_config

FIX = pathlib.Path(__file__).resolve().parent / "fixtures" / "n8n"


def client(tmp_path, url, key=KEY):
    return N8n(make_config(tmp_path, n8n_url=url, key=key), Clock(SCALE))


async def test_allow_list_is_enforced_before_any_io(tmp_path):
    n = client(tmp_path, "http://127.0.0.1:9")
    for method, path in [("GET", "agent-hub/chat"), ("POST", "agent-hub/status"), ("DELETE", "bridge/tickets"),
                         ("GET", "../rest/workflows"), ("POST", "agent-hub/chat")]:
        with pytest.raises(N8nError) as e:
            await n.request(method, path)
        assert e.value.code == "not_allowed"
    assert ("POST", "bridge/chat") in ALLOWED and ("POST", "agent-hub/chat") not in ALLOWED
    assert len(ALLOWED) == 13
    await n.close()


async def test_not_configured(tmp_path):
    n = N8n(make_config(tmp_path), Clock(SCALE))
    with pytest.raises(N8nError) as e:
        await n.status()
    assert e.value.code == "not_configured"


async def test_key_header_and_401_banner(tmp_path, fake_n8n, log_capture):
    n = client(tmp_path, fake_n8n.url)
    data = await n.status()
    assert data["overall"] == "ok"
    assert fake_n8n.requests[-1]["key"] == KEY
    assert n.health["ok"] is True
    fake_n8n.require_key = "another-key"
    with pytest.raises(N8nError) as e:
        await n.tickets()
    assert e.value.code == "key" and e.value.message == KEY_ERROR
    assert n.health["code"] == "key" and n.health["error"] == KEY_ERROR
    fake_n8n.fail["bridge/inbox"] = 403
    fake_n8n.require_key = None
    with pytest.raises(N8nError) as e:
        await n.inbox()
    assert e.value.code == "key"
    # firstContact is logged once, with the status only
    logs = log_capture.getvalue()
    assert logs.count("n8n.firstContact") == 1 and "n8n.firstContact HTTP 200" in logs
    await n.close()


async def test_no_key_no_header(tmp_path, fake_n8n):
    n = client(tmp_path, fake_n8n.url, key=None)
    await n.status()
    assert fake_n8n.requests[-1]["key"] is None
    await n.close()


async def test_chat_timeout_is_120s(tmp_path, fake_n8n):
    n = client(tmp_path, fake_n8n.url)
    fake_n8n.delay["bridge/chat"] = 130
    with pytest.raises(N8nError) as e:
        await n.chat({"query": "hi"})
    assert e.value.code == "timeout"
    fake_n8n.delay["bridge/chat"] = 100  # under 120 s: fine
    assert (await n.chat({"query": "hi"}))["output"] == "echo: hi"
    fake_n8n.delay["bridge/tickets"] = 16  # GETs time out after 15 s
    with pytest.raises(N8nError) as e:
        await n.tickets()
    assert e.value.code == "timeout"
    await n.close()


async def _tiny_server(handler):
    app = web.Application()
    app.router.add_route("*", "/webhook/{p:.*}", handler)
    server = TestServer(app, host="127.0.0.1")
    await server.start_server()
    return server


async def test_no_redirects_and_size_limit(tmp_path):
    async def redirect(request):
        if request.match_info["p"] == "bridge/tickets":
            raise web.HTTPFound("http://127.0.0.1:9/elsewhere")
        if request.match_info["p"] == "bridge/inbox":
            return web.Response(body=b"[" + b'"x",' * 400_000 + b'"x"]', content_type="application/json")
        return web.Response(text="<html>not json</html>")

    server = await _tiny_server(redirect)
    n = client(tmp_path, str(server.make_url("")).rstrip("/"))
    with pytest.raises(N8nError) as e:
        await n.tickets()
    assert e.value.code == "http" and e.value.status == 302
    with pytest.raises(N8nError) as e:
        await n.inbox()
    assert e.value.code == "too_big"
    with pytest.raises(N8nError) as e:
        await n.library()
    assert e.value.code == "bad_json"
    await n.close()
    await server.close()


async def test_unreachable(tmp_path):
    n = client(tmp_path, "http://127.0.0.1:9")
    with pytest.raises(N8nError) as e:
        await n.status()
    assert e.value.code == "unreachable" and n.health["ok"] is False and n.health["since"]
    await n.close()


async def test_shared_single_flight(tmp_path, fake_n8n):
    n = client(tmp_path, fake_n8n.url)
    fake_n8n.delay["bridge/runs"] = 5
    results = await asyncio.gather(*(n.runs("wfExample0000007") for _ in range(5)))
    assert all(r == results[0] for r in results)
    assert fake_n8n.count("bridge/runs") == 1
    await n.close()


# ---- agents ----------------------------------------------------------------------------------------------------------
def test_agent_grouping_and_ticket_matching():
    status = json.loads((FIX / "status.json").read_text())
    g = agents.grouped(status)
    names = {k: [a["name"] for a in v] for k, v in g.items()}
    assert "The Bridge: API" in names["system"] and "Phone Reply Agent" in names["system"]
    assert "Agent Hub: Status" in names["system"] and "Agent Hub: Chat (Assist)" in names["system"]
    assert "Download Watcher" in names["agents"] and "Security Weekly Review" in names["agents"]
    assert all(a["id"].startswith("svc:") for a in g["services"])
    assert not any(a["hasRuns"] for a in g["system"])
    all_agents = status["agents"]
    assert agents.match_agent("Jellyfin library agent", all_agents) == "svc:media-agent"
    assert agents.match_agent("Library", all_agents) == "svc:media-agent"
    assert agents.match_agent("the download watcher", all_agents) == "n8n:wfExample0000006"
    assert agents.match_agent("test", all_agents) is None
    assert agents.match_agent(None, all_agents) is None
    assert agents.match_agent("Download", all_agents) is None  # ambiguous: Watcher and Daily Summary
    assert agents.AGENT_ID_RE.match("svc:media-agent") and agents.AGENT_ID_RE.match("claude:abc-1")
    assert not agents.AGENT_ID_RE.match("svc:bad id") and not agents.AGENT_ID_RE.match("x:y")


# ---- pollers ---------------------------------------------------------------------------------------------------------
async def test_poller_single_flight():
    gate = asyncio.Event()
    calls = []

    async def fetch():
        calls.append(1)
        await gate.wait()

    p = Poller("t", fetch, Clock(SCALE), lambda: True, 30, 300)
    first = asyncio.ensure_future(p.tick())
    await asyncio.sleep(0)
    assert await p.tick() == "skipped" and await p.tick() == "skipped"
    gate.set()
    assert await first == "ok"
    assert len(calls) == 1 and p.skipped == 2


async def test_poller_backoff_and_status_retry():
    visible = {"v": True}

    async def boom():
        raise N8nError("unreachable", "down")

    tickets = Poller("tickets", boom, Clock(SCALE), lambda: visible["v"], 60, 900)
    delays = []
    for _ in range(5):
        await tickets.tick()
        delays.append(tickets.next_delay())
    assert delays == [120, 240, 300, 300, 300]  # x2 up to 5 min
    visible["v"] = False
    assert tickets.next_delay() == 900  # never faster than the hidden cadence

    status = Poller("status", boom, Clock(SCALE), lambda: visible["v"], 30, 300, error_retry_s=60)
    for _ in range(3):
        await status.tick()
        assert status.next_delay() == 60  # agent-hub/status retries every 60 s

    async def ok():
        return None

    tickets.fetch = ok
    await tickets.tick()
    assert tickets.failures == 0 and tickets.next_delay() == 900
    visible["v"] = True
    assert tickets.next_delay() == 60


async def test_poller_cadence_and_kick():
    calls = []

    async def fetch():
        calls.append(1)

    p = Poller("inbox", fetch, Clock(SCALE), lambda: True, 120, 600)
    p.start()
    await asyncio.sleep(0.05)
    assert len(calls) == 1  # first tick at start; the next after 120 logical s (1.2 s real)
    p.kick()
    await asyncio.sleep(0.05)
    assert len(calls) == 2  # a kick runs it right away ("right after a chat reply, ticket write or apply")
    await asyncio.sleep(1.3)
    assert len(calls) == 3
    await p.stop()


async def test_library_polls_every_15s_while_applying(bridge):
    lib = bridge.jobs.pollers["library"]
    assert lib.interval() == 3600  # nobody watching
    bridge.jobs.apply_job = {"state": "running"}
    assert lib.interval() == 15
    bridge.jobs.apply_job = None
    q = bridge.hub.add()
    assert lib.interval() == 600 and bridge.jobs.pollers["status"].interval() == 30
    bridge.hub.remove(q)
    assert bridge.jobs.pollers["status"].interval() == 300
