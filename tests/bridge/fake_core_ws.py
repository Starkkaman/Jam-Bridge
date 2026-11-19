"""A fake HA Core websocket as the Supervisor proxy presents it (ws://supervisor/core/websocket).

The auth dance (auth_required -> auth -> auth_ok / auth_invalid), crew_hub/subscribe (result, then a `full` event from
card/fixtures/snapshot-sample.json, then `machine` events via push_machine), crew_hub/get, crew_hub/command,
crew_hub/dismiss, crew_hub/transcript, a heartbeat (30 s logical) and drop() to cut every connection. Frames are
recorded without the token.

crew_hub/transcript answers the way the integration does (crew_hub 0.8.0): the opened plaintext
{session, cursor, more, turns} as the websocket result, or a websocket error whose code is the reason (not_live,
no_ack, bad_seal, ...). Live sessions on a machine with commands get "transcript" in their controls (crew-hubd 0.8+),
except NO_TRANSCRIPT (an older machine side). The conversations are invented (fixtures/transcripts.json); the cursor
is the number of turns already sent.

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
TRANSCRIPTS = pathlib.Path(__file__).resolve().parent / "fixtures" / "transcripts.json"
NO_TRANSCRIPT = "e5c7a913-0b2d-4f6e-8a14-c3d5e7f9a1b2"  # "error demo": its machine side predates transcript


def add_transcript_controls(machines):
    for m in machines.values():
        if not ((m.get("machine") or {}).get("commands") or {}).get("enabled"):
            continue
        for s in m.get("sessions") or []:
            c = s.get("controls")
            if isinstance(c, list) and "send" in c and "transcript" not in c and s.get("id") != NO_TRANSCRIPT:
                c.append("transcript")
    return machines


class FakeCore:
    def __init__(self, token="test-token", scale=1.0):
        self.token = token
        self.scale = scale
        self.machines = add_transcript_controls(json.loads(SNAPSHOT.read_text()))
        # "machine/session" -> list of machine turns (plaintext, as crew-hubd builds them)
        self.transcripts = {k: v for k, v in json.loads(TRANSCRIPTS.read_text()).items() if not k.startswith("_")}
        self.transcript_error = None  # a code: every crew_hub/transcript fails with it
        self.transcript_delay = 0
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

    def transcript_requests(self):
        return [f for f in self.frames if f.get("type") == "crew_hub/transcript"]

    def add_turn(self, key, turn):
        self.transcripts.setdefault(key, []).append(turn)

    def _transcript(self, frame):
        """(result, error code)"""
        if self.transcript_error:
            return None, self.transcript_error
        machine, session = frame.get("machine"), frame.get("session")
        m = self.machines.get(machine)
        if m is None:
            return None, "not_configured"
        s = next((x for x in m.get("sessions") or [] if x.get("id") == session), None)
        if s is None or s.get("status") == "ended" or m.get("status") != "online":
            return None, "not_live"
        if "transcript" not in (s.get("controls") or []):
            return None, "invalid_args"
        turns = self.transcripts.get(f"{machine}/{session}", [])
        after = frame.get("after")
        start = int(after[1:]) if isinstance(after, str) and after[:1] == "c" and after[1:].isdigit() else 0
        limit = max(1, min(40, int(frame.get("limit") or 20)))
        new = turns[start:]
        more = start == 0 and len(new) > limit
        sent = new[-limit:] if start == 0 else new[:limit]
        end = len(turns) if start == 0 else start + len(sent)
        return {"session": session, "cursor": f"c{end}", "more": more, "turns": copy.deepcopy(sent)}, None

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
        elif t == "crew_hub/transcript":
            if self.transcript_delay:
                await asyncio.sleep(self.transcript_delay * self.scale)
            result, code = self._transcript(frame)
            if code:
                await ws.send_json({"id": mid, "type": "result", "success": False,
                                    "error": {"code": code, "message": f"Transcript failed: {code}"}})
            else:
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
