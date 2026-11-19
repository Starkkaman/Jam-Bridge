"""crew_hub adapter: one websocket to ws://supervisor/core/websocket, authenticated with SUPERVISOR_TOKEN.

The token is read from the environment on each connect and is never logged, stored or returned. The Supervisor proxy
runs crew_hub's admin-only commands as its "Supervisor" system user (docs/BRIDGE.md §1 row 1), so the frame allow-list
below is what keeps this app narrow: auth, crew_hub/subscribe, crew_hub/get, crew_hub/command (send, answer,
interrupt, stop), crew_hub/dismiss, crew_hub/transcript (one session's recent conversation, read on demand and never
stored: docs/COMMANDS.md § transcript), unsubscribe_events. Nothing else, and no REST.

Reconnects after 1, 2, 5, 10, 30 s and resubscribes.
"""

import asyncio
import contextlib
import itertools
import json
import logging
import os

import aiohttp

LOG = logging.getLogger("bridge.crew")

ALLOWED_TYPES = frozenset({"auth", "crew_hub/subscribe", "crew_hub/get", "crew_hub/command", "crew_hub/dismiss",
                           "crew_hub/transcript", "unsubscribe_events"})
COMMAND_ACTIONS = frozenset({"send", "answer", "interrupt", "stop"})
RECONNECT_DELAYS = (1, 2, 5, 10, 30)
COMMAND_TIMEOUT = 45  # crew_hub waits for the machine's ack itself


class FrameRefused(Exception):
    pass


class CrewError(Exception):
    def __init__(self, code, message=""):
        super().__init__(message or code)
        self.code = code


def check_frame(frame):
    """Raises FrameRefused for anything outside the allow-list (checked before every send)."""
    if not isinstance(frame, dict):
        raise FrameRefused("frame must be an object")
    t = frame.get("type")
    if t not in ALLOWED_TYPES:
        raise FrameRefused(f"frame type {t!r} is not allowed")
    if t == "crew_hub/command" and frame.get("action") not in COMMAND_ACTIONS:
        raise FrameRefused(f"crew_hub/command action {frame.get('action')!r} is not allowed")
    return frame


