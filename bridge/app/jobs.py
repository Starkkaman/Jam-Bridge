"""Background work: n8n pollers, the chat sender, library apply jobs, Claude status rows and the nightly prune.

Long calls never hold a browser request: the API stores a row, starts work here and returns 202; results arrive over
SSE and are in the DB even when no browser is open.
"""

import asyncio
import contextlib
import datetime
import logging
import uuid

from . import agents as agents_mod
from .crew import CrewError
from .db import QUEUE_DROP_MS
from .n8n import N8nError

LOG = logging.getLogger("bridge.jobs")

STATUS_ERROR_RETRY = 60  # NAS agent's request: agent-hub/status retries every 60 s
BACKOFF_CAP = 300
APPLY_LIBRARY_POLL = 15
RERUN_POLL = 10  # bridge/runs while a re-run is being watched
RERUN_WATCH = 15 * 60
REPLAY_CLIP = 1500
PRUNE_AT = (3, 30)  # local time


class Poller:
    """One n8n GET on a cadence. Single in-flight (a tick is skipped while the previous call is open); errors back off
    x2 up to 5 min (or retry at a fixed interval), never faster than the normal cadence."""

    def __init__(self, name, fetch, clock, visible_fn, visible_s, hidden_s, error_retry_s=None, interval_fn=None):
        self.name = name
        self.fetch = fetch
        self.clock = clock
        self.visible_fn = visible_fn
        self.visible_s = visible_s
        self.hidden_s = hidden_s
        self.error_retry_s = error_retry_s
        self.interval_fn = interval_fn
        self.failures = 0
        self.inflight = False
        self.calls = 0
        self.skipped = 0
        self.last_ok_at = None
        self._kick = asyncio.Event()
        self._task = None

    def interval(self):
        if self.interval_fn:
            custom = self.interval_fn()
            if custom:
                return custom
        return self.visible_s if self.visible_fn() else self.hidden_s

    def next_delay(self):
        normal = self.interval()
        if not self.failures:
            return normal
        if self.error_retry_s:
            return self.error_retry_s
        return min(normal * 2 ** self.failures, max(normal, BACKOFF_CAP))

    async def tick(self):
        if self.inflight:
            self.skipped += 1
            return "skipped"
        self.inflight = True
        self.calls += 1
        try:
            await self.fetch()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            self.failures += 1
            if not isinstance(exc, N8nError):
                LOG.exception("poller %s failed", self.name)
            return "error"
        finally:
            self.inflight = False
        self.failures = 0
        self.last_ok_at = self.clock.now_ms()
        return "ok"

    def kick(self):
        self._kick.set()

    def start(self):
        if self._task is None:
            self._task = asyncio.ensure_future(self._run())

    async def stop(self):
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run(self):
        while True:
            await self.tick()
            self._kick.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._kick.wait(), self.clock.secs(self.next_delay()))


