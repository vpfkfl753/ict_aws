import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


class Conflict(Exception):
    pass


class Store:
    def __init__(self, path: str, lease_seconds: int = 600):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.lease_seconds = lease_seconds
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS exchanges (
                    id TEXT PRIMARY KEY,
                    request_key TEXT NOT NULL UNIQUE,
                    sender TEXT NOT NULL,
                    recipient TEXT NOT NULL,
                    text TEXT NOT NULL,
                    state TEXT NOT NULL,
                    source TEXT,
                    context TEXT,
                    result TEXT,
                    error TEXT,
                    lease TEXT,
                    lease_until REAL NOT NULL DEFAULT 0,
                    created REAL NOT NULL,
                    notified INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS agents (
                    owner TEXT PRIMARY KEY,
                    seen REAL NOT NULL
                );
            """)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def unpack(row):
        if row is None:
            return None
        data = dict(row)
        if data.get("source"):
            data["source"] = json.loads(data["source"])
        return data

    def create(self, request_key, sender, recipient, text):
        with self.db() as db:
            db.execute(
                """INSERT OR IGNORE INTO exchanges
                   (id, request_key, sender, recipient, text, state, created)
                   VALUES (?, ?, ?, ?, ?, 'prepare_pending', ?)""",
                (str(uuid.uuid4()), request_key, sender, recipient, text, time.time()),
            )
            row = db.execute(
                "SELECT * FROM exchanges WHERE request_key = ?", (request_key,)
            ).fetchone()
            if (row["sender"], row["recipient"], row["text"]) != (sender, recipient, text):
                raise Conflict("Request key already used for a different message")
            return self.unpack(row)

    def claim(self, owner):
        now = time.time()
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT OR REPLACE INTO agents (owner, seen) VALUES (?, ?)", (owner, now))
            row = db.execute(
                """SELECT * FROM exchanges WHERE
                   (sender = ? AND (state = 'prepare_pending' OR
                       (state = 'prepare_running' AND lease_until < ?))) OR
                   (recipient = ? AND (state = 'interpret_pending' OR
                       (state = 'interpret_running' AND lease_until < ?)))
                   ORDER BY created LIMIT 1""",
                (owner, now, owner, now),
            ).fetchone()
            if row is None:
                return None
            stage = row["state"].split("_")[0]
            db.execute(
                "UPDATE exchanges SET state = ?, lease = ?, lease_until = ? WHERE id = ?",
                (stage + "_running", str(uuid.uuid4()), now + self.lease_seconds, row["id"]),
            )
            return self.unpack(
                db.execute("SELECT * FROM exchanges WHERE id = ?", (row["id"],)).fetchone()
            )

    def update(self, exchange_id, owner, lease, action, value):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM exchanges WHERE id = ?", (exchange_id,)).fetchone()
            if not row:
                raise Conflict("Unknown exchange")
            stage = row["state"].split("_")[0]
            expected_owner = row["sender"] if stage == "prepare" else row["recipient"]
            if (
                row["state"] not in {"prepare_running", "interpret_running"}
                or owner != expected_owner
                or lease != row["lease"]
                or row["lease_until"] < time.time()
            ):
                raise Conflict("Work lease is invalid or expired")
            if action == "source" and stage == "prepare":
                encoded = json.dumps(value, sort_keys=True)
                if row["source"] and row["source"] != encoded:
                    raise Conflict("Original message already recorded")
                db.execute("UPDATE exchanges SET source = ? WHERE id = ?", (encoded, exchange_id))
            elif action == "context" and stage == "prepare" and row["source"]:
                db.execute(
                    "UPDATE exchanges SET context = ?, state = 'interpret_pending' WHERE id = ?",
                    (value, exchange_id),
                )
            elif action == "result" and stage == "interpret":
                db.execute(
                    "UPDATE exchanges SET result = ?, state = 'completed' WHERE id = ?",
                    (value, exchange_id),
                )
            elif action == "error":
                db.execute(
                    "UPDATE exchanges SET error = ?, state = ? WHERE id = ?",
                    (value, stage + "_failed", exchange_id),
                )
            else:
                raise Conflict("Invalid transition")

    def latest(self, owner, exchange_id=None):
        with self.db() as db:
            if exchange_id:
                row = db.execute(
                    "SELECT * FROM exchanges WHERE recipient = ? AND id = ?",
                    (owner, exchange_id),
                ).fetchone()
            else:
                row = db.execute(
                    "SELECT * FROM exchanges WHERE recipient = ? ORDER BY created DESC LIMIT 1",
                    (owner,),
                ).fetchone()
            return self.unpack(row)

    def notifications(self):
        with self.db() as db:
            return [
                self.unpack(row)
                for row in db.execute(
                    """SELECT * FROM exchanges WHERE notified = 0 AND
                   state IN ('completed', 'prepare_failed', 'interpret_failed')
                   ORDER BY created LIMIT 20"""
                )
            ]

    def acknowledge(self, exchange_id):
        with self.db() as db:
            db.execute("UPDATE exchanges SET notified = 1 WHERE id = ?", (exchange_id,))

    def agents(self):
        with self.db() as db:
            return {row["owner"]: row["seen"] for row in db.execute("SELECT * FROM agents")}
