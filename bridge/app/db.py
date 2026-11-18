"""SQLite store at /data/bridge.db (mode 0600, WAL). One writer thread: every call runs on a single-thread executor
(`await db.a("method", ...)`); the sync methods are also used directly by backup and tests.

No tokens or secrets in rows. Times are epoch ms UTC. Chat messages are pruned after 90 days (the owner's decision 4),
the audit log after a year; inbox rows are never pruned.
"""

import asyncio
import contextlib
import functools
import json
import os
import pathlib
import sqlite3
import uuid
from concurrent.futures import ThreadPoolExecutor

DAY_MS = 86_400_000
MESSAGE_RETENTION_MS = 90 * DAY_MS
ACTION_RETENTION_MS = 365 * DAY_MS
QUEUE_DROP_MS = 10 * 60 * 1000
USER_TEXT_MAX = 4000
AGENT_TEXT_MAX = 64 * 1024

MESSAGE_STATES = ("queued", "sending", "replied", "delivered", "failed", "timeout", "unknown", "dropped")
RETRYABLE = ("failed", "timeout", "unknown", "dropped")
THEMES = ("follow_ha", "night", "day")

MIGRATIONS = [
    """
    CREATE TABLE threads (
        id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK (kind IN ('n8n','claude')), target TEXT NOT NULL,
        title TEXT, target_name TEXT, created_by_id TEXT, created_by_name TEXT, created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL, last_message_at INTEGER, archived INTEGER NOT NULL DEFAULT 0,
        n8n_conversation_id TEXT
    );
    CREATE INDEX threads_target ON threads (kind, target, archived);
    CREATE TABLE messages (
        id INTEGER PRIMARY KEY, thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
        role TEXT NOT NULL CHECK (role IN ('user','agent','status')),
        source TEXT NOT NULL CHECK (source IN ('bridge','n8n','crew_hub','snapshot')),
        author_id TEXT, author_name TEXT, text TEXT NOT NULL, state TEXT, error TEXT, client_id TEXT UNIQUE,
        urgent INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL, queued_at INTEGER, finished_at INTEGER,
        duration_ms INTEGER
    );
    CREATE INDEX messages_thread ON messages (thread_id, id);
    CREATE INDEX messages_state ON messages (state);
    CREATE TABLE read_state (
        user_id TEXT NOT NULL, scope TEXT NOT NULL, last_read_at INTEGER NOT NULL, PRIMARY KEY (user_id, scope)
    );
    CREATE TABLE tickets (
        id INTEGER PRIMARY KEY, type TEXT, agent TEXT, agent_id TEXT, agent_match TEXT, title TEXT, details TEXT,
        status TEXT, source TEXT, resolution TEXT, comments_json TEXT, created_at INTEGER, updated_at INTEGER,
        fetched_at INTEGER
    );
    CREATE TABLE inbox (
        id INTEGER PRIMARY KEY, source TEXT, title TEXT, message TEXT, created_at INTEGER, first_seen_at INTEGER
    );
    CREATE TABLE library (date TEXT PRIMARY KEY, finished TEXT, notes_json TEXT, errors_json TEXT, fetched_at INTEGER);
    CREATE TABLE findings (
        id TEXT PRIMARY KEY, date TEXT REFERENCES library(date) ON DELETE CASCADE, kind TEXT, severity TEXT,
        title TEXT, detail TEXT, fix TEXT, applied INTEGER NOT NULL DEFAULT 0, applied_seen_at INTEGER, ord INTEGER
    );
    CREATE TABLE actions (
        id INTEGER PRIMARY KEY, at INTEGER NOT NULL, user_id TEXT, user_name TEXT, kind TEXT NOT NULL, target TEXT,
        summary TEXT, client_id TEXT UNIQUE, outcome TEXT, error TEXT
    );
    CREATE INDEX actions_at ON actions (at);
    CREATE TABLE cache (key TEXT PRIMARY KEY, json TEXT, fetched_at INTEGER, ok INTEGER, error TEXT);
    CREATE TABLE settings (key TEXT PRIMARY KEY, value_json TEXT);
    """,
]


