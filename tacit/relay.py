import hmac
import json
import os
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from tacit.store import Conflict, Store


class Submission(BaseModel):
    request_key: str = Field(min_length=1, max_length=200)
    sender: str
    recipient: str
    text: str = Field(min_length=1, max_length=12000)


class Update(BaseModel):
    lease: str
    action: Literal["source", "context", "result", "error"]
    value: str | dict[str, str]


def create_app(store=None, members=None, bridge_token=None):
    store = store or Store(os.environ.get("TACIT_DB", ".tacit/relay.sqlite3"))
    members = members if members is not None else json.loads(os.environ["TACIT_MEMBERS"])
    bridge_token = bridge_token or os.environ["TACIT_BRIDGE_TOKEN"]
    tokens = [*members.values(), bridge_token]
    if len(members) != 2 or len(set(tokens)) != 3 or any(len(t) < 24 for t in tokens):
        raise ValueError(
            "Configure two members and three distinct tokens of at least 24 characters"
        )
    app = FastAPI(title="Tacit Agent Plane", version="0.1.0")

    def identity(authorization: Annotated[str, Header()] = ""):
        if not authorization.startswith("Bearer "):
            raise HTTPException(401, "Bearer token required")
        token = authorization[7:]
        if hmac.compare_digest(token, bridge_token):
            return "bridge"
        for owner, secret in members.items():
            if hmac.compare_digest(token, secret):
                return owner
        raise HTTPException(401, "Invalid token")

    def bridge(actor=Depends(identity)):
        if actor != "bridge":
            raise HTTPException(403, "Bridge credentials required")

    def agent(actor=Depends(identity)):
        if actor == "bridge":
            raise HTTPException(403, "Agent credentials required")
        return actor

    @app.get("/health")
    def health():
        return {"status": "ok", "protocol": 1}

    @app.get("/v1/agents", dependencies=[Depends(bridge)])
    def agents():
        seen = store.agents()
        return {owner: seen.get(owner) for owner in members}

    @app.post("/v1/exchanges", dependencies=[Depends(bridge)])
    def submit(body: Submission):
        if body.sender not in members or body.recipient not in members:
            raise HTTPException(400, "Both participants must be registered")
        if body.sender == body.recipient:
            raise HTTPException(400, "Choose the other participant")
        try:
            return store.create(**body.model_dump())
        except Conflict as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/v1/exchanges/latest", dependencies=[Depends(bridge)])
    def latest(owner: str, exchange_id: str | None = None):
        return store.latest(owner, exchange_id)

    @app.post("/v1/work/claim")
    def claim(owner=Depends(agent)):
        return store.claim(owner)

    @app.post("/v1/work/{exchange_id}")
    def update(exchange_id: str, body: Update, owner=Depends(agent)):
        if body.action == "source":
            if not isinstance(body.value, dict) or set(body.value) != {"channel", "ts"}:
                raise HTTPException(422, "Source requires channel and ts")
            if not body.value["channel"].startswith("D") or not body.value["ts"]:
                raise HTTPException(422, "Source must identify a direct message")
        elif not isinstance(body.value, str) or not body.value.strip() or len(body.value) > 24000:
            raise HTTPException(422, "Value must be a nonempty string up to 24000 characters")
        try:
            store.update(exchange_id, owner, body.lease, body.action, body.value)
        except Conflict as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"ok": True}

    @app.get("/v1/notifications", dependencies=[Depends(bridge)])
    def notifications():
        return store.notifications()

    @app.post("/v1/notifications/{exchange_id}/ack", dependencies=[Depends(bridge)])
    def acknowledge(exchange_id: str):
        store.acknowledge(exchange_id)
        return {"ok": True}

    return app
