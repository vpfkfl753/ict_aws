import asyncio
import time
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from tacit.store import Conflict
from tacit.workflow import Workflow


class Submit(BaseModel):
    request_key: str = Field(min_length=1, max_length=200)
    sender: str
    recipient: str
    text: str = Field(min_length=1, max_length=3000)
    mode: Literal["dm", "agent"] = "dm"
    source: dict[str, str] | None = None


class WorkUpdate(BaseModel):
    lease: str = Field(min_length=1)
    action: Literal[
        "source", "approval", "publish", "question", "answer", "ask_user", "result", "error"
    ]
    value: str | dict[str, str] = ""


class Decision(BaseModel):
    owner: str
    version: int
    action: Literal["approve", "edit", "cancel", "user_answer"]
    value: str | None = None


def register_workflow(app, store, members, bridge, agent):
    flow = Workflow(store)
    router = APIRouter(prefix="/v2")

    def registered(owner):
        if owner not in members:
            raise HTTPException(403, "Registered user required")
        return owner

    def call(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Conflict as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/exchanges", dependencies=[Depends(bridge)])
    def submit(body: Submit):
        registered(body.sender)
        recipients = (
            [p for p in members if p != body.sender]
            if body.recipient == "broadcast" and body.mode == "agent"
            else [registered(body.recipient)]
        )
        if body.sender in recipients:
            raise HTTPException(400, "Choose another participant")
        if body.source and (
            set(body.source) != {"channel", "ts"} or not body.source["channel"].startswith("D")
        ):
            raise HTTPException(422, "DM source required")
        tasks = [
            call(
                flow.create,
                body.request_key + ":" + recipient,
                body.sender,
                recipient,
                body.text,
                body.mode,
                body.source,
            )
            for recipient in recipients
        ]
        return (
            tasks[0]
            if len(tasks) == 1
            else {"id": tasks[0]["id"], "exchanges": [t["id"] for t in tasks]}
        )

    @router.get("/exchanges", dependencies=[Depends(bridge)])
    def recent(owner: str):
        return flow.recent(registered(owner))

    @router.get("/exchanges/latest", dependencies=[Depends(bridge)])
    def latest(owner: str, exchange_id: str | None = None, received_only: bool = False):
        registered(owner)
        if received_only and exchange_id is None:
            for summary in flow.recent(owner):
                task = flow.get(owner, summary["id"])
                if (task["mode"] == "agent" and task["sender"] == owner) or (
                    task["mode"] == "dm" and task["recipient"] == owner
                ):
                    return task
            return None
        return flow.get(owner, exchange_id)

    @router.get("/settings", dependencies=[Depends(bridge)])
    def settings(owner: str):
        return flow.settings(registered(owner))

    @router.post("/settings", dependencies=[Depends(bridge)])
    def configure(owner: str, body: dict):
        return call(flow.settings, registered(owner), body)

    @router.get("/worker/settings")
    def worker_settings(owner=Depends(agent)):
        return flow.settings(owner)

    @router.get("/worker/info")
    def worker_info(owner=Depends(agent)):
        with store.db() as db:
            row = db.execute("SELECT value FROM workflow_meta WHERE key='bot_user'").fetchone()
        return {"bot_user": row[0] if row else None}

    @router.post("/audit/{task_id}", dependencies=[Depends(bridge)])
    def audit(task_id: str, owner: str):
        return call(flow.request_audit, registered(owner), task_id)

    @router.get("/worker/requests")
    def local_requests(owner=Depends(agent)):
        return flow.local_requests(owner)

    @router.post("/previews/{task_id}", dependencies=[Depends(bridge)])
    def preview(task_id: str, owner: str, version: int):
        return call(flow.request_preview, registered(owner), task_id, version)

    @router.post("/worker/requests/{request_id}")
    def finish_request(request_id: str, owner=Depends(agent)):
        flow.local_requests(owner, request_id)
        return {"ok": True}

    @router.post("/bridge/info", dependencies=[Depends(bridge)])
    def bridge_info(body: dict[str, str]):
        bot = body.get("bot_user", "")
        if not bot.startswith("U") or not bot.isalnum():
            raise HTTPException(422, "Bot user ID required")
        with store.db() as db:
            db.execute("INSERT OR REPLACE INTO workflow_meta VALUES ('bot_user', ?)", (bot,))
        return {"ok": True}

    @router.get("/deliveries", dependencies=[Depends(bridge)])
    def deliveries():
        return flow.deliveries()

    @router.post("/deliveries/{task_id}", dependencies=[Depends(bridge)])
    def delivered(task_id: str, body: dict):
        try:
            call(
                flow.delivered,
                task_id,
                registered(body["owner"]),
                int(body["seq"]),
                body["channel"],
                body["ts"],
            )
        except (KeyError, ValueError, TypeError) as exc:
            raise HTTPException(422, "Invalid delivery") from exc
        return {"ok": True}

    @router.post("/decisions/{task_id}", dependencies=[Depends(bridge)])
    def decide(task_id: str, body: Decision):
        return call(
            flow.change,
            task_id,
            registered(body.owner),
            body.action,
            body.value,
            version=body.version,
        )

    @router.post("/work/claim")
    async def claim(
        request: Request,
        wait_seconds: int = Query(default=0, ge=0, le=180),
        include_requests: bool = False,
        owner=Depends(agent),
    ):
        deadline = time.monotonic() + wait_seconds
        while True:
            if await request.is_disconnected():
                return None
            # Audit requests wake the same long poll without leasing model work
            # while a local Slack notice is still being delivered.
            requests = (
                await run_in_threadpool(flow.local_requests, owner) if include_requests else []
            )
            if requests:
                return {"work": None, "requests": requests}
            work = await run_in_threadpool(flow.claim, owner)
            if work is not None or time.monotonic() >= deadline:
                return {"work": work, "requests": []} if include_requests else work
            await asyncio.sleep(min(1, max(0, deadline - time.monotonic())))

    @router.get("/work/{task_id}")
    def read_work(task_id: str, owner=Depends(agent)):
        task = flow.get(owner, task_id)
        if task is None:
            raise HTTPException(404, "Unknown exchange")
        return task

    @router.post("/work/{task_id}")
    def update(task_id: str, body: WorkUpdate, owner=Depends(agent)):
        if body.action == "source":
            if (
                not isinstance(body.value, dict)
                or set(body.value) != {"channel", "ts"}
                or not body.value["channel"].startswith("D")
                or not body.value["ts"]
            ):
                raise HTTPException(422, "DM channel and timestamp required")
        elif not isinstance(body.value, str) or not body.value.strip() or len(body.value) > 5000:
            raise HTTPException(422, "Nonempty text up to 5000 characters required")
        return call(flow.change, task_id, owner, body.action, body.value, lease=body.lease)

    app.include_router(router)
    return flow
