"""crew_hub adapter: frame allow-list, auth, reconnect + resubscribe, passthrough to SSE, link-down errors."""

import pytest

from bridge.app.clock import Clock
from bridge.app.crew import RECONNECT_DELAYS, Crew, CrewError, FrameRefused, check_frame
from conftest import SCALE, TOKEN, cid, wait_for


@pytest.mark.parametrize("frame", [
    {"type": "call_service", "domain": "homeassistant", "service": "restart"},
    {"type": "supervisor/api", "endpoint": "/backups"},
    {"type": "subscribe_events"},
    {"type": "crew_hub/command", "machine": "nas", "action": "start", "args": {}},
    {"type": "crew_hub/command", "machine": "nas", "args": {}},
    {"type": "get_states"},
    "not a dict",
])
def test_frame_allow_list_refuses(frame):
    with pytest.raises(FrameRefused):
        check_frame(frame)


@pytest.mark.parametrize("frame", [
    {"type": "auth", "access_token": "x"}, {"type": "crew_hub/subscribe"}, {"type": "crew_hub/get"},
    {"type": "crew_hub/dismiss", "machine": "nas", "session": "s"}, {"type": "unsubscribe_events"},
    *({"type": "crew_hub/command", "machine": "nas", "action": a, "args": {}}
      for a in ("send", "answer", "interrupt", "stop")),
])
def test_frame_allow_list_accepts(frame):
    assert check_frame(frame) is frame


async def test_connect_auth_subscribe(fake_core):
    crew = Crew(fake_core.url, Clock(SCALE))
    crew.start()
    await wait_for(lambda: crew.connected and crew.machines)
    assert set(crew.machines) == {"nas", "mac"}
    assert fake_core.auth_ok == 1 and fake_core.subscribes == 1
    assert {"type": "auth"} in fake_core.frames  # recorded without the token
    assert TOKEN not in repr(fake_core.frames) and TOKEN not in repr(vars(crew))
    await crew.stop()


async def test_auth_failure_retries_with_backoff(fake_core, monkeypatch, log_capture):
    monkeypatch.setenv("SUPERVISOR_TOKEN", "wrong-token")
    crew = Crew(fake_core.url, Clock(SCALE))
    crew.start()
    await wait_for(lambda: fake_core.auth_failed >= 2, timeout=3)
    assert not crew.connected and crew.error == "auth_invalid"
    monkeypatch.setenv("SUPERVISOR_TOKEN", TOKEN)  # read from the environment on each connect
    await wait_for(lambda: crew.connected, timeout=3)
    assert "wrong-token" not in log_capture.getvalue()
    await crew.stop()


async def test_reconnect_and_resubscribe(bridge, fake_core):
    from test_api import SSE

    assert RECONNECT_DELAYS == (1, 2, 5, 10, 30)
    async with SSE(bridge) as sse:
        fake_core.refuse = True
        await fake_core.drop()
        await wait_for(lambda: not bridge.crew.connected)
        r, d = await bridge.post("/api/claude/command", {"machine": "nas", "action": "send", "clientId": cid(),
                                                         "args": {"session": "s1", "text": "hi"}})
        assert r.status == 503 and d["code"] == "crew_disconnected"
        r, d = await bridge.get("/api/state")
        assert d["health"]["crew"]["connected"] is False
        fake_core.refuse = False
        await wait_for(lambda: bridge.crew.connected, timeout=3)
        assert fake_core.connections == 2 and fake_core.subscribes == 2
        await wait_for(lambda: [e for e in sse.of("health") if e.get("crew", {}).get("connected") is True])
        assert [e for e in sse.of("health") if e.get("crew", {}).get("connected") is False]
        await wait_for(lambda: sse.of("crew"))  # the full snapshot again after resubscribing
        assert set(sse.of("crew")[-1]["machines"]) == {"nas", "mac"}
    acts = await bridge.db.a("list_actions")
    assert acts[0]["kind"] == "claude.send" and acts[0]["outcome"] == "error"


async def test_command_when_never_connected(tmp_path):
    crew = Crew("ws://127.0.0.1:9/core/websocket", Clock(SCALE))
    with pytest.raises(CrewError) as e:
        await crew.command("nas", "send", {"session": "s", "text": "x"})
    assert e.value.code == "disconnected"


async def test_command_timeout(bridge, fake_core):
    fake_core.command_delay = 50  # crew_hub answers after 45 logical s at most
    r, d = await bridge.post("/api/claude/command", {"machine": "nas", "action": "send", "clientId": cid(),
                                                     "args": {"session": "s1", "text": "hi"}})
    assert r.status == 503 and d["code"] == "crew_timeout"