class Crew:
    def __init__(self, url, clock, token_env="SUPERVISOR_TOKEN"):
        self.url = url
        self.clock = clock
        self.token_env = token_env
        self.machines = {}
        self.connected = False
        self.connected_at = None
        self.error = None
        self.on_full = None  # callback(machines)
        self.on_machine = None  # callback(machine_id, data)
        self.on_connection = None  # callback(connected)
        self._ws = None
        self._session = None
        self._ids = itertools.count(1)
        self._pending = {}
        self._sub_id = None
        self._task = None
        self._stopping = False
        self.connects = 0  # successful auth + subscribe count (tests)

    # ---- lifecycle ------------------------------------------------------------------------------------------------
    def start(self):
        if self._task is None:
            self._task = asyncio.ensure_future(self._run())

    async def stop(self):
        self._stopping = True
        if self._ws is not None:
            await self._ws.close()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def _run(self):
        attempt = 0
        self._session = aiohttp.ClientSession(trust_env=False)
        while not self._stopping:
            subscribed = False
            try:
                subscribed = await self._connect_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.error = f"{type(exc).__name__}"
                LOG.warning("crew_hub websocket: %s", self.error)
            finally:
                self._set_connected(False)
                self._fail_pending("disconnected")
            if self._stopping:
                break
            attempt = 0 if subscribed else attempt
            delay = RECONNECT_DELAYS[min(attempt, len(RECONNECT_DELAYS) - 1)]
            attempt += 1
            await self.clock.sleep(delay)

    async def _connect_once(self):
        """Returns True if the connection got as far as a subscription (resets the backoff)."""
        token = os.environ.get(self.token_env, "")
        async with self._session.ws_connect(self.url, heartbeat=55, autoping=True, max_msg_size=32 * 1024 * 1024,
                                            timeout=aiohttp.ClientWSTimeout(ws_close=10)) as ws:
            self._ws = ws
            first = await self._recv(ws, timeout=20)
            if not first or first.get("type") != "auth_required":
                raise CrewError("protocol", "no auth_required")
            await self._send({"type": "auth", "access_token": token})
            reply = await self._recv(ws, timeout=20)
            if not reply or reply.get("type") != "auth_ok":
                self.error = "auth_invalid"
                LOG.warning("crew_hub websocket: authentication refused")
                return False
            sub_id = next(self._ids)
            self._sub_id = sub_id
            fut = asyncio.get_running_loop().create_future()
            self._pending[sub_id] = fut
            await self._send({"id": sub_id, "type": "crew_hub/subscribe"})
            reader = asyncio.ensure_future(self._read(ws))
            try:
                result = await asyncio.wait_for(asyncio.shield(fut), self.clock.secs(20))
            except Exception:
                reader.cancel()
                raise
            if not result.get("success"):
                self.error = (result.get("error") or {}).get("code") or "subscribe_failed"
                LOG.warning("crew_hub/subscribe failed: %s", self.error)
                reader.cancel()
                return False
            self.error = None
            self.connects += 1
            self._set_connected(True)
            await reader
            return True

    async def _recv(self, ws, timeout):
        msg = await asyncio.wait_for(ws.receive(), self.clock.secs(timeout))
        if msg.type != aiohttp.WSMsgType.TEXT:
            return None
        data = json.loads(msg.data)
        return data if isinstance(data, dict) else None

    async def _read(self, ws):
        async for msg in ws:
            if msg.type != aiohttp.WSMsgType.TEXT:
                if msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSE):
                    break
                continue
            try:
                data = json.loads(msg.data)
            except ValueError:
                continue
            for item in data if isinstance(data, list) else [data]:
                if isinstance(item, dict):
                    self._dispatch(item)
        self._ws = None

    def _dispatch(self, msg):
        mid = msg.get("id")
        t = msg.get("type")
        if t == "result":
            fut = self._pending.pop(mid, None)
            if fut is not None and not fut.done():
                fut.set_result(msg)
        elif t == "event" and mid == self._sub_id:
            ev = msg.get("event") or {}
            if ev.get("type") == "full" and isinstance(ev.get("machines"), dict):
                self.machines = ev["machines"]
                if self.on_full:
                    self.on_full(self.machines)
            elif ev.get("type") == "machine" and isinstance(ev.get("id"), str):
                if ev.get("data") is None:
                    self.machines.pop(ev["id"], None)
                else:
                    self.machines[ev["id"]] = ev["data"]
                if self.on_machine:
                    self.on_machine(ev["id"], ev.get("data"))

    def _set_connected(self, value):
        if value == self.connected:
            return
        self.connected = value
        if value:
            self.connected_at = self.clock.now_ms()
            LOG.info("crew_hub websocket connected (%d machines)", len(self.machines))
        else:
            LOG.info("crew_hub websocket disconnected")
        if self.on_connection:
            self.on_connection(value)

    def _fail_pending(self, code):
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(CrewError(code))
        self._pending.clear()

    async def _send(self, frame):
        check_frame(frame)
        if self._ws is None or self._ws.closed:
            raise CrewError("disconnected", "crew_hub is not connected")
        await self._ws.send_str(json.dumps(frame))

    async def _call(self, frame, timeout=COMMAND_TIMEOUT):
        check_frame(frame)
        if not self.connected:
            raise CrewError("disconnected", "crew_hub is not connected")
        mid = next(self._ids)
        frame = {"id": mid, **frame}
        fut = asyncio.get_running_loop().create_future()
        self._pending[mid] = fut
        try:
            await self._send(frame)
            msg = await asyncio.wait_for(fut, self.clock.secs(timeout))
        except TimeoutError:
            raise CrewError("timeout", "crew_hub did not answer") from None
        finally:
            self._pending.pop(mid, None)
        if not msg.get("success"):
            err = msg.get("error") or {}
            raise CrewError(str(err.get("code") or "failed")[:60], str(err.get("message") or "")[:200])
        return msg.get("result")

    # ---- public ---------------------------------------------------------------------------------------------------
    async def command(self, machine, action, args):
        """{ok, error?, result?} as crew_hub returns it. Raises CrewError when the link is down."""
        res = await self._call({"type": "crew_hub/command", "machine": machine, "action": action, "args": args})
        return res if isinstance(res, dict) else {"ok": False, "error": "bad_result"}

    async def dismiss(self, machine, session, agents=None, session_dismissed=False):
        res = await self._call({"type": "crew_hub/dismiss", "machine": machine, "session": session,
                                "agents": list(agents or []), "session_dismissed": bool(session_dismissed)})
        return res if isinstance(res, dict) else {"ok": False, "error": "bad_result"}

    async def transcript(self, machine, session, after=None, limit=20):
        """crew_hub/transcript: the integration asks the machine (signed), checks the sealed answer and sends the
        plaintext {session, cursor, more, turns} as the websocket result; a failure is a websocket error whose code is
        the reason (not_live, no_ack, bad_seal, ...). Returns {ok, error?, result?} like command(); raises CrewError
        only when the link itself is down or slow. Nothing here keeps it."""
        frame = {"type": "crew_hub/transcript", "machine": machine, "session": session, "limit": int(limit)}
        if after:
            frame["after"] = after
        try:
            res = await self._call(frame)
        except CrewError as err:
            if err.code in ("disconnected", "timeout"):
                raise
            return {"ok": False, "error": err.code}
        if not isinstance(res, dict) or not isinstance(res.get("turns"), list):
            return {"ok": False, "error": "bad_result"}
        return {"ok": True, "result": res}

    def find_session(self, machine, session_id):
        m = self.machines.get(machine)
        if not isinstance(m, dict):
            return None
        for s in m.get("sessions") or []:
            if isinstance(s, dict) and s.get("id") == session_id:
                return s
        return None
