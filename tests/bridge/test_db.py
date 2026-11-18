"""SQLite store: migrations, WAL, 0600, clientId dedupe, restart recovery, retention, backup snapshot and restore."""

import os
import sqlite3
import stat
import subprocess
import sys
import threading

from bridge.app import backup
from bridge.app.clock import Clock
from bridge.app.db import DAY_MS, DB, MIGRATIONS
from bridge.app.server import User
from conftest import ROOT

U = User("u1", "alex", "Alex", ["system-admin"])


def open_db(tmp_path, clock=None):
    return DB(tmp_path / "bridge.db", clock or Clock()).open()


def test_migrations_wal_and_mode(tmp_path):
    db = open_db(tmp_path)
    assert db.q1("PRAGMA user_version")[0] == len(MIGRATIONS)
    assert db.q1("PRAGMA journal_mode")[0] == "wal"
    assert db.q1("PRAGMA foreign_keys")[0] == 1
    db.open_thread("n8n", "crew", "Crew", U)
    for f in tmp_path.glob("bridge.db*"):
        assert stat.S_IMODE(os.stat(f).st_mode) == 0o600, f
    db.close()
    db = open_db(tmp_path)  # reopening does not re-run migrations
    assert db.q1("SELECT COUNT(*) FROM threads")[0] == 1
    db.close()


def test_client_id_dedupe(tmp_path):
    db = open_db(tmp_path)
    t, created = db.open_thread("n8n", "crew", "Crew", U)
    assert created
    t2, created2 = db.open_thread("n8n", "crew", "Crew", U)
    assert not created2 and t2["id"] == t["id"]
    m1, c1 = db.add_user_message(t["id"], U, "hi", "client-0001")
    m2, c2 = db.add_user_message(t["id"], U, "hi again", "client-0001")
    assert c1 and not c2 and m1["id"] == m2["id"] and m2["text"] == "hi"
    a1, _ = db.begin_action(U, "chat.send", "crew", "x", "client-0002")
    a2, existing = db.begin_action(U, "chat.send", "crew", "x", "client-0002")
    assert a1 and a2 is None and existing["id"] == a1
    db.close()


def test_sending_becomes_unknown_on_start(tmp_path):
    db = open_db(tmp_path)
    t, _ = db.open_thread("n8n", "crew", "Crew", U)
    m, _ = db.add_user_message(t["id"], U, "hi", "client-0003")
    db.set_message_state(m["id"], "sending")
    assert db.recover_on_start() == [m["id"]]
    row = db.get_message(m["id"])
    assert row["state"] == "unknown" and "restarted" in row["error"]
    db.close()


def test_retention_prune(tmp_path):
    clock = Clock()
    db = open_db(tmp_path, clock)
    old, _ = db.open_thread("n8n", "crew", "Crew", U)
    m, _ = db.add_user_message(old["id"], U, "old", "client-0010")
    db.set_message_state(m["id"], "replied")
    db.add_reply(old["id"], "agent", "n8n", "old reply")
    keep, _ = db.open_thread("claude", "nas/s1", "Example session", U)
    m, _ = db.add_user_message(keep["id"], U, "old but", "client-0011")
    db.set_message_state(m["id"], "delivered")
    db.begin_action(U, "chat.send", "crew", "x", "client-0012")
    db.upsert_inbox([{"id": 1, "title": "t", "message": "m", "createdAt": "2026-01-01T00:00:00Z"}])
    clock.advance(60 * 86400)
    db.add_user_message(keep["id"], U, "recent", "client-0013")
    clock.advance(31 * 86400)  # first messages now 91 days old, the recent one 31
    counts = db.prune()
    assert counts == {"messages": 3, "threads": 1, "actions": 0}
    assert db.get_thread(old["id"]) is None
    assert [m["text"] for m in db.list_messages(keep["id"])] == ["recent"]
    assert len(db.list_inbox()) == 1  # inbox is never pruned
    clock.advance(300 * 86400)
    assert db.prune()["actions"] == 1  # audit kept a year
    db.close()


def test_prune_spares_in_flight(tmp_path):
    clock = Clock()
    db = open_db(tmp_path, clock)
    t, _ = db.open_thread("n8n", "crew", "Crew", U)
    m, _ = db.add_user_message(t["id"], U, "queued", "client-0020")
    clock.advance(100 * DAY_MS / 1000)
    assert db.prune()["messages"] == 0
    assert db.get_message(m["id"]) is not None
    db.close()


