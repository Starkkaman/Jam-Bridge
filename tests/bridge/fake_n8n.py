"""A fake n8n for The Bridge's tests and dev runs (aiohttp).

Serves tests/bridge/fixtures/n8n/*.json under /webhook/..., records every request (with the X-Bridge-Key header it
saw) and every POST body, and can delay, fail or answer 401. Delays are logical seconds times `scale`, the same
fast clock the server under test uses.

Standalone: python tests/bridge/fake_n8n.py --port 8098
"""

import argparse
import asyncio
import copy
import json
import pathlib

from aiohttp import web

FIX = pathlib.Path(__file__).resolve().parent / "fixtures" / "n8n"
QUIET_ERROR = "evening quiet hours (17:00-01:00): nothing applied. Send force: true to apply anyway."


def load(name):
    return json.loads((FIX / name).read_text())


class FakeN8n:
    def __init__(self, scale=1.0, require_key=None):
        self.scale = scale
        self.require_key = require_key  # when set, any other X-Bridge-Key (or none) gets 401
        self.requests = []  # dicts: method, path, query, key
        self.posts = []  # dicts: path, body, key
        self.delay = {}  # path -> logical seconds
        self.delay_once = {}  # path -> [logical seconds, ...] used by the next requests, one each
        self.fail = {}  # path -> HTTP status
        self.quiet_hours = False
        self.apply_flip_after = 0  # logical seconds before applied flags flip
        self.chat_reply = None  # None: echo
        self.status = load("status.json")
        self.tickets = load("tickets.json")
        self.inbox = load("inbox.json")
        self.library = load("library.json")
        for f in self.library["findings"][13:]:  # like the mockup: 14-25 not applied yet
            f["applied"] = False
        self.runs = load("runs.json")
        self.run_details = {r["id"]: r for r in (load("run-140.json"), load("run-error.json"))}
        self.outputs = {"svc:media-agent": load("agent-output-media.json")}
        local = FIX / "agent-output-netaudit.json"  # real security output: local only, never in git
        if local.exists():
            self.outputs["svc:netaudit"] = json.loads(local.read_text())
        # "Try again" (the NAS agent's status fields): the digest re-runs, the library agent re-runs as a scan
        for a in self.status["agents"]:
            if a.get("id") == "n8n:wfExample0000007":
                a["rerun"] = True
            elif a.get("id") == "svc:media-agent":
                a["rerun"], a["rerun_via"] = True, "bridge/library/scan"
        self.rerun_finish_after = 20  # logical seconds from run-now to the new run's success
        self.rerun_status = "success"
        self.scan_running = False
        self._next_run = 900
        self.precheck = {"playing": 0, "sessions": 0, "titles": [], "evening": False, "busy": False, "undo": True,
                         "undo_note": "Old trailers are kept for 7 days"}

    def app(self):
        app = web.Application()
        app.router.add_route("*", "/webhook/{path:.*}", self.handle)
        return app

    def keys_seen(self):
        return [r["key"] for r in self.requests]

    def count(self, path, method=None):
        return sum(1 for r in self.requests if r["path"] == path and (method is None or r["method"] == method))

    async def handle(self, request):
        path = request.match_info["path"]
        key = request.headers.get("X-Bridge-Key")
        self.requests.append({"method": request.method, "path": path, "query": dict(request.query), "key": key})
        body = None
        if request.method == "POST":
            try:
                body = await request.json()
            except ValueError:
                body = None
            self.posts.append({"path": path, "body": body, "key": key})
        if self.require_key is not None and key != self.require_key:
            return web.json_response({"message": "Authorization data is wrong!"}, status=401)
        if path in self.fail:
            return web.json_response({"message": "fake failure"}, status=self.fail[path])
        once = self.delay_once.get(path)
        wait = once.pop(0) if once else self.delay.get(path)
        if wait:
            await asyncio.sleep(wait * self.scale)
        handler = ROUTES.get((request.method, path))
        if handler is None:
            return web.json_response({"code": 404, "message": "webhook not registered"}, status=404)
        return await handler(self, request, body)

    async def _status(self, request, body):
        return web.json_response(self.status)

    async def _tickets(self, request, body):
        return web.json_response(self.tickets)

    async def _inbox(self, request, body):
        return web.json_response(self.inbox)

    async def _library(self, request, body):
        return web.json_response(self.library)

    async def _precheck(self, request, body):
        return web.json_response(self.precheck)

    async def _runs(self, request, body):
        return web.json_response(self.runs)

    async def _run(self, request, body):
        run = self.run_details.get(request.query.get("id"))
        if run is None:
            return web.json_response({"message": "Error in workflow"}, status=500)
        return web.json_response(run)

    async def _output(self, request, body):
        out = self.outputs.get(request.query.get("id"))
        if out is None:
            return web.json_response({"message": "Error in workflow"}, status=500)
        return web.json_response(out)

    async def _chat(self, request, body):
        body = body or {}
        reply = self.chat_reply if self.chat_reply is not None else f"echo: {body.get('query', '')}"
        return web.json_response({"output": reply})

    async def _ticket(self, request, body):
        body = body or {}
        if body.get("action") == "update":
            t = next((t for t in self.tickets if t["id"] == body.get("id")), None)
            if t is None:
                return web.json_response({"ok": False, "error": "no such ticket"})
            for k in ("status", "resolution"):
                if body.get(k) is not None:
                    t[k] = body[k]
            if body.get("comment"):
                comments = json.loads(t["comments"]) if t.get("comments") else []
                comments.append({"at": "2026-11-18T18:00:00.000Z", "author": body.get("author"),
                                 "text": body["comment"]})
                t["comments"] = json.dumps(comments)
            t["updatedAt"] = "2026-11-18T18:00:00.000Z"
            return web.json_response({"ok": True, "id": t["id"]})
        new_id = max([t["id"] for t in self.tickets] + [0]) + 1
        self.tickets.insert(0, {"id": new_id, "createdAt": "2026-11-18T18:00:00.000Z",
                                "updatedAt": "2026-11-18T18:00:00.000Z", "type": body.get("type"),
                                "agent": body.get("agent"), "agent_id": body.get("agent_id"),
                                "title": body.get("title"), "details": body.get("details"), "status": "open",
                                "source": body.get("source"), "resolution": None, "comments": None})
        return web.json_response({"ok": True, "id": new_id})

    async def _apply(self, request, body):
        body = body or {}
        if self.quiet_hours and not body.get("force"):
            return web.json_response({"ok": False, "error": QUIET_ERROR})
        ids = body.get("ids")
        targets = [f for f in self.library["findings"] if ids == "all" or f["id"] in (ids or [])]

        async def flip():
            if self.apply_flip_after:
                await asyncio.sleep(self.apply_flip_after * self.scale)
            for f in targets:
                f["applied"] = True

        if self.apply_flip_after:
            asyncio.ensure_future(flip())
        else:
            await flip()
        return web.json_response({"ok": True, "applied": len(targets)})

    async def _run_now(self, request, body):
        body = body or {}
        wf = str(body.get("workflowId") or "")
        wf = wf[4:] if wf.startswith("n8n:") else wf
        agent = next((a for a in self.status["agents"] if a.get("workflowId") == wf), None)
        if agent is None or agent.get("rerun") is not True or agent.get("rerun_via"):
            return web.json_response({"message": "Error in workflow"}, status=500)  # not re-runnable
        self._next_run += 1
        run = {"id": str(self._next_run), "status": "running", "mode": "integrated",
               "startedAt": "2026-11-18T18:00:00.000Z", "stoppedAt": None}

        async def finish():
            await asyncio.sleep(self.rerun_finish_after * self.scale)
            self.runs.insert(0, run)  # n8n lists an execution once it is saved
            await asyncio.sleep(self.rerun_finish_after * self.scale)
            run.update(status=self.rerun_status, stoppedAt="2026-11-18T18:00:05.000Z")

        asyncio.ensure_future(finish())
        return web.json_response({"ok": True, "workflowId": wf, "name": agent.get("name"),
                                  "startedAt": run["startedAt"], "note": "Started in the background"})

    async def _scan(self, request, body):
        if self.scan_running:
            return web.json_response({"message": "A scan is already running"}, status=409)
        return web.json_response({"started": True, "state": "running", "task": "library-scan"})


ROUTES = {
    ("GET", "agent-hub/status"): FakeN8n._status,
    ("GET", "bridge/tickets"): FakeN8n._tickets,
    ("GET", "bridge/inbox"): FakeN8n._inbox,
    ("GET", "bridge/library"): FakeN8n._library,
    ("GET", "bridge/library/precheck"): FakeN8n._precheck,
    ("GET", "bridge/runs"): FakeN8n._runs,
    ("GET", "bridge/run"): FakeN8n._run,
    ("GET", "bridge/agent-output"): FakeN8n._output,
    ("POST", "bridge/chat"): FakeN8n._chat,
    ("POST", "agent-hub/ticket"): FakeN8n._ticket,
    ("POST", "bridge/library/apply"): FakeN8n._apply,
    ("POST", "bridge/run-now"): FakeN8n._run_now,
    ("POST", "bridge/library/scan"): FakeN8n._scan,
}


def fresh_copy(obj):
    return copy.deepcopy(obj)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8098)
    ap.add_argument("--require-key")
    a = ap.parse_args()
    web.run_app(FakeN8n(require_key=a.require_key).app(), host="127.0.0.1", port=a.port)