def iso_to_ms(value):
    """'2026-11-18T16:00:45.048Z' or '2026-11-18' -> epoch ms, else None."""
    if not isinstance(value, str) or not value:
        return None
    from datetime import datetime, timezone

    v = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(v)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _clip(value, n):
    if value is None:
        return None
    s = value if isinstance(value, str) else str(value)
    return s[:n]


def auto_title(text):
    line = next((ln.strip() for ln in (text or "").splitlines() if ln.strip()), "")
    return (line[:59].rstrip() + "…") if len(line) > 60 else (line or None)


def parse_comments(raw):
    """n8n's `comments` is a JSON string (or null). Returns (list, raw_text_if_invalid)."""
    if raw is None or raw == "":
        return [], None
    data = raw
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except ValueError:
            return [], raw[:4000]
    if not isinstance(data, list):
        return [], (raw if isinstance(raw, str) else json.dumps(raw))[:4000]
    out = []
    for c in data:
        if isinstance(c, dict):
            out.append({"at": _clip(c.get("at"), 40), "author": _clip(c.get("author"), 80) or "",
                        "text": _clip(c.get("text"), 4000) or ""})
        elif isinstance(c, str):
            out.append({"at": None, "author": "", "text": c[:4000]})
    return out, None


# Ticket statuses n8n accepts (bridge/ticket-update). The Engineer (an OpenCode agent on the ticket inbox) sets
# needs_isaac when it wants direction and awaiting_approval when a sandboxed fix is ready for review.
TICKET_STATUSES = ("open", "in_progress", "needs_isaac", "awaiting_approval", "done", "closed")
TICKET_NEEDS_YOU = ("needs_isaac", "awaiting_approval")
AGENT_AUTHORS = frozenset({"NAS agent", "Engineer"})  # any other comment author is the human (HA user name)


def ticket_group(status):
    """n8n status -> the page's filter group: open | needs | progress | done."""
    s = str(status or "").strip().lower()
    if s in TICKET_NEEDS_YOU:
        return "needs"
    if s in ("done", "closed", "resolved"):
        return "done"
    if s in ("in_progress", "in progress", "progress"):
        return "progress"
    return "open"


