import logging
import os
import re
import threading
import time

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from tacit.slack_compose import compose_view, register_compose

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
    if exchange["state"].endswith("_failed") or exchange["state"] == "failed":
        return prefix + f"처리에 실패했습니다. {exchange['error']}"
    if exchange["state"] == "cancelled":
        return prefix + "추가 맥락 공유를 취소했습니다."
    if exchange["state"] in {"approval_wait", "user_wait"}:
        return prefix + (
            "공유 승인을 기다리고 있습니다."
            if exchange["state"] == "approval_wait"
            else "사용자 답변을 기다리고 있습니다."
        )
    return prefix + f"처리 중입니다: {exchange['state']}"


def register_handlers(app, relay, team, protocol=1, bot_user=None):
    @app.command("/tacit-send")
    def send(ack, command, respond, client):
        ack()
        log.info("Received /tacit-send team=%s user=%s", command["team_id"], command["user_id"])
        if command["team_id"] != team:
            respond("등록된 워크스페이스에서만 사용할 수 있습니다.")
            return
        try:
            if protocol == 2 and command["text"].strip().startswith("@broadcast "):
                recipient, text = "broadcast", command["text"].strip()[11:]
            else:
                recipient, text = parse_send(command["text"])
        except ValueError:
            if len(command["text"]) > 3000:
                respond(
                    "메시지를 3000자 이내로 입력하거나 /tacit-send @상대 메시지 형식으로 보내주세요."
                )
                return
            try:
                client.views_open(
                    trigger_id=command["trigger_id"],
                    view=compose_view(command["text"], protocol=protocol),
                )
                log.info("Opened compose modal user=%s", command["user_id"])
            except Exception as exc:
                log.error("Compose modal could not open (%s)", type(exc).__name__)
                respond("전송 창을 열지 못했습니다. /tacit-send를 다시 실행해주세요.")
            return
        try:
            extra = {}
            if protocol == 2:
                mode = "agent" if recipient == "broadcast" else "dm"
                if bot_user and command.get("channel_id"):
                    try:
                        if (
                            bot_user
                            in client.conversations_members(channel=command["channel_id"])[
                                "members"
                            ]
                        ):
                            mode = "agent"
                    except Exception:
                        pass
                extra["mode"] = mode
            exchange = relay.request(
                "POST",
                f"/v{protocol}/exchanges",
                json={
                    "request_key": f"{team}:{command['trigger_id']}",
                    "sender": command["user_id"],
                    "recipient": recipient,
                    "text": text,
                    **extra,
                },
            )
        except ValueError as exc:
            respond(str(exc))
            return
        except Exception:
            respond("접수하지 못했습니다. 두 사용자 등록과 중계 서버 상태를 확인해주세요.")
            return
        respond(
            f"접수했습니다. 원문 또는 Agent 전용 요청을 처리하고, 추가 맥락은 승인 후 공유합니다.\n{exchange['id']}"
            if protocol == 2
            else f"접수했습니다. 송신 Agent가 원문 DM과 맥락을 전달합니다.\n{exchange['id']}"
        )

    @app.command("/tacit-receive")
    def receive(ack, command, respond):
        ack()
        log.info("Received /tacit-receive team=%s user=%s", command["team_id"], command["user_id"])
        if command["team_id"] != team:
            respond("등록된 워크스페이스에서만 사용할 수 있습니다.")
            return
        try:
            params = {"owner": command["user_id"]}
            if protocol == 2:
                params["received_only"] = True
            if command["text"].strip():
                params["exchange_id"] = command["text"].strip()
            exchange = relay.request("GET", f"/v{protocol}/exchanges/latest", params=params)
            respond(response_type="ephemeral", text=render(exchange))
        except Exception:
            respond("조회하지 못했습니다. 중계 서버 상태를 확인해주세요.")

    @app.command("/tacit-status")
    def status(ack, command, respond):
        ack()
        log.info("Received /tacit-status team=%s user=%s", command["team_id"], command["user_id"])
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


def exit_for_restart():
    # StreamHandler.emit already flushed the reason, and logging.shutdown() would block
    # here whenever a wedged thread still holds the handler lock. Fail hard instead, so
    # systemd Restart=on-failure applies.
    os._exit(1)


def watch_connection(client, stop, on_stall, stall_seconds=120, interval=5):
    """End the process once Socket Mode stops carrying traffic.

    A wedged client reconnects forever without ever receiving a message, so the
    process stays alive and the unit stays active while every slash command fails
    with dispatch_failed. Each failed attempt is briefly connected, so a healthy
    interval has to be both connected and free of connection errors.
    """
    errors = [0]

    def record(error):
        errors[0] += 1

    client.on_error_listeners.append(record)
    healthy = time.monotonic()
    seen = 0
    while not stop.wait(interval):
        total = errors[0]
        broke, seen = total != seen, total
        if client.is_connected() and not broke:
            healthy = time.monotonic()
            continue
        stalled = time.monotonic() - healthy
        if stalled >= stall_seconds:
            log.error("Socket Mode has been unusable for %ds; exiting to restart", int(stalled))
            on_stall()
            return


def run_slack(relay, bot_token, app_token, team):
    app = App(token=bot_token)
    auth = app.client.auth_test()
    if auth["team_id"] != team:
        raise ValueError("SLACK_BOT_TOKEN does not belong to TACIT_TEAM_ID")
    protocol = relay.request("GET", "/health")["protocol"]
    register_handlers(app, relay, team, protocol, auth["user_id"])
    register_compose(app, relay, team, protocol)
    if protocol == 2:
        from tacit.slack_product import notify_product_once, register_product

        relay.request("POST", "/v2/bridge/info", json={"bot_user": auth["user_id"]})
        register_product(app, relay, team)
    stop = threading.Event()

    def notifications():
        while not stop.is_set():
            try:
                notify_once(relay, app.client)
                if protocol == 2:
                    notify_product_once(relay, app.client)
            except Exception as exc:
                log.error("Notification delivery failed (%s); will retry", type(exc).__name__)
            stop.wait(5)

    thread = threading.Thread(target=notifications, daemon=True)
    thread.start()
    handler = SocketModeHandler(app, app_token)
    watchdog = threading.Thread(
        target=watch_connection,
        args=(handler.client, stop, exit_for_restart),
        daemon=True,
    )
    watchdog.start()
    try:
        handler.start()
    finally:
        stop.set()
        handler.close()
        thread.join(timeout=45)