def test_backup_snapshot_during_writes_and_restore(tmp_path):
    db = open_db(tmp_path)
    t, _ = db.open_thread("n8n", "crew", "Crew", U)
    stop = threading.Event()

    def writer():
        conn = sqlite3.connect(str(tmp_path / "bridge.db"), timeout=5)
        i = 0
        while not stop.is_set():
            conn.execute("INSERT INTO messages (thread_id, role, source, text, created_at) VALUES (?,?,?,?,?)",
                         (t["id"], "agent", "n8n", "x" * 500, i))
            conn.commit()
            i += 1
        conn.close()

    th = threading.Thread(target=writer)
    th.start()
    try:
        for _ in range(3):
            assert backup.snapshot(tmp_path) == 0
    finally:
        stop.set()
        th.join()
    snap = tmp_path / "snapshot" / "bridge.db"
    assert stat.S_IMODE(os.stat(snap).st_mode) == 0o600
    conn = sqlite3.connect(str(snap))
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert conn.execute("SELECT COUNT(*) FROM threads").fetchone()[0] == 1
    conn.close()
    db.close()
    # a restore wipes /data and copies the backup (snapshot only) back: run.sh restores it
    for f in tmp_path.glob("bridge.db*"):
        f.unlink()
    out = subprocess.run([sys.executable, str(ROOT / "bridge" / "app" / "backup.py"), "--restore"],
                         env={**os.environ, "BRIDGE_DATA": str(tmp_path)}, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    db = open_db(tmp_path)
    assert db.get_thread(t["id"]) is not None
    db.close()
    # restore is a no-op when the DB exists
    assert backup.restore(tmp_path) == 0


def test_backup_without_db(tmp_path):
    assert backup.snapshot(tmp_path) == 0
    assert not (tmp_path / "snapshot" / "bridge.db").exists()


def test_comments_parsing_and_ticket_upsert(tmp_path):
    db = open_db(tmp_path)
    rows = [{"id": 1, "title": "a", "comments": None},
            {"id": 2, "title": "b", "comments": "not json ["},
            {"id": 3, "title": "c", "comments": '[{"at":"2026-11-18T16:15:15.356Z","author":"NAS agent","text":"hi"}]'},
            {"id": 4, "title": "d", "comments": '{"weird": 1}'},
            {"title": "no id"}]
    assert sorted(db.upsert_tickets(rows)) == [1, 2, 3, 4]
    assert db.upsert_tickets(rows) == []  # unchanged rows are not reported again
    t = {x["id"]: x for x in db.list_tickets()}
    assert t[1]["comments"] == [] and t[1]["commentsRaw"] is None
    assert t[2]["comments"] == [] and t[2]["commentsRaw"] == "not json ["
    assert t[3]["comments"][0]["author"] == "NAS agent"
    assert t[4]["commentsRaw"]
    assert db.tickets_with_new_agent_comment("u1") == [3]
    db.mark_read("u1", "tickets", 2 ** 50)
    assert db.tickets_with_new_agent_comment("u1") == []
    db.close()


def test_ticket_status_groups(tmp_path):
    from bridge.app.db import TICKET_STATUSES, ticket_group

    assert TICKET_STATUSES == ("open", "in_progress", "needs_isaac", "awaiting_approval", "done", "closed")
    assert {s: ticket_group(s) for s in TICKET_STATUSES} == {
        "open": "open", "in_progress": "progress", "needs_isaac": "needs", "awaiting_approval": "needs",
        "done": "done", "closed": "done"}
    assert ticket_group(None) == "open" and ticket_group("resolved") == "done" and ticket_group("odd") == "open"
    db = open_db(tmp_path)
    rows = [{"id": 1, "title": "a", "status": "open", "updatedAt": "2026-11-18T10:00:00.000Z"},
            {"id": 2, "title": "b", "status": "awaiting_approval", "updatedAt": "2026-11-18T09:00:00.000Z",
             "comments": '[{"at":"2026-11-18T09:00:00.000Z","author":"Engineer","text":"ready"},'
                         '{"at":"2026-11-18T09:01:00.000Z","author":"Alex","text":"looking"}]'},
            {"id": 3, "title": "c", "status": "needs_isaac", "updatedAt": "2026-11-18T08:00:00.000Z"}]
    db.upsert_tickets(rows)
    need = db.tickets_needing_you()
    assert [t["id"] for t in need] == [3, 2]
    assert need[0]["last"] is None and need[1]["last"]["text"] == "ready"  # the last agent comment, not the human's
    assert db.tickets_with_new_agent_comment("u1") == [2]
    db.close()


async def test_nightly_prune_schedule(bridge):
    s = bridge.jobs.seconds_to_next_prune()
    assert 0 < s <= 86400
    bridge.clock.advance(s + 1)
    assert bridge.jobs.seconds_to_next_prune() > 86000  # next night
    assert await bridge.jobs.prune_now() == {"messages": 0, "threads": 0, "actions": 0}