class DB:
    def __init__(self, path, clock):
        self.path = pathlib.Path(path)
        self.clock = clock
        self.conn = None
        self._exec = ThreadPoolExecutor(max_workers=1, thread_name_prefix="bridge-db")

    # ---- plumbing -------------------------------------------------------------------------------------------------
    async def a(self, name, *args, **kwargs):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._exec, functools.partial(getattr(self, name), *args, **kwargs))

    def open(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        os.close(fd)
        os.chmod(self.path, 0o600)
        old_umask = os.umask(0o077)  # -wal and -shm files are created 0600 too
        try:
            self.conn = sqlite3.connect(str(self.path), isolation_level=None, check_same_thread=False)
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=NORMAL")
            self.conn.execute("PRAGMA foreign_keys=ON")
            self.conn.execute("PRAGMA busy_timeout=5000")
            self._migrate()
        finally:
            os.umask(old_umask)
        return self

    def close(self):
        if self.conn is not None:
            self.conn.close()
            self.conn = None
        self._exec.shutdown(wait=True)

    def snapshot(self, dest):
        """Consistent copy with sqlite3's backup API (used on a clean stop; backup.py does the same)."""
        dest = pathlib.Path(dest)
        dest.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        tmp = dest.with_suffix(".tmp")
        fd = os.open(tmp, os.O_RDWR | os.O_CREAT | os.O_TRUNC, 0o600)
        os.close(fd)
        out = sqlite3.connect(str(tmp))
        try:
            self.conn.backup(out)
        finally:
            out.close()
        os.chmod(tmp, 0o600)
        os.replace(tmp, dest)

    def _migrate(self):
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        for i, script in enumerate(MIGRATIONS[version:], start=version + 1):
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                for stmt in script.split(";"):
                    if stmt.strip():
                        self.conn.execute(stmt)
                self.conn.execute(f"PRAGMA user_version={i}")
                self.conn.execute("COMMIT")
            except Exception:
                self.conn.execute("ROLLBACK")
                raise

    @contextlib.contextmanager
    def tx(self):
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield self.conn
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        self.conn.execute("COMMIT")

    def q(self, sql, args=()):
        return self.conn.execute(sql, args).fetchall()

    def q1(self, sql, args=()):
        return self.conn.execute(sql, args).fetchone()

    def now(self):
        return self.clock.now_ms()

    # ---- threads --------------------------------------------------------------------------------------------------
    @staticmethod
    def thread_dict(r, unread=None, last=None):
        d = {"id": r["id"], "kind": r["kind"], "target": r["target"], "title": r["title"],
             "targetName": r["target_name"], "createdById": r["created_by_id"], "createdByName": r["created_by_name"],
             "createdAt": r["created_at"], "updatedAt": r["updated_at"], "lastMessageAt": r["last_message_at"],
             "archived": bool(r["archived"])}
        if unread is not None:
            d["unread"] = unread
        if last is not None:
            d["last"] = last
        return d

    def get_thread(self, thread_id):
        return self.q1("SELECT * FROM threads WHERE id=?", (thread_id,))

    def open_thread(self, kind, target, target_name, user, new=False):
        """Returns (row, created). Reuses the most recent open (not archived) thread of that target unless `new`."""
        with self.tx():
            row = None if new else self.q1(
                "SELECT * FROM threads WHERE kind=? AND target=? AND archived=0"
                " ORDER BY COALESCE(last_message_at, created_at) DESC LIMIT 1", (kind, target))
            if row is not None:
                if target_name and target_name != row["target_name"]:
                    self.conn.execute("UPDATE threads SET target_name=? WHERE id=?", (target_name, row["id"]))
                    row = self.get_thread(row["id"])
                return row, False
            now = self.now()
            tid = "th_" + uuid.uuid4().hex
            self.conn.execute(
                "INSERT INTO threads (id, kind, target, title, target_name, created_by_id, created_by_name, created_at,"
                " updated_at, n8n_conversation_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (tid, kind, target, None, target_name, user.id, user.display, now, now, tid))
            return self.get_thread(tid), True

    def update_thread(self, thread_id, title=None, archived=None, rotate=False):
        with self.tx():
            row = self.get_thread(thread_id)
            if row is None:
                return None
            now = self.now()
            if title is not None:
                self.conn.execute("UPDATE threads SET title=?, updated_at=? WHERE id=?", (title or None, now, thread_id))
            if archived is not None:
                self.conn.execute("UPDATE threads SET archived=?, updated_at=? WHERE id=?",
                                  (1 if archived else 0, now, thread_id))
            if rotate:
                self.conn.execute("UPDATE threads SET n8n_conversation_id=?, updated_at=? WHERE id=?",
                                  (f"{thread_id}.{now}", now, thread_id))
            return self.get_thread(thread_id)

    def list_threads(self, user_id, archived=False):
        rows = self.q("SELECT * FROM threads WHERE archived=? ORDER BY COALESCE(last_message_at, created_at) DESC",
                      (1 if archived else 0,))
        out = []
        for r in rows:
            last = self.q1("SELECT role, text, state, created_at FROM messages WHERE thread_id=? ORDER BY id DESC LIMIT 1",
                           (r["id"],))
            last_d = None
            if last is not None:
                last_d = {"role": last["role"], "text": last["text"][:160], "state": last["state"],
                          "at": last["created_at"]}
            out.append(self.thread_dict(r, unread=self.thread_unread(user_id, r["id"]), last=last_d))
        return out

    def thread_unread(self, user_id, thread_id):
        since = self.read_at(user_id, f"thread:{thread_id}")
        return self.q1("SELECT COUNT(*) FROM messages WHERE thread_id=? AND role IN ('agent','status') AND created_at>?",
                       (thread_id, since))[0]

    # ---- messages -------------------------------------------------------------------------------------------------
    @staticmethod
    def message_dict(r):
        return {"id": r["id"], "threadId": r["thread_id"], "role": r["role"], "source": r["source"],
                "authorId": r["author_id"], "authorName": r["author_name"], "text": r["text"], "state": r["state"],
                "error": r["error"], "clientId": r["client_id"], "urgent": bool(r["urgent"]),
                "createdAt": r["created_at"], "finishedAt": r["finished_at"], "durationMs": r["duration_ms"]}

    def get_message(self, mid):
        return self.q1("SELECT * FROM messages WHERE id=?", (mid,))

    def message_by_client_id(self, client_id):
        return self.q1("SELECT * FROM messages WHERE client_id=?", (client_id,))

    def add_user_message(self, thread_id, user, text, client_id, urgent=False):
        """Returns (row, created). A repeated clientId returns the existing row."""
        with self.tx():
            old = self.message_by_client_id(client_id)
            if old is not None:
                return old, False
            now = self.now()
            cur = self.conn.execute(
                "INSERT INTO messages (thread_id, role, source, author_id, author_name, text, state, client_id, urgent,"
                " created_at, queued_at) VALUES (?, 'user', 'bridge', ?, ?, ?, 'queued', ?, ?, ?, ?)",
                (thread_id, user.id, user.display, text[:USER_TEXT_MAX], client_id, 1 if urgent else 0, now, now))
            self.conn.execute("UPDATE threads SET last_message_at=?, updated_at=? WHERE id=?", (now, now, thread_id))
            # a new thread is titled from its first message (first line, <= 60 chars); PATCH can rename it later
            self.conn.execute("UPDATE threads SET title=? WHERE id=? AND title IS NULL", (auto_title(text), thread_id))
            return self.get_message(cur.lastrowid), True

    def add_reply(self, thread_id, role, source, text, author_name=None):
        with self.tx():
            now = self.now()
            cur = self.conn.execute(
                "INSERT INTO messages (thread_id, role, source, author_name, text, state, created_at, finished_at)"
                " VALUES (?,?,?,?,?,NULL,?,?)",
                (thread_id, role, source, author_name, (text or "")[:AGENT_TEXT_MAX], now, now))
            self.conn.execute("UPDATE threads SET last_message_at=?, updated_at=? WHERE id=?", (now, now, thread_id))
            return self.get_message(cur.lastrowid)

    def set_message_state(self, mid, state, error=None, started_at=None):
        now = self.now()
        finished = state not in ("queued", "sending")
        duration = (now - started_at) if (finished and started_at) else None
        with self.tx():
            self.conn.execute(
                "UPDATE messages SET state=?, error=?, finished_at=?, duration_ms=COALESCE(?, duration_ms) WHERE id=?",
                (state, _clip(error, 300), now if finished else None, duration, mid))
        return self.get_message(mid)

    def requeue(self, mid):
        now = self.now()
        with self.tx():
            self.conn.execute("UPDATE messages SET state='queued', error=NULL, finished_at=NULL, duration_ms=NULL,"
                              " queued_at=? WHERE id=?", (now, mid))
        return self.get_message(mid)

    def list_messages(self, thread_id, before=None, limit=50):
        if before:
            rows = self.q("SELECT * FROM messages WHERE thread_id=? AND id<? ORDER BY id DESC LIMIT ?",
                          (thread_id, before, limit))
        else:
            rows = self.q("SELECT * FROM messages WHERE thread_id=? ORDER BY id DESC LIMIT ?", (thread_id, limit))
        return [self.message_dict(r) for r in reversed(rows)]

    def next_queued(self, thread_id):
        return self.q1("SELECT * FROM messages WHERE thread_id=? AND state='queued' ORDER BY queued_at, id LIMIT 1",
                       (thread_id,))

    def threads_with_queued(self):
        return [r[0] for r in self.q("SELECT DISTINCT thread_id FROM messages WHERE state='queued'")]

    def recover_on_start(self):
        """A `sending` row found at start becomes `unknown` (never resent automatically)."""
        now = self.now()
        with self.tx():
            ids = [r[0] for r in self.q("SELECT id FROM messages WHERE state='sending'")]
            self.conn.execute("UPDATE messages SET state='unknown', error='The Bridge restarted while waiting',"
                              " finished_at=? WHERE state='sending'", (now,))
        return ids

    def last_turns(self, thread_id, limit=6):
        rows = self.q("SELECT role, author_name, text FROM messages WHERE thread_id=? AND role IN ('user','agent')"
                      " ORDER BY id DESC LIMIT ?", (thread_id, limit))
        return list(reversed(rows))

    def previous_message_at(self, thread_id, before_id):
        r = self.q1("SELECT created_at FROM messages WHERE thread_id=? AND id<? ORDER BY id DESC LIMIT 1",
                    (thread_id, before_id))
        return r[0] if r else None

    # ---- read state -----------------------------------------------------------------------------------------------
    def read_at(self, user_id, scope):
        if not user_id:
            return 2 ** 62  # anonymous viewers have nothing unread
        r = self.q1("SELECT last_read_at FROM read_state WHERE user_id=? AND scope=?", (user_id, scope))
        return r[0] if r else 0

    def mark_read(self, user_id, scope, at):
        with self.tx():
            self.conn.execute(
                "INSERT INTO read_state (user_id, scope, last_read_at) VALUES (?,?,?) ON CONFLICT(user_id, scope)"
                " DO UPDATE SET last_read_at=MAX(last_read_at, excluded.last_read_at)", (user_id, scope, at))

    # ---- tickets --------------------------------------------------------------------------------------------------
    def upsert_tickets(self, rows, matcher=None):
        """rows from bridge/tickets. Returns the ids that are new or changed."""
        now = self.now()
        changed = []
        with self.tx():
            for t in rows:
                if not isinstance(t, dict) or not isinstance(t.get("id"), int):
                    continue
                comments, raw = parse_comments(t.get("comments"))
                comments_json = json.dumps({"list": comments, "raw": raw})
                agent = _clip(t.get("agent"), 200)
                agent_id = _clip(t.get("agent_id"), 120)
                match = agent_id or (matcher(agent) if matcher else None)
                vals = (_clip(t.get("type"), 40), agent, agent_id, match, _clip(t.get("title"), 500),
                        _clip(t.get("details"), 8000), _clip(t.get("status"), 40), _clip(t.get("source"), 200),
                        _clip(t.get("resolution"), 4000), comments_json, iso_to_ms(t.get("createdAt")),
                        iso_to_ms(t.get("updatedAt")))
                old = self.q1("SELECT type, agent, agent_id, agent_match, title, details, status, source, resolution,"
                              " comments_json, created_at, updated_at FROM tickets WHERE id=?", (t["id"],))
                if old is not None and tuple(old) == vals:
                    continue
                self.conn.execute(
                    "INSERT OR REPLACE INTO tickets (id, type, agent, agent_id, agent_match, title, details, status,"
                    " source, resolution, comments_json, created_at, updated_at, fetched_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (t["id"], *vals, now))
                changed.append(t["id"])
        return changed

    @staticmethod
    def ticket_dict(r):
        c = json.loads(r["comments_json"] or '{"list": [], "raw": null}')
        return {"id": r["id"], "type": r["type"], "agent": r["agent"], "agentId": r["agent_id"],
                "agentMatch": r["agent_match"], "title": r["title"], "details": r["details"], "status": r["status"],
                "group": ticket_group(r["status"]),
                "source": r["source"], "resolution": r["resolution"], "comments": c.get("list") or [],
                "commentsRaw": c.get("raw"), "createdAt": r["created_at"], "updatedAt": r["updated_at"]}

    def list_tickets(self, status=None):
        if status:
            rows = self.q("SELECT * FROM tickets WHERE status=? ORDER BY id DESC", (status,))
        else:
            rows = self.q("SELECT * FROM tickets ORDER BY id DESC")
        return [self.ticket_dict(r) for r in rows]

    def tickets_with_new_agent_comment(self, user_id):
        since = self.read_at(user_id, "tickets")
        out = []
        for t in self.list_tickets():
            for c in t["comments"]:
                at = iso_to_ms(c.get("at")) or 0
                if c.get("author") in AGENT_AUTHORS and at > since:
                    out.append(t["id"])
                    break
        return out

    def tickets_needing_you(self):
        """Tickets the Engineer handed back (needs_isaac / awaiting_approval), oldest update first, for Now."""
        out = []
        for t in self.list_tickets():
            if t["group"] != "needs":
                continue
            last = next((c for c in reversed(t["comments"]) if c.get("author") in AGENT_AUTHORS), None)
            out.append({"id": t["id"], "title": t["title"], "status": t["status"], "agent": t["agent"],
                        "agentId": t["agentMatch"], "since": t["updatedAt"] or t["createdAt"],
                        "last": {k: last.get(k) for k in ("at", "author", "text")} if last else None})
        out.sort(key=lambda x: (x["since"] or 0, x["id"]))
        return out

    # ---- inbox ----------------------------------------------------------------------------------------------------
    def upsert_inbox(self, rows):
        now = self.now()
        new = []
        with self.tx():
            for i in rows:
                if not isinstance(i, dict) or not isinstance(i.get("id"), int):
                    continue
                exists = self.q1("SELECT 1 FROM inbox WHERE id=?", (i["id"],))
                vals = (_clip(i.get("source"), 80), _clip(i.get("title"), 500), _clip(i.get("message"), 64 * 1024),
                        iso_to_ms(i.get("createdAt")))
                if exists:
                    self.conn.execute("UPDATE inbox SET source=?, title=?, message=?, created_at=? WHERE id=?",
                                      (*vals, i["id"]))
                else:
                    self.conn.execute("INSERT INTO inbox (id, source, title, message, created_at, first_seen_at)"
                                      " VALUES (?,?,?,?,?,?)", (i["id"], *vals, now))
                    new.append(i["id"])
        return new

    def list_inbox(self, before=None, limit=50):
        if before:
            rows = self.q("SELECT * FROM inbox WHERE id<? ORDER BY id DESC LIMIT ?", (before, limit))
        else:
            rows = self.q("SELECT * FROM inbox ORDER BY id DESC LIMIT ?", (limit,))
        return [{"id": r["id"], "source": r["source"], "title": r["title"], "message": r["message"],
                 "createdAt": r["created_at"], "firstSeenAt": r["first_seen_at"]} for r in rows]

    def inbox_unread(self, user_id):
        return self.q1("SELECT COUNT(*) FROM inbox WHERE first_seen_at>?", (self.read_at(user_id, "inbox"),))[0]

    # ---- library --------------------------------------------------------------------------------------------------
    def save_library(self, report):
        """Returns (changed, newly_applied_ids)."""
        date = _clip(report.get("date"), 20)
        if not date:
            return False, []
        now = self.now()
        newly = []
        changed = False
        with self.tx():
            old = self.q1("SELECT finished FROM library WHERE date=?", (date,))
            finished = _clip(report.get("finished"), 60)
            if old is None or old["finished"] != finished:
                changed = True
            self.conn.execute(
                "INSERT INTO library (date, finished, notes_json, errors_json, fetched_at) VALUES (?,?,?,?,?)"
                " ON CONFLICT(date) DO UPDATE SET finished=excluded.finished, notes_json=excluded.notes_json,"
                " errors_json=excluded.errors_json, fetched_at=excluded.fetched_at",
                (date, finished, json.dumps(report.get("notes") or [])[:200_000],
                 json.dumps(report.get("errors") or [])[:200_000], now))
            for n, f in enumerate(report.get("findings") or []):
                if not isinstance(f, dict) or not f.get("id"):
                    continue
                fid = _clip(f.get("id"), 80)
                applied = 1 if f.get("applied") else 0
                prev = self.q1("SELECT applied FROM findings WHERE id=?", (fid,))
                if prev is None:
                    changed = True
                elif prev["applied"] != applied:
                    changed = True
                    if applied:
                        newly.append(fid)
                self.conn.execute(
                    "INSERT INTO findings (id, date, kind, severity, title, detail, fix, applied, applied_seen_at, ord)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET date=excluded.date, kind=excluded.kind,"
                    " severity=excluded.severity, title=excluded.title, detail=excluded.detail, fix=excluded.fix,"
                    " applied=excluded.applied, ord=excluded.ord,"
                    " applied_seen_at=CASE WHEN excluded.applied=1 AND findings.applied=0 THEN excluded.applied_seen_at"
                    " ELSE findings.applied_seen_at END",
                    (fid, date, _clip(f.get("kind"), 40), _clip(f.get("severity"), 20), _clip(f.get("title"), 500),
                     _clip(f.get("detail"), 4000), _clip(f.get("fix"), 60), applied, now if applied else None, n))
        return changed, newly

    def get_library(self):
        lib = self.q1("SELECT * FROM library ORDER BY date DESC LIMIT 1")
        if lib is None:
            return None
        rows = self.q("SELECT * FROM findings WHERE date=? ORDER BY ord", (lib["date"],))
        findings = [{"id": r["id"], "kind": r["kind"], "severity": r["severity"], "title": r["title"],
                     "detail": r["detail"], "fix": r["fix"], "applied": bool(r["applied"]),
                     "appliedSeenAt": r["applied_seen_at"]} for r in rows]
        return {"date": lib["date"], "finished": lib["finished"], "notes": json.loads(lib["notes_json"] or "[]"),
                "errors": json.loads(lib["errors_json"] or "[]"), "findings": findings, "fetchedAt": lib["fetched_at"]}

    # ---- actions (audit) ------------------------------------------------------------------------------------------
    def begin_action(self, user, kind, target, summary, client_id):
        """Returns (action_id, None) or (None, existing dict) for a repeated clientId."""
        with self.tx():
            if client_id:
                old = self.q1("SELECT * FROM actions WHERE client_id=?", (client_id,))
                if old is not None:
                    return None, dict(old)
            cur = self.conn.execute(
                "INSERT INTO actions (at, user_id, user_name, kind, target, summary, client_id) VALUES (?,?,?,?,?,?,?)",
                (self.now(), user.id, user.display, kind, _clip(target, 200), _clip(summary, 200), client_id))
            return cur.lastrowid, None

    def finish_action(self, action_id, outcome, error=None):
        if action_id is None:
            return
        with self.tx():
            self.conn.execute("UPDATE actions SET outcome=?, error=? WHERE id=?", (outcome, _clip(error, 300), action_id))

    def list_actions(self, limit=100):
        return [dict(r) for r in self.q("SELECT * FROM actions ORDER BY id DESC LIMIT ?", (limit,))]

    # ---- cache ----------------------------------------------------------------------------------------------------
    def cache_put(self, key, obj, ok=True, error=None):
        with self.tx():
            if ok:
                self.conn.execute("INSERT OR REPLACE INTO cache (key, json, fetched_at, ok, error) VALUES (?,?,?,1,NULL)",
                                  (key, json.dumps(obj), self.now()))
            else:  # keep the last good copy, record the error
                self.conn.execute(
                    "INSERT INTO cache (key, json, fetched_at, ok, error) VALUES (?, NULL, NULL, 0, ?)"
                    " ON CONFLICT(key) DO UPDATE SET ok=0, error=excluded.error", (key, _clip(error, 300)))

    def cache_get(self, key):
        r = self.q1("SELECT * FROM cache WHERE key=?", (key,))
        if r is None:
            return None
        return {"data": json.loads(r["json"]) if r["json"] else None, "at": r["fetched_at"], "ok": bool(r["ok"]),
                "error": r["error"]}

    # ---- settings -------------------------------------------------------------------------------------------------
    def get_setting(self, key, default=None):
        r = self.q1("SELECT value_json FROM settings WHERE key=?", (key,))
        return json.loads(r[0]) if r else default

    def put_setting(self, key, value):
        with self.tx():
            self.conn.execute("INSERT OR REPLACE INTO settings (key, value_json) VALUES (?,?)", (key, json.dumps(value)))

    # ---- retention ------------------------------------------------------------------------------------------------
    def prune(self):
        """Messages older than 90 days (not in flight), then threads with no messages left that are also older than
        90 days, then audit rows older than a year. Returns counts."""
        now = self.now()
        cutoff = now - MESSAGE_RETENTION_MS
        with self.tx():
            m = self.conn.execute("DELETE FROM messages WHERE created_at<? AND COALESCE(state,'') NOT IN"
                                  " ('queued','sending')", (cutoff,)).rowcount
            t = self.conn.execute(
                "DELETE FROM threads WHERE COALESCE(last_message_at, created_at)<? AND NOT EXISTS"
                " (SELECT 1 FROM messages WHERE messages.thread_id=threads.id)", (cutoff,)).rowcount
            a = self.conn.execute("DELETE FROM actions WHERE at<?", (now - ACTION_RETENTION_MS,)).rowcount
            self.conn.execute("DELETE FROM cache WHERE (key LIKE 'runs:%' OR key LIKE 'run:%' OR key LIKE 'output:%')"
                              " AND COALESCE(fetched_at,0)<?", (now - DAY_MS,))
        return {"messages": m, "threads": t, "actions": a}
