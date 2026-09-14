import logging
import re
import threading
import time

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

log = logging.getLogger(__name__)


def parse_send(text):
    match = re.fullmatch(r"\s*<@([UW][A-Z0-9]+)(?:\|[^>]+)?>\s+(.+?)\s*", text, re.DOTALL)
    if not match:
        raise ValueError("사용법: /tacit-send @상대 메시지")
    return match.group(1), match.group(2)


def render(exchange):
    if exchange is None:
        return "아직 수신한 맥락이 없습니다."
    prefix = f"맥락 전달 {exchange['id']}\n"
    if exchange["state"] == "completed":
        return prefix + f"원문: {exchange['text'][:800]}\n\n{exchange['result']}"
    if exchange["state"].endswith("_failed"):
        return prefix + f"처리에 실패했습니다. {exchange['error']}"
    return prefix + f"처리 중입니다: {exchange['state']}"


def register_handlers(app, relay, team):
    @app.command("/tacit-send")
    def send(ack, command, respond):
        ack()
        if command["team_id"] != team:
            respond("등록된 워크스페이스에서만 사용할 수 있습니다.")
            return
        try:
            recipient, text = parse_send(command["text"])
            exchange = relay.request(
                "POST",
                "/v1/exchanges",
                json={
                    "request_key": f"{team}:{command['trigger_id']}",
                    "sender": command["user_id"],
                    "recipient": recipient,
                    "text": text,
                },
            )
        except ValueError as exc:
            respond(str(exc))
            return
        except Exception:
            respond("접수하지 못했습니다. 두 사용자 등록과 중계 서버 상태를 확인해주세요.")
            return
        respond(f"접수했습니다. 송신 Agent가 원문 DM과 맥락을 전달합니다.\n{exchange['id']}")

    @app.command("/tacit-receive")
    def receive(ack, command, respond):
        ack()
        if command["team_id"] != team:
            respond("등록된 워크스페이스에서만 사용할 수 있습니다.")
            return
        try:
            params = {"owner": command["user_id"]}
            if command["text"].strip():
                params["exchange_id"] = command["text"].strip()
            exchange = relay.request("GET", "/v1/exchanges/latest", params=params)
            respond(response_type="ephemeral", text=render(exchange))
        except Exception:
            respond("조회하지 못했습니다. 중계 서버 상태를 확인해주세요.")

    @app.command("/tacit-status")
    def status(ack, command, respond):
        ack()
        if command["team_id"] != team:
            respond("등록된 워크스페이스에서만 사용할 수 있습니다.")
            return
        try:
            agents = relay.request("GET", "/v1/agents")
            if command["user_id"] not in agents:
                respond("등록된 사용자만 조회할 수 있습니다.")
                return
            lines = []
            for owner, seen in agents.items():
                state = "최근 연결됨" if seen and time.time() - seen < 600 else "연결 대기"
                lines.append(f"<@{owner}>: {state}")
            respond("\n".join(lines))
        except Exception:
            respond("중계 서버에 연결할 수 없습니다.")


def notify_once(relay, client):
    for exchange in relay.request("GET", "/v1/notifications"):
        owner = (
            exchange["sender"] if exchange["state"] == "prepare_failed" else exchange["recipient"]
        )
        channel = client.conversations_open(users=owner)["channel"]["id"]
        # Plain-text blocks prevent model output from creating mentions or links.
        text = render(exchange)
        blocks = [
            {"type": "section", "text": {"type": "plain_text", "text": text[i : i + 2900]}}
            for i in range(0, len(text), 2900)
        ]
        client.chat_postMessage(
            channel=channel,
            text=f"맥락 전달 {exchange['id']}",
            blocks=blocks,
            unfurl_links=False,
            unfurl_media=False,
        )
        relay.request("POST", f"/v1/notifications/{exchange['id']}/ack")


def run_slack(relay, bot_token, app_token, team):
    app = App(token=bot_token)
    if app.client.auth_test()["team_id"] != team:
        raise ValueError("SLACK_BOT_TOKEN does not belong to TACIT_TEAM_ID")
    register_handlers(app, relay, team)
    stop = threading.Event()

    def notifications():
        while not stop.is_set():
            try:
                notify_once(relay, app.client)
            except Exception as exc:
                log.error("Notification delivery failed (%s); will retry", type(exc).__name__)
            stop.wait(5)

    thread = threading.Thread(target=notifications, daemon=True)
    thread.start()
    handler = SocketModeHandler(app, app_token)
    try:
        handler.start()
    finally:
        stop.set()
        handler.close()
        thread.join(timeout=45)