class Jobs:
    def __init__(self, db, n8n, crew, hub, clock):
        self.db = db
        self.n8n = n8n
        self.crew = crew
        self.hub = hub
        self.clock = clock
        self.jobs = {}  # job id -> dict
        self.apply_job = None
        self._chat_workers = {}
        self._tasks = set()
        self.matcher_agents = []
        vis = lambda: self.hub.visible  # noqa: E731
        self.pollers = {
            "status": Poller("status", self.fetch_status, clock, vis, 30, 300, error_retry_s=STATUS_ERROR_RETRY),
            "tickets": Poller("tickets", self.fetch_tickets, clock, vis, 60, 900),
            "inbox": Poller("inbox", self.fetch_inbox, clock, vis, 120, 600),
            "library": Poller("library", self.fetch_library, clock, vis, 600, 3600,
                              interval_fn=lambda: APPLY_LIBRARY_POLL if self.apply_running else None),
        }
        self._prune_task = None

    # ---- lifecycle ------------------------------------------------------------------------------------------------
    async def start(self, pollers=True):
        recovered = await self.db.a("recover_on_start")
        if recovered:
            LOG.info("%d message(s) were sending at the last stop; marked unknown", len(recovered))
        cached = await self.db.a("cache_get", "n8n.status")
        if cached and cached.get("data"):
            self.matcher_agents = cached["data"].get("agents") or []
        for tid in await self.db.a("threads_with_queued"):
            self.enqueue(tid)
        if pollers:
            for p in self.pollers.values():
                p.start()
            self._prune_task = asyncio.ensure_future(self._prune_loop())

    async def stop(self):
        for p in self.pollers.values():
            await p.stop()
        tasks = [t for t in [self._prune_task, *self._chat_workers.values(), *self._tasks] if t]
        for t in tasks:
            t.cancel()
        for t in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t

    def spawn(self, coro):
        t = asyncio.ensure_future(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)
        return t

    def on_visibility(self, visible):
        if visible:
            for p in self.pollers.values():
                p.kick()

    @property
    def apply_running(self):
        return self.apply_job is not None and self.apply_job["state"] == "running"

    # ---- pollers --------------------------------------------------------------------------------------------------
    async def fetch_status(self):
        try:
            data = await self.n8n.status()
        except N8nError as err:
            await self.db.a("cache_put", "n8n.status", None, ok=False, error=err.message)
            cached = await self.db.a("cache_get", "n8n.status")
            self.hub.publish("n8n", {"status": cached and cached["data"], "at": cached and cached["at"],
                                     "error": err.message, "code": err.code})
            raise
        if not isinstance(data, dict):
            raise N8nError("bad_json", "agent-hub/status is not an object")
        await self.db.a("cache_put", "n8n.status", data)
        self.matcher_agents = data.get("agents") or []
        self.hub.publish("n8n", {"status": data, "at": self.clock.now_ms()})

    async def fetch_tickets(self):
        data = await self.n8n.tickets()
        if not isinstance(data, list):
            raise N8nError("bad_json", "bridge/tickets is not a list")
        agents = self.matcher_agents
        changed = await self.db.a("upsert_tickets", data, lambda name: agents_mod.match_agent(name, agents))
        if changed:
            self.hub.publish("tickets", {"ids": changed})

    async def fetch_inbox(self):
        data = await self.n8n.inbox()
        if not isinstance(data, list):
            raise N8nError("bad_json", "bridge/inbox is not a list")
        new = await self.db.a("upsert_inbox", data)
        if new:
            self.hub.publish("inbox", {"count": len(new), "ids": new})

    async def fetch_library(self):
        data = await self.n8n.library()
        if not isinstance(data, dict):
            raise N8nError("bad_json", "bridge/library is not an object")
        changed, newly = await self.db.a("save_library", data)
        if changed or newly:
            self.hub.publish("library", {"date": data.get("date"), "applied": newly})
        await self._apply_progress(data)

    async def _apply_progress(self, report):
        job = self.apply_job
        if job is None or not job["detail"].get("ids"):
            return
        applied = {f.get("id") for f in report.get("findings") or [] if isinstance(f, dict) and f.get("applied")}
        ids = job["detail"]["ids"]
        done = sum(1 for i in ids if i in applied)
        current = next((i for i in ids if i not in applied), None)
        if (done, current) != (job["detail"]["done"], job["detail"]["current"]):
            job["detail"]["done"], job["detail"]["current"] = done, current
            self.hub.publish("job", job_event(job))

    def kick(self, *names):
        for n in names:
            self.pollers[n].kick()

    # ---- chat -----------------------------------------------------------------------------------------------------
    def enqueue(self, thread_id):
        w = self._chat_workers.get(thread_id)
        if w is None or w.done():
            self._chat_workers[thread_id] = asyncio.ensure_future(self._chat_worker(thread_id))

    async def _chat_worker(self, thread_id):
        try:
            while True:
                msg = await self.db.a("next_queued", thread_id)
                if msg is None:
                    return
                await self._send_one(thread_id, msg)
        finally:
            if self._chat_workers.get(thread_id) is asyncio.current_task():
                self._chat_workers.pop(thread_id, None)

    def _emit_message(self, row):
        self.hub.publish("message", self.db.message_dict(row))

    async def _send_one(self, thread_id, msg):
        mid = msg["id"]
        now = self.clock.now_ms()
        if now - (msg["queued_at"] or msg["created_at"]) > QUEUE_DROP_MS:
            self._emit_message(await self.db.a("set_message_state", mid, "dropped",
                                               "Not sent: waited more than 10 min behind another message"))
            return
        thread = await self.db.a("get_thread", thread_id)
        if thread is None:
            return
        started = self.clock.now_ms()
        self._emit_message(await self.db.a("set_message_state", mid, "sending"))
        if thread["kind"] == "n8n":
            await self._send_n8n(thread, msg, started)
        else:
            await self._send_claude(thread, msg, started)

    async def _send_n8n(self, thread, msg, started):
        mid = msg["id"]
        query = msg["text"]
        replay = await self.db.a("get_setting", "replay_after_hours")
        if isinstance(replay, (int, float)) and replay > 0:
            prev = await self.db.a("previous_message_at", thread["id"], mid)
            if prev and started - prev > replay * 3_600_000:
                turns = await self.db.a("last_turns", thread["id"])
                lines = [f"{'Me' if r['role'] == 'user' else 'You'}: {r['text']}" for r in turns if r["text"] != query]
                summary = "\n".join(lines)[-REPLAY_CLIP:]
                if summary:
                    query = f"(Earlier in this chat:\n{summary})\n\n{query}"
        body = {"query": query, "conversation_id": thread["n8n_conversation_id"],
                "user_name": msg["author_name"] or "Alex"}
        if thread["target"] != "crew":
            body["agent_id"] = thread["target"]
        try:
            data = await self.n8n.chat(body)
        except N8nError as err:
            state = "timeout" if err.code == "timeout" else "failed"
            text = "No reply after 2 min" if state == "timeout" else err.message
            self._emit_message(await self.db.a("set_message_state", mid, state, text, started_at=started))
            return
        except Exception as exc:  # noqa: BLE001
            LOG.exception("chat send failed")
            self._emit_message(await self.db.a("set_message_state", mid, "failed", type(exc).__name__,
                                               started_at=started))
            return
        output = data.get("output") if isinstance(data, dict) else None
        if not isinstance(output, str):
            output = "" if output is None else str(output)
        self._emit_message(await self.db.a("set_message_state", mid, "replied", started_at=started))
        reply = await self.db.a("add_reply", thread["id"], "agent", "n8n", output or "(empty reply)",
                                thread["target_name"])
        self._emit_message(reply)
        self.hub.publish("thread", self.db.thread_dict(await self.db.a("get_thread", thread["id"])))
        self.kick("status")

    async def _send_claude(self, thread, msg, started):
        mid = msg["id"]
        machine, _, session = thread["target"].partition("/")
        try:
            res = await self.crew.command(machine, "send", {"session": session, "text": msg["text"],
                                                            "urgent": bool(msg["urgent"])})
        except CrewError as err:
            self._emit_message(await self.db.a("set_message_state", mid, "failed", err.code, started_at=started))
            return
        if res.get("ok"):
            row = await self.db.a("set_message_state", mid, "delivered", started_at=started)
        else:
            row = await self.db.a("set_message_state", mid, "failed", str(res.get("error") or "failed"),
                                  started_at=started)
        self._emit_message(row)

    # ---- Claude sessions ---------------------------------------------------------------------------------------------
    # A Bridge thread for a Claude session is a pointer (machine/session): the conversation is read from the machine
    # when the thread is open (conversation.py) and Claude's text is never written to the DB. Only what the user sends
    # is stored. (Until 0.2.0 a session going idle added its summary to the thread as a "status" row.)
    def on_crew_full(self, machines):
        pass

    def on_crew_machine(self, machine_id, data):
        pass

    # ---- library apply --------------------------------------------------------------------------------------------
    def new_job(self, kind, user, detail=None):
        job = {"id": "job_" + uuid.uuid4().hex[:16], "kind": kind, "state": "running", "detail": detail or {},
               "startedAt": self.clock.now_ms(), "finishedAt": None, "by": user.display}
        self.jobs[job["id"]] = job
        if len(self.jobs) > 50:
            for old in list(self.jobs)[:-50]:
                if self.jobs[old]["state"] != "running":
                    del self.jobs[old]
        self.hub.publish("job", job_event(job))
        return job

    def finish_job(self, job, state, detail=None):
        job["state"] = state
        job["finishedAt"] = self.clock.now_ms()
        if detail:
            job["detail"] = {**job["detail"], **detail}
        self.hub.publish("job", job_event(job))

    def start_apply(self, ids, force, user, action_id, targets=None):
        """detail {done, total, ids, current, force}: `ids` are the findings this job applies ("all" = the ones not
        applied at the start), `done` counts those seen applied, `current` is the next one still open."""
        targets = list(targets if targets is not None else ([] if ids == "all" else ids))
        job = self.new_job("library.apply", user, {"ids": targets, "total": len(targets), "done": 0,
                                                   "current": targets[0] if targets else None, "force": force,
                                                   "all": ids == "all"})
        self.apply_job = job
        self.kick("library")
        self.spawn(self._apply(job, ids, force, user, action_id))
        return job

    async def _apply(self, job, ids, force, user, action_id):
        body = {"ids": ids, "by": user.display}
        if force:
            body["force"] = True
            try:
                pre = await self.n8n.precheck()
                if isinstance(pre, dict):
                    job["detail"]["precheck"] = pre
            except N8nError as err:
                job["detail"]["precheck"] = {"error": err.message}
        try:
            res = await self.n8n.apply(body)
        except N8nError as err:
            state = "timeout" if err.code == "timeout" else "error"
            self.finish_job(job, state, {"error": err.message, "code": err.code})
            await self.db.a("finish_action", action_id, state, err.message)
        else:
            if isinstance(res, dict) and res.get("ok") is False:
                error = str(res.get("error") or "refused")[:500]
                quiet = "quiet" in error.lower()
                self.finish_job(job, "refused", {"error": error, "quietHours": quiet, "canForce": quiet and not force})
                await self.db.a("finish_action", action_id, "refused", error)
            else:
                result = res if isinstance(res, dict) else {"result": res}
                clipped = {k: v for k, v in result.items() if isinstance(v, (int, float, bool, str))}
                self.finish_job(job, "done", {"result": clipped})
                await self.db.a("finish_action", action_id, "ok")
        self.kick("library", "status")

    # ---- "Try again" (re-run a workflow) and library scan ------------------------------------------------------------
    def start_rerun(self, agent, user, action_id, hint):
        wf = agents_mod.bare_workflow_id(agent.get("workflowId") or agent.get("id"))
        job = self.new_job("agent.rerun", user, {"agentId": agent["id"], "name": agent.get("name"), "workflowId": wf,
                                                 "hint": hint, "step": "starting", "run": None})
        self.spawn(self._rerun(job, wf, user, action_id))
        return job

    def _progress(self, job, **detail):
        job["detail"].update(detail)
        self.hub.publish("job", job_event(job))

    async def _rerun(self, job, wf, user, action_id):
        try:
            before = await self.n8n.runs(wf)
            known = {str(r.get("id")) for r in before or [] if isinstance(r, dict)}
        except N8nError:
            known = None  # can't tell old runs from new: take the newest "integrated" one after the start
        try:
            res = await self.n8n.run_now({"workflowId": wf, "by": user.display})
        except N8nError as err:
            code = "not_rerunnable" if err.status == 500 else err.code
            msg = "n8n can't re-run this workflow" if err.status == 500 else err.message
            self.finish_job(job, "error", {"error": msg, "code": code})
            await self.db.a("finish_action", action_id, "refused" if code == "not_rerunnable" else "error", msg)
            return
        if isinstance(res, dict) and res.get("ok") is False:
            msg = str(res.get("error") or "refused")[:300]
            self.finish_job(job, "refused", {"error": msg})
            await self.db.a("finish_action", action_id, "refused", msg)
            return
        res = res if isinstance(res, dict) else {}
        self._progress(job, step="started", startedAt=res.get("startedAt"), note=str(res.get("note") or "")[:300])
        await self.db.a("finish_action", action_id, "ok")
        deadline = self.clock.time() + RERUN_WATCH
        run = None
        while self.clock.time() < deadline:
            await self.clock.sleep(RERUN_POLL)
            try:
                runs = await self.n8n.runs(wf)
            except N8nError:
                continue
            runs = [r for r in runs or [] if isinstance(r, dict)]
            await self.db.a("cache_put", f"runs:{wf}", runs)
            fresh = [r for r in runs if r.get("mode") == "integrated" and (known is None or str(r.get("id")) not in known)]
            if not fresh:
                continue
            new = fresh[0]
            view = {k: new.get(k) for k in ("id", "status", "mode", "startedAt", "stoppedAt")}
            if view != run:
                run = view
                self._progress(job, step="running", run=run)
            if new.get("status") not in ("running", "waiting", "new", None):
                ok = new.get("status") == "success"
                self.finish_job(job, "done" if ok else "error", {"step": "finished",
                                                                 **({} if ok else {"error": "The run failed"})})
                self.kick("status")
                return
        self.finish_job(job, "timeout", {"step": "unseen" if run is None else "running",
                                         "error": "No finished run seen yet; check Runs later"})
        self.kick("status")

    def start_scan(self, hours, user, action_id, agent_id=None):
        job = self.new_job("library.scan", user, {"hours": hours, "agentId": agent_id, "hint": agents_mod.SCAN_HINT})
        self.spawn(self._scan(job, hours, user, action_id))
        return job

    async def _scan(self, job, hours, user, action_id):
        try:
            res = await self.n8n.library_scan({"hours": hours, "by": user.display})
        except N8nError as err:
            if err.status == 409:
                self.finish_job(job, "refused", {"error": "A library scan is already running", "code": "scan_running"})
                await self.db.a("finish_action", action_id, "refused", "scan_running")
            else:
                self.finish_job(job, "error", {"error": err.message, "code": err.code})
                await self.db.a("finish_action", action_id, "error", err.message)
            return
        res = res if isinstance(res, dict) else {}
        self.finish_job(job, "done", {k: res.get(k) for k in ("started", "state", "task")
                                      if isinstance(res.get(k), (str, int, float, bool, type(None)))})
        await self.db.a("finish_action", action_id, "ok")
        self.kick("status", "library")

    # ---- retention ------------------------------------------------------------------------------------------------
    def seconds_to_next_prune(self):
        now = datetime.datetime.fromtimestamp(self.clock.time())
        target = now.replace(hour=PRUNE_AT[0], minute=PRUNE_AT[1], second=0, microsecond=0)
        if target <= now:
            target += datetime.timedelta(days=1)
        return (target - now).total_seconds()

    async def prune_now(self):
        counts = await self.db.a("prune")
        if any(counts.values()):
            LOG.info("prune: %s", counts)
        return counts

    async def _prune_loop(self):
        while True:
            await self.clock.sleep(self.seconds_to_next_prune())
            try:
                await self.prune_now()
            except Exception:  # noqa: BLE001
                LOG.exception("prune failed")
            await self.clock.sleep(60)


def job_event(job):
    return {"id": job["id"], "kind": job["kind"], "state": job["state"], "detail": job["detail"],
            "startedAt": job["startedAt"], "finishedAt": job["finishedAt"]}
