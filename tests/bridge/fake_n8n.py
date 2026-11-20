"""A fake n8n for The Bridge's tests and dev runs (aiohttp).

Serves tests/bridge/fixtures/n8n/*.json under /webhook/..., records every request (with the X-Bridge-Key header it
saw) and every POST body, and can delay, fail or answer 401. Delays are logical seconds times `scale`, the same
fast clock the server under test uses.

Standalone: python tests/bridge/fake_n8n.py --port 8098
"""

import argparse
import asyncio
import copy
import datetime
import json
import pathlib
import time

from aiohttp import web

FIX = pathlib.Path(__file__).resolve().parent / "fixtures" / "n8n"
QUIET_ERROR = "evening quiet hours (17:00-01:00): nothing applied. Send force: true to apply anyway."
NOT_BRIDGE = "only sessions started from The Bridge can be continued here; reply on the ticket or in OpenCode"
MIN = 60


def iso(ago_s):
    """An ISO time `ago_s` real seconds before now (the page counts "Working… 2:13" against its own clock)."""
    t = datetime.datetime.fromtimestamp(time.time() - ago_s, datetime.timezone.utc)
    return t.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def steps(n, name, status="done", ago_s=0):
    return [{"name": name, "status": status, "at": iso(ago_s)} for _ in range(n)]


OC_AGENTS = [
    {"id": "oc:engineer", "name": "Chief Engineer", "short_name": "the Engineer", "role": "engineering",
     "description": "Works through tickets and code changes in its own sandbox on the NAS. It writes the fix, tests it "
                    "there and hands it back for review."},
    {"id": "oc:ha-builder", "name": "HA builder", "short_name": "the HA builder", "role": "home",
     "description": "Builds automations, scripts and dashboards in a sandbox copy of Home Assistant, then proposes "
                    "them."},
]
SANDBOX_COPIES = [
    {"id": "n8n:wfSandbox00001", "workflowId": "wfSandbox00001", "name": "SANDBOX: Download Watcher",
     "kind": "n8n workflow", "role": "downloads", "state": "off", "active": False, "chat": False},
    {"id": "n8n:wfSandbox00002", "workflowId": "wfSandbox00002", "name": "SANDBOX: Agent Hub: Chat (Assist)",
     "kind": "n8n workflow", "role": "general", "state": "ok", "active": True, "chat": False},
]


