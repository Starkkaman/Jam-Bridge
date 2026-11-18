#!/usr/bin/env python3
"""backup_pre hook (config.yaml): copy /data/bridge.db to /data/snapshot/bridge.db with sqlite3's backup API.

The live DB files are excluded from the backup (backup_exclude); the snapshot is what HA backs up. Standalone on
purpose (stdlib only, no package imports): the Supervisor runs `python3 /app/backup.py` inside the container.
Exit 0 when there is no DB yet; non-zero fails this app's backup. `--restore` (run.sh) copies the snapshot back
when bridge.db is missing.
"""

import os
import pathlib
import sqlite3
import sys


def snapshot(data_dir):
    data_dir = pathlib.Path(data_dir)
    src = data_dir / "bridge.db"
    if not src.exists():
        print("backup: no database yet; nothing to snapshot")
        return 0
    dest_dir = data_dir / "snapshot"
    dest_dir.mkdir(mode=0o700, exist_ok=True)
    dest = dest_dir / "bridge.db"
    tmp = dest_dir / "bridge.db.tmp"
    fd = os.open(tmp, os.O_RDWR | os.O_CREAT | os.O_TRUNC, 0o600)
    os.close(fd)
    conn = sqlite3.connect(str(src), timeout=10)
    try:
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
        out = sqlite3.connect(str(tmp))
        try:
            conn.backup(out)
            ok = out.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            out.close()
    finally:
        conn.close()
    if ok != "ok":
        print("backup: snapshot failed its integrity check", file=sys.stderr)
        tmp.unlink(missing_ok=True)
        return 1
    os.chmod(tmp, 0o600)
    os.replace(tmp, dest)
    print("backup: snapshot written")
    return 0


def restore(data_dir):
    """run.sh at start: after a restore /data holds only the snapshot (the live files are excluded from backups),
    so copy it back. A no-op when bridge.db exists or there is no snapshot."""
    data_dir = pathlib.Path(data_dir)
    db = data_dir / "bridge.db"
    snap = data_dir / "snapshot" / "bridge.db"
    if db.exists() or not snap.exists():
        return 0
    for suffix in ("-wal", "-shm"):  # stale WAL files must not be applied to the restored copy
        (data_dir / f"bridge.db{suffix}").unlink(missing_ok=True)
    tmp = data_dir / "bridge.db.restore"
    fd = os.open(tmp, os.O_RDWR | os.O_CREAT | os.O_TRUNC, 0o600)
    os.close(fd)
    src = sqlite3.connect(str(snap))
    try:
        out = sqlite3.connect(str(tmp))
        try:
            src.backup(out)
        finally:
            out.close()
    finally:
        src.close()
    os.chmod(tmp, 0o600)
    os.replace(tmp, db)
    print("backup: database restored from the snapshot")
    return 0


if __name__ == "__main__":
    data = os.environ.get("BRIDGE_DATA", "/data")
    sys.exit(restore(data) if "--restore" in sys.argv[1:] else snapshot(data))
