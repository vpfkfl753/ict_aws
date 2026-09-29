"""Versioned, approval-gated exchanges. Private drafts never enter this store."""

import hashlib
import json
import time
import uuid

from tacit.store import Conflict

DEFAULT_SETTINGS = {
    "workspace": "",
    "backend": "",
    "model": "",
    "web": False,
    "history": True,
    "auto_receive": True,
    "auto_send": False,
    "auto_share": False,
}
BACKENDS = {"", "kiro", "codex", "opencode", "bedrock", "openai"}


class Workflow:
    def __init__(self, store):
        self.store = store
        with store.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS workflows (
                    id TEXT PRIMARY KEY, request_key TEXT UNIQUE NOT NULL,
                    body TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS preferences (
                    owner TEXT PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS workflow_meta (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS local_requests (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL,
                    task TEXT NOT NULL, state TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS preview_requests (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, task TEXT NOT NULL,
                    version INTEGER NOT NULL, state TEXT NOT NULL);
            """)

    def settings(self, owner, value=None):
        with self.store.db() as db:
            if value is not None:
                if set(value) - set(DEFAULT_SETTINGS):
                    raise Conflict("Unknown preference")
                if value.get("backend", "") not in BACKENDS:
                    raise Conflict("Unknown backend")
                if any(
                    not isinstance(value.get(k, False), bool)
                    for k in ("web", "history", "auto_send", "auto_receive", "auto_share")
                ):
                    raise Conflict("Boolean preference required")
                if any(
                    not isinstance(value.get(k, ""), str) or len(value.get(k, "")) > 1000
                    for k in ("workspace", "model")
                ):
                    raise Conflict("Invalid text preference")
                db.execute(
                    "INSERT OR REPLACE INTO preferences VALUES (?, ?)",
                    (owner, json.dumps({**DEFAULT_SETTINGS, **value})),
                )
            row = db.execute("SELECT body FROM preferences WHERE owner=?", (owner,)).fetchone()
            return {**DEFAULT_SETTINGS, **json.loads(row[0])} if row else dict(DEFAULT_SETTINGS)

    @staticmethod
    def event(task, kind, actor, text=""):
        task["events"].append(
            {
                "seq": len(task["events"]) + 1,
                "kind": kind,
                "actor": actor,
                "text": text,
                "at": time.time(),
                "version": task.get("version", 0),
            }
        )

    @staticmethod
    def save(db, task):
        db.execute(
            "UPDATE workflows SET body=? WHERE id=?",
            (json.dumps(task, ensure_ascii=False), task["id"]),
        )

    def create(self, request_key, sender, recipient, text, mode="dm", source=None):
        with self.store.db() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                "SELECT body FROM workflows WHERE request_key=?", (request_key,)
            ).fetchone()
            if previous:
                task = json.loads(previous[0])
                if (task["sender"], task["recipient"], task["text"], task["mode"]) != (
                    sender,
                    recipient,
                    text,
                    mode,
                ):
                    raise Conflict("Request key already used")
                return task
            task = dict(
                id=str(uuid.uuid4()),
                sender=sender,
                recipient=recipient,
                text=text,
                mode=mode,
                source=source,
                state="prepare_pending",
                owner=sender,
                round=0,
                version=0,
                context="",
                question="",
                answer="",
                result="",
                events=[],
                lease="",
                lease_until=0,
                notified=0,
                created=time.time(),
            )
            self.event(task, "created", sender)
            db.execute(
                "INSERT INTO workflows VALUES (?, ?, ?, ?)",
                (task["id"], request_key, json.dumps(task), task["created"]),
            )
            return task

    def claim(self, owner):
        with self.store.db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT OR REPLACE INTO agents VALUES (?, ?)", (owner, time.time()))
            for row in db.execute("SELECT body FROM workflows ORDER BY created"):
                task = json.loads(row[0])
                if task["owner"] != owner:
                    continue
                if not (
                    task["state"].endswith("_pending")
                    or (task["state"].endswith("_running") and task["lease_until"] < time.time())
                ):
                    continue
                task.update(
                    state=task["state"].split("_")[0] + "_running",
                    lease=str(uuid.uuid4()),
                    lease_until=time.time() + self.store.lease_seconds,
                )
                self.save(db, task)
                if owner != task["recipient"]:
                    task.pop("user_answer", None)
                    task.pop("user_question", None)
                return task

    def get(self, owner, task_id=None):
        with self.store.db() as db:
            rows = db.execute("SELECT body FROM workflows ORDER BY created DESC")
            for row in rows:
                task = json.loads(row[0])
                if owner in (task["sender"], task["recipient"]) and (
                    task_id is None or task["id"] == task_id
                ):
                    if owner != task["recipient"]:
                        if task["mode"] != "agent" or task["state"] != "completed":
                            task["result"] = ""
                        task.pop("user_answer", None)
                        task["events"] = [
                            e
                            for e in task["events"]
                            if e["kind"] not in {"user_question", "user_answer", "result"}
                        ]
                    return task

    def recent(self, owner):
        with self.store.db() as db:
            tasks = [
                json.loads(r[0])
                for r in db.execute("SELECT body FROM workflows ORDER BY created DESC")
            ]
        return [
            {k: t[k] for k in ("id", "sender", "recipient", "state", "created", "text")}
            for t in tasks
            if owner in (t["sender"], t["recipient"])
        ][:20]

    def request_audit(self, owner, task_id):
        if self.get(owner, task_id) is None:
            raise Conflict("Only participating users may request local audit")
        request_id = str(uuid.uuid4())
        with self.store.db() as db:
            db.execute(
                "INSERT INTO local_requests VALUES (?, ?, ?, 'pending')",
                (request_id, owner, task_id),
            )
        return {"id": request_id}

    def local_requests(self, owner, done=None):
        with self.store.db() as db:
            if done:
                db.execute(
                    "UPDATE local_requests SET state='done' WHERE owner=? AND id=?", (owner, done)
                )
                db.execute(
                    "UPDATE preview_requests SET state='done' WHERE owner=? AND id=?", (owner, done)
                )
            previews = [
                {**dict(row), "kind": "preview"}
                for row in db.execute(
                    "SELECT * FROM preview_requests WHERE owner=? AND state='pending' LIMIT 10",
                    (owner,),
                )
            ]
            return previews + [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM local_requests WHERE owner=? AND state='pending' LIMIT 10",
                    (owner,),
                )
            ]

    def request_preview(self, owner, task_id, version):
        task = self.get(owner, task_id)
        if (
            not task
            or task["owner"] != owner
            or task["version"] != version
            or task["state"] not in {"approval_wait", "user_wait"}
        ):
            raise Conflict("This preview is no longer available")
        request_id = str(uuid.uuid4())
        with self.store.db() as db:
            db.execute(
                "INSERT INTO preview_requests VALUES (?, ?, ?, ?, 'pending')",
                (request_id, owner, task_id, version),
            )
        return {"id": request_id}

    def deliveries(self):
        from tacit.presentation import card_snapshot

        with self.store.db() as db:
            tasks = [
                json.loads(r[0]) for r in db.execute("SELECT body FROM workflows ORDER BY created")
            ]
        items = []
        for t in tasks:
            for owner in (t["sender"], t["recipient"]):
                delivered = t.get("delivery", {}).get(owner, {})
                for e in t["events"]:
                    if e["seq"] <= delivered.get("seq", 0):
                        continue
                    if e["kind"] in {"user_question", "user_answer"} and owner != t["recipient"]:
                        continue
                    if e["kind"] == "result" and owner != t["recipient"]:
                        continue
                    text = t["result"] if e["kind"] == "result" else e["text"]
                    auto_receive = self.settings(owner)["auto_receive"]
                    if e["kind"] == "result" and not auto_receive:
                        text = "설명이 준비됐어요. /tacit-receive로 확인할 수 있어요."
                    items.append(
                        {
                            "id": t["id"],
                            "owner": owner,
                            "seq": e["seq"],
                            "kind": e["kind"],
                            "actor": e["actor"],
                            "event_version": e.get("version"),
                            "text": text,
                            "card": card_snapshot(t, owner, auto_receive),
                            "thread": delivered.get("thread"),
                        }
                    )
                    break
        return items[:20]

    def delivered(self, task_id, owner, seq, channel, ts):
        with self.store.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT body FROM workflows WHERE id=?", (task_id,)).fetchone()
            if not row:
                raise Conflict("Unknown exchange")
            task = json.loads(row[0])
            if owner not in (task["sender"], task["recipient"]) or not 0 < seq <= len(
                task["events"]
            ):
                raise Conflict("Invalid delivery")
            delivery = task.setdefault("delivery", {}).setdefault(owner, {})
            if seq > delivery.get("seq", 0):
                delivery.update(
                    seq=seq, thread=delivery.get("thread") or {"channel": channel, "ts": ts}
                )
                self.save(db, task)

    def change(self, task_id, actor, action, value=None, lease=None, version=None):
        with self.store.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT body FROM workflows WHERE id=?", (task_id,)).fetchone()
            if not row:
                raise Conflict("Unknown exchange")
            t = json.loads(row[0])
            if actor != t["owner"]:
                raise Conflict("This user does not own the current step")
            if lease is not None:
                if (
                    not t["state"].endswith("_running")
                    or lease != t["lease"]
                    or t["lease_until"] < time.time()
                ):
                    raise Conflict("Expired or invalid lease")
            elif version != t["version"]:
                raise Conflict("Stale action; reopen the current message")
            stage = t["state"].split("_")[0]
            if action == "source" and lease and stage == "prepare":
                if t["source"] and t["source"] != value:
                    raise Conflict("Original already sent")
                t["source"] = value
                self.event(t, "original_sent", actor)
            elif (
                action == "auto_share"
                and lease
                and (
                    stage in {"prepare", "reply"} or (stage == "interpret" and t["mode"] == "agent")
                )
            ):
                preference = db.execute(
                    "SELECT body FROM preferences WHERE owner=?", (actor,)
                ).fetchone()
                if not preference or json.loads(preference[0]).get("auto_share") is not True:
                    raise Conflict("Automatic sharing is disabled")
                if not isinstance(value, str) or not value.strip() or len(value) > 5000:
                    raise Conflict("Shared text must be 1–5000 characters")
                if stage == "prepare" and t["mode"] == "dm" and not t["source"]:
                    raise Conflict("Original must precede context")
                t.update(
                    version=t["version"] + 1, digest=hashlib.sha256(value.encode()).hexdigest()
                )
                self.event(t, "auto_shared", actor)
                if stage == "interpret":
                    t.update(result=value, state="completed", owner=t["sender"])
                    self.event(t, "agent_result", actor, value)
                else:
                    key = "context" if stage == "prepare" else "answer"
                    t.update({key: value, "state": "interpret_pending", "owner": t["recipient"]})
                    self.event(t, key, actor, value)
            elif (
                action == "approval"
                and lease
                and (
                    stage in {"prepare", "reply"} or (stage == "interpret" and t["mode"] == "agent")
                )
            ):
                if stage == "prepare" and t["mode"] == "dm" and not t["source"]:
                    raise Conflict("Original must precede context")
                # Only a digest is accepted, never the private draft.
                if (
                    not isinstance(value, str)
                    or len(value) != 64
                    or any(c not in "0123456789abcdef" for c in value)
                ):
                    raise Conflict("SHA256 digest required")
                t.update(
                    state="approval_wait", digest=value, resume=stage, version=t["version"] + 1
                )
                self.event(t, "approval_requested", actor)
            elif (
                action in {"approve", "edit", "cancel"}
                and not lease
                and t["state"] == "approval_wait"
            ):
                if action == "cancel":
                    t.update(state="cancelled")
                else:
                    if action == "edit":
                        if not isinstance(value, str) or not value.strip() or len(value) > 5000:
                            raise Conflict("Approved text must be 1–5000 characters")
                        t["approved_edit"] = value
                    t.update(state="publish_pending")
                self.event(t, action, actor)
            elif action == "publish" and lease and stage == "publish":
                edited = t.get("approved_edit")
                if edited is not None and value != edited:
                    raise Conflict("Only the edited approved version may be shared")
                if edited is None and hashlib.sha256(value.encode()).hexdigest() != t["digest"]:
                    raise Conflict("Draft changed after approval")
                if t["resume"] == "interpret":
                    t["result"] = value
                    kind = "agent_result"
                elif t["resume"] == "prepare":
                    t["context"] = value
                    kind = "context"
                else:
                    t["answer"] = value
                    kind = "answer"
                self.event(t, kind, actor, value)
                t.pop("approved_edit", None)
                if t["resume"] == "interpret":
                    t.update(state="completed", owner=t["sender"])
                else:
                    t.update(state="interpret_pending", owner=t["recipient"])
            elif (
                action == "question"
                and lease
                and stage == "interpret"
                and t["round"] < 2
                and not t.get("user_answer")
            ):
                t.update(
                    question=value, round=t["round"] + 1, owner=t["sender"], state="reply_pending"
                )
                self.event(t, "question", actor, value)
            elif action == "answer" and lease and stage == "reply":
                # Auto replies are constrained to exact text from already shared evidence.
                approved = [e["text"] for e in t["events"] if e["kind"] in {"context", "answer"}]
                if not value.strip() or not any(value in text for text in approved):
                    raise Conflict("Automatic answer must quote approved evidence")
                t.update(answer=value, owner=t["recipient"], state="interpret_pending")
                self.event(t, "answer", actor, value)
            elif (
                action == "ask_user" and lease and stage == "interpret" and not t.get("user_answer")
            ):
                t.update(state="user_wait", user_question=value, version=t["version"] + 1)
                self.event(t, "user_question", actor)
            elif action == "user_answer" and not lease and t["state"] == "user_wait":
                if not isinstance(value, str) or not value.strip() or len(value) > 3000:
                    raise Conflict("Answer must be 1–3000 characters")
                t.update(user_answer=value, state="interpret_pending")
                self.event(t, "user_answer", actor)
            elif action == "result" and lease and stage == "interpret" and t["mode"] == "dm":
                t.update(result=value, state="completed")
                self.event(t, "result", actor)
            elif action == "error" and lease:
                t.update(state="failed", error="Local worker failed; inspect local logs.")
                self.event(t, "failed", actor)
            else:
                raise Conflict("Invalid transition")
            self.save(db, t)
            return {"ok": True, "state": t["state"], "version": t["version"]}