def oc_seed(scene="default"):
    """Sessions per agent + their details, made-up, times relative to now. Scenes for screenshots:
    default (the Engineer on ticket #7, the HA builder waiting for approval), bridge-busy (the Engineer working on a
    Bridge chat), approval (that Bridge chat waits for approval to go live)."""
    ses = {
        "ses_b1": {"agent": "oc:engineer", "title": "Bridge: Why do the trailer fixes stall?", "origin": "bridge",
                   "updated": iso(12 * MIN), "busy": False, "needsApproval": False,
                   "messages": [
                       {"role": "user", "text": "The trailer fixes stall at about 40 %. Can you find out why, and fix "
                                                "it in your sandbox?", "at": iso(17 * MIN)},
                       {"role": "assistant", "text": "Found it. The apply step waits for Jellyfin's library refresh, "
                        "and that refresh gives up after 30 s when more than 100 titles changed. The batch then "
                        "stops without saying so.\n\nI'm testing a fix in the sandbox: refresh in batches of 25 and "
                        "retry once. Nothing touches the live library.", "at": iso(12 * MIN)}],
                   "tools": steps(5, "read", ago_s=16 * MIN) + steps(3, "grep", ago_s=15 * MIN)
                   + steps(2, "edit", ago_s=14 * MIN) + steps(1, "bash", ago_s=13 * MIN)},
        "ses_t7": {"agent": "oc:engineer", "title": "Ticket #7 · Trailer fixes stall at 40 %", "origin": "ticket",
                   "ticketId": 7, "updated": iso(40), "busy": True, "busySince": iso(4 * MIN + 10),
                   "needsApproval": False,
                   "messages": [{"role": "user", "text": "Ticket #7: Trailer fixes stall at 40 %.",
                                 "at": iso(4 * MIN + 10)}],
                   "tools": steps(4, "read", ago_s=3 * MIN) + steps(1, "bash", "running", 40)},
        "ses_t5": {"agent": "oc:engineer", "title": "Ticket #5 · Library scan skips new folders", "origin": "ticket",
                   "ticketId": 5, "updated": iso(26 * 3600), "busy": False, "needsApproval": False,
                   "messages": [{"role": "user", "text": "Ticket #5: Library scan skips new folders.",
                                 "at": iso(27 * 3600)},
                                {"role": "assistant", "text": "Fixed in the sandbox and handed back for review.",
                                 "at": iso(26 * 3600)}],
                   "tools": steps(6, "read", ago_s=26 * 3600) + steps(1, "edit", ago_s=26 * 3600)},
        "ses_o1": {"agent": "oc:engineer", "title": "Poster cache cleanup", "origin": "opencode",
                   "updated": iso(4 * 86400), "busy": False, "needsApproval": False,
                   "messages": [{"role": "user", "text": "Clean up the poster cache.", "at": iso(4 * 86400 + 600)},
                                {"role": "assistant", "text": "Done: 1.2 GB freed.", "at": iso(4 * 86400)}],
                   "tools": steps(2, "bash", ago_s=4 * 86400)},
        "ses_h1": {"agent": "oc:ha-builder", "title": "Bridge: Porch light at sunset, only when someone's home",
                   "origin": "bridge", "updated": iso(9 * MIN), "busy": True, "busySince": iso(11 * MIN),
                   "needsApproval": True, "approvalText": "homeassistant: write automation.porch_light_sunset",
                   "messages": [
                       {"role": "user", "text": "Turn the porch light on at sunset, but only when someone is home. "
                                                "Off at 23:00.", "at": iso(11 * MIN)},
                       {"role": "assistant", "text": "Built and tested it in the sandbox copy: one automation, a "
                        "sunset trigger, a \"someone home\" condition and an off step at 23:00. To add it to your Home "
                        "Assistant I need your approval in OpenCode.", "at": iso(9 * MIN)}],
                   "tools": steps(3, "read", ago_s=10 * MIN) + steps(1, "edit", ago_s=10 * MIN)
                   + steps(2, "bash", ago_s=9 * MIN) + steps(1, "homeassistant_write", "pending", 9 * MIN)},
        "ses_h2": {"agent": "oc:ha-builder", "title": "Ticket #6 · Bathroom fan runs all night", "origin": "ticket",
                   "ticketId": 6, "updated": iso(25 * 3600), "busy": True, "busySince": iso(25 * 3600 + 300),
                   "needsApproval": True, "approvalText": "shell: read /workspace/config/automations.yaml",
                   "messages": [{"role": "user", "text": "Ticket #6: Bathroom fan runs all night.",
                                 "at": iso(25 * 3600 + 300)}],
                   "tools": steps(2, "read", ago_s=25 * 3600) + steps(1, "bash", "pending", 25 * 3600)},
        "ses_h3": {"agent": "oc:ha-builder", "title": "Bridge: Energy dashboard: add the heat pump", "origin": "bridge",
                   "updated": iso(5 * 86400), "busy": False, "needsApproval": False,
                   "messages": [{"role": "user", "text": "Add the heat pump to the energy dashboard.",
                                 "at": iso(5 * 86400 + 900)},
                                {"role": "assistant", "text": "Added in the sandbox copy; it's in OpenCode for review.",
                                 "at": iso(5 * 86400)}],
                   "tools": steps(2, "read", ago_s=5 * 86400) + steps(1, "edit", ago_s=5 * 86400)},
    }
    if scene in ("bridge-busy", "approval"):
        b, t = ses["ses_b1"], ses["ses_t7"]
        t.update(busy=False, updated=iso(31 * MIN))
        t["tools"][-1]["status"] = "done"
        b.update(busy=True, busySince=iso(2 * MIN + 13), updated=iso(20))
        b["messages"].append({"role": "user", "text": "Does the fix also cover the nightly run?",
                              "at": iso(2 * MIN + 13)})
        b["tools"] += steps(2, "read", ago_s=MIN) + steps(1, "bash", "running", 20)
        if scene == "approval":
            b.update(needsApproval=True, approvalText="deploy: copy the fix to the live library agent")
            b["messages"].append({"role": "assistant", "text": "Yes: the nightly run uses the same apply step, so it "
                                  "gets the batches too. Tested both in the sandbox: 312 titles in 13 batches, no "
                                  "stalls.\n\nI'd like to put it live now. That needs your approval in OpenCode.",
                                  "at": iso(40)})
            b["tools"][-1]["status"] = "done"
            b["tools"].append({"name": "deploy", "status": "pending", "at": iso(30)})
    for sid, x in ses.items():
        x["id"] = sid
        x.setdefault("ticketId", None)
        x.setdefault("busySince", None)
        x.setdefault("approvalText", None)
    return ses


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
        # OpenCode (bridge/opencode/*): two agents; their status rows join agent-hub/status with two SANDBOX copies
        self.oc_sessions = oc_seed()
        self.oc_next = 0
        self.oc_reply_after = 30  # logical seconds from a message to the agent's reply
        self.oc_tasks = set()
        for a in OC_AGENTS:
            self.status["agents"].append({"id": a["id"], "name": a["name"], "kind": "opencode agent", "role": a["role"],
                                          "chat": True, "rerun": False, "state": "ok", "task": None})
        self.status["agents"].extend(copy.deepcopy(SANDBOX_COPIES))
        self.oc_sync()
        self.precheck = {"playing": 0, "sessions": 0, "titles": [], "evening": False, "busy": False, "undo": True,
                         "undo_note": "Old trailers are kept for 7 days"}

    def app(self):
        app = web.Application()
        app.router.add_route("*", "/webhook/{path:.*}", self.handle)
        app.router.add_get("/fake/opencode/scene", self._oc_scene)  # dev runs: switch the screenshot scene
        return app

    async def _oc_scene(self, request):
        self.oc_sessions = oc_seed(request.query.get("name") or "default")
        self.oc_sync()
        return web.json_response({"ok": True})

    # ---- OpenCode -------------------------------------------------------------------------------------------------
    def oc_sync(self):
        """The status rows follow the sessions, like the live agent-hub/status (state running, task "<title> since …")."""
        for row in self.status["agents"]:
            if row.get("kind") != "opencode agent":
                continue
            busy = [s for s in self.oc_sessions.values() if s["agent"] == row["id"] and s["busy"]]
            busy.sort(key=lambda s: s["busySince"] or "")
            row["state"] = "running" if busy else "ok"
            row["task"] = f"{busy[0]['title']} since {busy[0]['busySince'][11:16]}" if busy else None

    def oc_brief(self, s):
        return {k: s.get(k) for k in ("id", "title", "updated", "busy", "busySince", "needsApproval", "origin",
                                      "ticketId")}

    def oc_of(self, agent):
        return sorted((s for s in self.oc_sessions.values() if s["agent"] == agent), key=lambda s: s["updated"],
                      reverse=True)

    async def _oc_agents(self, request, body):
        out = []
        for a in OC_AGENTS:
            L = self.oc_of(a["id"])
            busy = sorted((s["busySince"] for s in L if s["busy"] and s["busySince"]))
            last = L[0] if L else None
            out.append({**a, "busy": any(s["busy"] for s in L), "busySince": busy[0] if busy else None,
                        "needsApproval": any(s["needsApproval"] for s in L),
                        "lastSession": {k: last[k] for k in ("id", "title", "updated")} if last else None})
        return web.json_response({"agents": out})

    async def _oc_list(self, request, body):
        agent = request.query.get("agent")
        if agent not in {a["id"] for a in OC_AGENTS}:
            return web.json_response({"ok": False, "error": f"unknown agent {agent}"}, status=400)
        try:
            limit = int(request.query.get("limit") or 20)
        except ValueError:
            limit = 0
        if not 1 <= limit <= 100:
            return web.json_response({"ok": False, "error": "limit must be 1-100"}, status=400)
        return web.json_response({"sessions": [self.oc_brief(s) for s in self.oc_of(agent)[:limit]]})

    async def _oc_session(self, request, body):
        sid = request.query.get("id")
        s = self.oc_sessions.get(sid)
        if s is None:
            return web.json_response({"ok": False, "error": f"session {sid} not found"}, status=400)
        return web.json_response(copy.deepcopy(s))

    async def _oc_message(self, request, body):
        body = body or {}
        agent, text, sid = body.get("agent"), body.get("text"), body.get("sessionId")
        if agent not in {a["id"] for a in OC_AGENTS}:
            return web.json_response({"ok": False, "error": f"unknown agent {agent}"}, status=400)
        if not isinstance(text, str) or not text.strip() or len(text) > 4000:
            return web.json_response({"ok": False, "error": "text must be 1-4000 characters"}, status=400)
        if sid is not None:
            s = self.oc_sessions.get(sid)
            if s is None or s["agent"] != agent:
                return web.json_response({"ok": False, "error": f"session {sid} not found"}, status=400)
            if s["origin"] != "bridge":
                return web.json_response({"ok": False, "error": NOT_BRIDGE}, status=400)
            if s["busy"]:
                return web.json_response({"ok": False, "error": "session_busy", "sessionId": sid}, status=409)
        else:
            run = next((s for s in self.oc_of(agent) if s["origin"] == "bridge" and s["busy"]), None)
            if run is not None:
                return web.json_response({"ok": False, "error": "agent_busy", "sessionId": run["id"]}, status=409)
            self.oc_next += 1
            sid = f"ses_new{self.oc_next}"
            self.oc_sessions[sid] = {"id": sid, "agent": agent, "title": "Bridge: " + text.strip()[:60],
                                     "origin": "bridge", "ticketId": None, "busySince": None, "approvalText": None,
                                     "needsApproval": False, "messages": [], "tools": []}
        s = self.oc_sessions[sid]
        s["messages"].append({"role": "user", "text": text, "at": iso(0)})
        s.update(busy=True, busySince=iso(0), updated=iso(0))
        self.oc_sync()

        async def reply():
            await asyncio.sleep(self.oc_reply_after * self.scale)
            s["messages"].append({"role": "assistant", "text": f"echo: {text}", "at": iso(0)})
            s["tools"].append({"name": "read", "status": "done", "at": iso(0)})
            s.update(busy=False, busySince=None, updated=iso(0))
            self.oc_sync()

        task = asyncio.ensure_future(reply())
        self.oc_tasks.add(task)
        task.add_done_callback(self.oc_tasks.discard)
        return web.json_response({"ok": True, "sessionId": sid})

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

    async def _ticket_update(self, request, body):
        # bridge/ticket-update: {id, status?, resolution?, comment?, author?}; no "action" field
        body = body or {}
        t = next((t for t in self.tickets if t["id"] == body.get("id")), None)
        if t is None:
            return web.json_response({"ok": False, "error": f"ticket {body.get('id')} not found"}, status=400)
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

    async def _ticket(self, request, body):
        # agent-hub/ticket only creates (like the real one: an update sent here is an empty ticket)
        body = body or {}
        if not body.get("title") and not body.get("details"):
            return web.json_response({"ok": False, "error": "empty ticket: a title or details is required"}, status=400)
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
    ("POST", "bridge/ticket-update"): FakeN8n._ticket_update,
    ("POST", "bridge/library/apply"): FakeN8n._apply,
    ("POST", "bridge/run-now"): FakeN8n._run_now,
    ("POST", "bridge/library/scan"): FakeN8n._scan,
    ("GET", "bridge/opencode/agents"): FakeN8n._oc_agents,
    ("GET", "bridge/opencode/sessions"): FakeN8n._oc_list,
    ("GET", "bridge/opencode/session"): FakeN8n._oc_session,
    ("POST", "bridge/opencode/message"): FakeN8n._oc_message,
}


def fresh_copy(obj):
    return copy.deepcopy(obj)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8098)
    ap.add_argument("--require-key")
    a = ap.parse_args()
    web.run_app(FakeN8n(require_key=a.require_key).app(), host="127.0.0.1", port=a.port)
