"""A fake HA Core websocket as the Supervisor proxy presents it (ws://supervisor/core/websocket).

The auth dance (auth_required -> auth -> auth_ok / auth_invalid), crew_hub/subscribe (result, then a `full` event from
card/fixtures/snapshot-sample.json, then `machine` events via push_machine), crew_hub/get, crew_hub/command,
crew_hub/dismiss, a heartbeat (30 s logical) and drop() to cut every connection. Frames are recorded without the token.

Standalone: python tests/bridge/fake_core_ws.py --port 8097 (token "dev-token")
"""

import argparse
import asyncio
import copy
import json
import pathlib

from aiohttp import WSMsgType, web

ROOT = pathlib.Path(__file__).resolve().parents[2]
SNAPSHOT = ROOT / "card" / "fixtures" / "snapshot-sample.json"


class FakeCore:
    def __init__(self, token="test-token", scale=1.0):
        self.token = token
        self.scale = scale
        self.machines = json.loads(SNAPSHOT.read_text())
        self.frames = []  # every frame after auth, plus {"type": "auth"} without the token
        self.connections = 0
        self.auth_ok = 0
        self.auth_failed = 0
        self.subscribes = 0
        self.command_result = lambda frame: {"ok": True}
        self.command_delay = 0
        self.refuse = False  # True: new connections get HTTP 503 (Core restarting)
        self._sockets = set()
        self._subs = {}  # ws -> subscription id

    def app(self):
        app = web.Application()
        app.router.add_get("/core/websocket", self.handle)
        return app

    async def drop(self):
        for ws in list(self._sockets):
            await ws.close()

    async def push_machine(self, machine_id, data):
        if data is None:
            self.machines.pop(machine_id, None)
        else:
            self.machines[machine_id] = data
        for ws, sub in list(self._subs.items()):
            await ws.send_json({"id": sub, "type": "event", "event": {"type": "machine", "id": machine_id,
                                                                      "data": data}})

    def commands(self):
        return [f for f in self.frames if f.get("type") == "crew_hub/command"]

    async def handle(self, request):
        if self.refuse:
            return web.Response(status=503, text="Core is restarting")
        ws = web.WebSocketResponse(heartbeat=30 * self.scale, autoping=True)
        await ws.prepare(request)
        self.connections += 1
        self._sockets.add(ws)
        try:
            await ws.send_json({"type": "auth_required", "ha_version": "2026.10.4"})
            msg = await ws.receive()
            if msg.type != WSMsgType.TEXT:
                return ws
            auth = json.loads(msg.data)
            self.frames.append({"type": auth.get("type")})
            if auth.get("type") != "auth" or auth.get("access_token") != self.token:
                self.auth_failed += 1
                await ws.send_json({"type": "auth_invalid", "message": "Invalid access token or password"})
                await ws.close()
                return ws
            self.auth_ok += 1
            await ws.send_json({"type": "auth_ok", "ha_version": "2026.10.4"})
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                frame = json.loads(msg.data)
                self.frames.append(frame)
                await self._frame(ws, frame)
        finally:
            self._sockets.discard(ws)
            self._subs.pop(ws, None)
        return ws

    async def _frame(self, ws, frame):
        mid, t = frame.get("id"), frame.get("type")
        if t == "crew_hub/subscribe":
            self.subscribes += 1
            self._subs[ws] = mid
            await ws.send_json({"id": mid, "type": "result", "success": True, "result": None})
            await ws.send_json({"id": mid, "type": "event",
                                "event": {"type": "full", "machines": copy.deepcopy(self.machines)}})
        elif t == "crew_hub/get":
            await ws.send_json({"id": mid, "type": "result", "success": True,
                                "result": {"machines": copy.deepcopy(self.machines)}})
        elif t == "crew_hub/command":
            if self.command_delay:
                await asyncio.sleep(self.command_delay * self.scale)
            if frame.get("machine") not in self.machines:
                result = {"ok": False, "error": "not_configured"}
            else:
                result = self.command_result(frame)
            await ws.send_json({"id": mid, "type": "result", "success": True, "result": result})
        elif t == "crew_hub/dismiss":
            await ws.send_json({"id": mid, "type": "result", "success": True, "result": {"ok": True}})
        elif t == "unsubscribe_events":
            await ws.send_json({"id": mid, "type": "result", "success": True, "result": None})
        else:
            await ws.send_json({"id": mid, "type": "result", "success": False,
                                "error": {"code": "unknown_command", "message": "Unknown command."}})


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8097)
    a = ap.parse_args()
    web.run_app(FakeCore(token="dev-token").app(), host="127.0.0.1", port=a.port)
