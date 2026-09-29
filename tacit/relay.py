import asyncio
import hmac
import html
import json
import os
import time
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

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


LANDING = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Tacit</title>
<style>
:root { color-scheme: light dark; --bg: #f7f7f5; --fg: #1d1d1f; --muted: #5f6368;
  --card: #ffffff; --line: #e2e2de; --accent: #2f5bd3; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #151618; --fg: #ececec; --muted: #a0a4ab; --card: #1f2023;
    --line: #34363b; --accent: #8fb0ff; }
}
body { margin: 0; background: var(--bg); color: var(--fg); line-height: 1.6;
  font-family: system-ui, -apple-system, "Apple SD Gothic Neo", "Noto Sans KR", sans-serif; }
main { max-width: 640px; margin: 0 auto; padding: 40px 16px; }
h1 { margin: 0 0 8px; font-size: 2rem; }
h2 { font-size: 1.1rem; margin: 32px 0 12px; }
p { margin: 0; color: var(--muted); }
ol { margin: 0; padding: 16px 16px 16px 36px; background: var(--card);
  border: 1px solid var(--line); border-radius: 12px; }
li + li { margin-top: 8px; }
code { font-size: 0.95em; padding: 1px 4px; border-radius: 4px; background: var(--line); }
nav { display: flex; flex-wrap: wrap; gap: 12px; margin-top: 24px; }
a { color: var(--accent); }
footer { margin-top: 32px; font-size: 0.9rem; color: var(--muted); }
</style>
</head>
<body>
<main>
<h1>Tacit</h1>
<p>팀에 흩어진 암묵지를 Slack DM 대화에 연결하는 B2B 협업 에이전트입니다.</p>
<h2>체험 방법</h2>
<ol>
<li>서비스 소개서의 테스트 계정으로 데모 Slack 워크스페이스에 로그인합니다.</li>
<li>상대 테스트 계정과의 DM에서 <code>/tacit-send @상대 메시지</code>를 실행합니다.</li>
<li>Tacit이 보낸 사람에게 공유 승인을 받고, 받는 사람에게 맞춤 설명을 보여줍니다.</li>
</ol>
{links}
<footer>서버 정상 · protocol 2</footer>
</main>
</body>
</html>
"""


def landing_page():
    links = [
        f'<a href="{html.escape(url)}">{label}</a>'
        for name, label in (
            ("TACIT_DEMO_SLACK_URL", "데모 Slack 워크스페이스"),
            ("TACIT_REPO_URL", "소스 저장소"),
        )
        if (url := os.environ.get(name, "").strip())
    ]
    return LANDING.replace("{links}", f"<nav>{''.join(links)}</nav>" if links else "")


def create_app(store=None, members=None, bridge_token=None):
    store = store or Store(os.environ.get("TACIT_DB", ".tacit/relay.sqlite3"))
    members = members if members is not None else json.loads(os.environ["TACIT_MEMBERS"])
    bridge_token = bridge_token or os.environ["TACIT_BRIDGE_TOKEN"]
    tokens = [*members.values(), bridge_token]
    if len(members) < 2 or len(set(tokens)) != len(tokens) or any(len(t) < 24 for t in tokens):
        raise ValueError(
            "Configure at least two members and distinct tokens of at least 24 characters"
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

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def landing():
        return landing_page()

    @app.get("/health")
    def health():
        return {"status": "ok", "protocol": 2}

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
    async def claim(
        request: Request, wait_seconds: int = Query(default=0, ge=0, le=180), owner=Depends(agent)
    ):
        deadline = time.monotonic() + wait_seconds
        while True:
            if await request.is_disconnected():
                return None
            work = await run_in_threadpool(store.claim, owner)
            if work is not None or time.monotonic() >= deadline:
                return work
            await asyncio.sleep(min(1, max(0, deadline - time.monotonic())))

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

    from tacit.workflow_api import register_workflow

    register_workflow(app, store, members, bridge, agent)
    return app
