"""SQLite persistence: people, face samples, security events."""
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta

import numpy as np

SCHEMA = """
CREATE TABLE IF NOT EXISTS persons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'Staff',
    project TEXT DEFAULT '',
    phone TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    valid_until TEXT DEFAULT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS face_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id INTEGER NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
    encoding BLOB NOT NULL,
    image_path TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    person_id INTEGER,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    severity TEXT NOT NULL,
    reason TEXT DEFAULT '',
    site TEXT DEFAULT '',
    confidence REAL,
    snapshot TEXT,
    acknowledged INTEGER NOT NULL DEFAULT 0,
    ack_note TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_samples_person ON face_samples(person_id);
"""


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


class Database:
    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()
        with self.conn() as c:
            c.executescript(SCHEMA)
        self._migrate_legacy()

    @contextmanager
    def conn(self):
        c = sqlite3.connect(self.path, timeout=10)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys = ON")
        try:
            yield c
            c.commit()
        finally:
            c.close()

    def _migrate_legacy(self):
        """Import rows from the original single-table `known_faces` schema once."""
        with self.conn() as c:
            has_legacy = c.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='known_faces'"
            ).fetchone()
            if not has_legacy:
                return
            if c.execute("SELECT COUNT(*) FROM persons").fetchone()[0] > 0:
                return
            by_name = {}
            for row in c.execute("SELECT name, encoding FROM known_faces").fetchall():
                name = (row["name"] or "").strip() or "Unnamed"
                if name not in by_name:
                    cur = c.execute(
                        "INSERT INTO persons (name, category, created_at) VALUES (?, 'Staff', ?)",
                        (name, now_iso()),
                    )
                    by_name[name] = cur.lastrowid
                c.execute(
                    "INSERT INTO face_samples (person_id, encoding, created_at) VALUES (?, ?, ?)",
                    (by_name[name], row["encoding"], now_iso()),
                )

    # ---- persons -------------------------------------------------------
    def add_person(self, name, category, project="", phone="", notes="", valid_until=None):
        with self.conn() as c:
            cur = c.execute(
                "INSERT INTO persons (name, category, project, phone, notes, valid_until, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (name, category, project, phone, notes, valid_until or None, now_iso()),
            )
            return cur.lastrowid

    def update_person(self, person_id, **fields):
        allowed = {"name", "category", "project", "phone", "notes", "valid_until", "active"}
        fields = {k: v for k, v in fields.items() if k in allowed}
        if not fields:
            return
        cols = ", ".join(f"{k} = ?" for k in fields)
        with self.conn() as c:
            c.execute(f"UPDATE persons SET {cols} WHERE id = ?", (*fields.values(), person_id))

    def delete_person(self, person_id):
        with self.conn() as c:
            paths = [r["image_path"] for r in c.execute(
                "SELECT image_path FROM face_samples WHERE person_id = ?", (person_id,))]
            c.execute("DELETE FROM persons WHERE id = ?", (person_id,))
        return [p for p in paths if p]

    def get_person(self, person_id):
        with self.conn() as c:
            row = c.execute("SELECT * FROM persons WHERE id = ?", (person_id,)).fetchone()
            return dict(row) if row else None

    def list_persons(self):
        with self.conn() as c:
            rows = c.execute(
                """SELECT p.*, COUNT(s.id) AS samples,
                          (SELECT MAX(ts) FROM events e WHERE e.person_id = p.id) AS last_seen,
                          (SELECT image_path FROM face_samples s2 WHERE s2.person_id = p.id
                             AND s2.image_path IS NOT NULL ORDER BY s2.id DESC LIMIT 1) AS photo
                   FROM persons p LEFT JOIN face_samples s ON s.person_id = p.id
                   GROUP BY p.id ORDER BY p.name COLLATE NOCASE"""
            ).fetchall()
            return [dict(r) for r in rows]

    def add_sample(self, person_id, encoding, image_path=None):
        with self.conn() as c:
            c.execute(
                "INSERT INTO face_samples (person_id, encoding, image_path, created_at) VALUES (?, ?, ?, ?)",
                (person_id, np.asarray(encoding, dtype=np.float64).tobytes(), image_path, now_iso()),
            )

    def load_gallery(self):
        """Return (encodings[], person_meta[]) for every sample of every active person."""
        with self.conn() as c:
            rows = c.execute(
                """SELECT s.encoding, p.id, p.name, p.category, p.project, p.valid_until
                   FROM face_samples s JOIN persons p ON p.id = s.person_id
                   WHERE p.active = 1"""
            ).fetchall()
        encodings = [np.frombuffer(r["encoding"], dtype=np.float64) for r in rows]
        meta = [{"id": r["id"], "name": r["name"], "category": r["category"],
                 "project": r["project"], "valid_until": r["valid_until"]} for r in rows]
        return encodings, meta

    # ---- events --------------------------------------------------------
    def add_event(self, **e):
        with self.conn() as c:
            cur = c.execute(
                """INSERT INTO events (ts, person_id, name, category, severity, reason, site, confidence, snapshot)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (e.get("ts") or now_iso(), e.get("person_id"), e["name"], e["category"], e["severity"],
                 e.get("reason", ""), e.get("site", ""), e.get("confidence"), e.get("snapshot")),
            )
            return cur.lastrowid

    def list_events(self, date=None, severity=None, unacked_only=False, limit=200):
        q, args = "SELECT * FROM events WHERE 1=1", []
        if date:
            q += " AND substr(ts, 1, 10) = ?"
            args.append(date)
        if severity:
            q += " AND severity = ?"
            args.append(severity)
        if unacked_only:
            q += " AND acknowledged = 0 AND severity != 'Low'"
        q += " ORDER BY ts DESC, id DESC LIMIT ?"
        args.append(int(limit))
        with self.conn() as c:
            return [dict(r) for r in c.execute(q, args).fetchall()]

    def ack_event(self, event_id, note=""):
        with self.conn() as c:
            c.execute("UPDATE events SET acknowledged = 1, ack_note = ? WHERE id = ?", (note, event_id))

    def site_register(self, date):
        """Daily entry register: first-in / last-seen per identified person."""
        with self.conn() as c:
            rows = c.execute(
                """SELECT e.person_id, e.name, e.category, COALESCE(p.project, '') AS project,
                          MIN(e.ts) AS first_in, MAX(e.ts) AS last_seen, COUNT(*) AS sightings
                   FROM events e LEFT JOIN persons p ON p.id = e.person_id
                   WHERE e.person_id IS NOT NULL AND substr(e.ts, 1, 10) = ?
                   GROUP BY e.person_id ORDER BY first_in""",
                (date,),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            t0 = datetime.fromisoformat(d["first_in"])
            t1 = datetime.fromisoformat(d["last_seen"])
            d["hours_on_site"] = round((t1 - t0).total_seconds() / 3600, 2)
            out.append(d)
        return out

    def on_site_now(self, window_min):
        """Emergency muster / roll-call list: identified people seen within the window."""
        since = (datetime.now() - timedelta(minutes=window_min)).isoformat(timespec="seconds")
        with self.conn() as c:
            rows = c.execute(
                """SELECT person_id, name, category, MAX(ts) AS last_seen
                   FROM events WHERE person_id IS NOT NULL AND ts >= ? AND category != 'Blacklisted'
                   GROUP BY person_id ORDER BY last_seen DESC""",
                (since,),
            ).fetchall()
            return [dict(r) for r in rows]

    def stats(self, date):
        with self.conn() as c:
            def one(q, *a):
                return c.execute(q, a).fetchone()[0]
            return {
                "persons": one("SELECT COUNT(*) FROM persons WHERE active = 1"),
                "events_today": one("SELECT COUNT(*) FROM events WHERE substr(ts,1,10) = ?", date),
                "entries_today": one(
                    "SELECT COUNT(DISTINCT person_id) FROM events WHERE person_id IS NOT NULL"
                    " AND substr(ts,1,10) = ?", date),
                "unknown_today": one(
                    "SELECT COUNT(*) FROM events WHERE person_id IS NULL AND substr(ts,1,10) = ?", date),
                "open_alerts": one(
                    "SELECT COUNT(*) FROM events WHERE acknowledged = 0 AND severity != 'Low'"),
                "critical_today": one(
                    "SELECT COUNT(*) FROM events WHERE severity = 'Critical' AND substr(ts,1,10) = ?", date),
            }

    def hourly_counts(self, date):
        with self.conn() as c:
            rows = c.execute(
                """SELECT CAST(substr(ts, 12, 2) AS INTEGER) AS hr,
                          SUM(CASE WHEN person_id IS NULL THEN 0 ELSE 1 END) AS known,
                          SUM(CASE WHEN person_id IS NULL THEN 1 ELSE 0 END) AS unknown
                   FROM events WHERE substr(ts,1,10) = ? GROUP BY hr""",
                (date,),
            ).fetchall()
        buckets = [{"hour": h, "known": 0, "unknown": 0} for h in range(24)]
        for r in rows:
            buckets[r["hr"]]["known"] = r["known"]
            buckets[r["hr"]]["unknown"] = r["unknown"]
        return buckets
