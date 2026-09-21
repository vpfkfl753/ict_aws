import logging
import uuid

import httpx

from tacit.presentation import readable_mentions

log = logging.getLogger(__name__)


def compose_view(text="", recipient=None, request_key=None, error=None, protocol=1):
    message = {
        "type": "plain_text_input",
        "action_id": "text",
        "multiline": True,
        "max_length": 3000,
    }
    if text:
        message["initial_value"] = readable_mentions(text)
    recipient_input = {"type": "users_select", "action_id": "user"}
    if recipient:
        recipient_input["initial_user"] = recipient
    blocks = []
    if error:
        blocks.append({"type": "section", "text": {"type": "plain_text", "text": error}})
    blocks.extend(
        [
            {
                "type": "input",
                "block_id": "recipient",
                "label": {"type": "plain_text", "text": "받는 사람"},
                "element": recipient_input,
            },
            {
                "type": "input",
                "block_id": "message",
                "label": {"type": "plain_text", "text": "메시지"},
                "element": message,
            },
        ]
    )
    if protocol == 2:
        blocks[-2]["optional"] = True
        blocks.append(
            {
                "type": "input",
                "block_id": "mode",
                "label": {"type": "plain_text", "text": "전송 방식"},
                "element": {
                    "type": "static_select",
                    "action_id": "value",
                    "initial_option": {
                        "text": {"type": "plain_text", "text": "원문 DM + 맥락"},
                        "value": "dm",
                    },
                    "options": [
                        {"text": {"type": "plain_text", "text": label}, "value": mode}
                        for label, mode in (
                            ("원문 DM + 맥락", "dm"),
                            ("Agent끼리 질문", "agent"),
                            ("모든 Agent에게 질문", "broadcast"),
                        )
                    ],
                },
            }
        )
    return {
        "type": "modal",
        "callback_id": "tacit_compose",
        "title": {"type": "plain_text", "text": "Tacit 메시지"},
        "submit": {"type": "plain_text", "text": "전송"},
        "close": {"type": "plain_text", "text": "취소"},
        "private_metadata": request_key or str(uuid.uuid4()),
        "blocks": blocks,
    }


def status_view(text):
    return {
        "type": "modal",
        "title": {"type": "plain_text", "text": "Tacit 메시지"},
        "close": {"type": "plain_text", "text": "닫기"},
        "blocks": [{"type": "section", "text": {"type": "plain_text", "text": text}}],
    }


def register_compose(app, relay, team, protocol=1):
    @app.view("tacit_compose")
    def submit(ack, body, view, client):
        values = view["state"]["values"]
        recipient = values["recipient"]["user"].get("selected_user")
        mode = values.get("mode", {}).get("value", {}).get("selected_option", {}).get("value", "dm")
        if protocol == 2 and mode == "broadcast":
            recipient, mode = "broadcast", "agent"
        text = (values["message"]["text"].get("value") or "").strip()
        errors = {}
        if body.get("team", {}).get("id") != team:
            errors["recipient"] = "등록된 워크스페이스에서만 사용할 수 있습니다."
        elif not recipient or recipient == body["user"]["id"]:
            errors["recipient"] = "메시지를 받을 상대를 선택해주세요."
        if not text or len(text) > 3000:
            errors["message"] = "메시지를 1~3000자로 입력해주세요."
        if errors:
            ack(response_action="errors", errors=errors)
            return
        # Acknowledge before contacting the relay; update this same modal afterwards.
        ack(response_action="update", view=status_view("접수 중입니다."))
        request_key = view["private_metadata"]
        try:
            exchange = relay.request(
                "POST",
                f"/v{protocol}/exchanges",
                json={
                    "request_key": f"{team}:modal:{request_key}",
                    "sender": body["user"]["id"],
                    "recipient": recipient,
                    "text": text,
                    **({"mode": mode} if protocol == 2 else {}),
                },
            )
        except httpx.HTTPStatusError as exc:
            reason = (
                "두 사용자가 서비스에 등록되어 있는지 확인해주세요."
                if exc.response.status_code == 400
                else "접수 결과를 확인하지 못했습니다. 잠시 후 다시 시도해주세요."
            )
            updated = compose_view(
                text,
                recipient if recipient != "broadcast" else None,
                request_key,
                error=reason,
                protocol=protocol,
            )
            log.warning("Modal submission rejected status=%s", exc.response.status_code)
        except (httpx.HTTPError, ValueError):
            updated = compose_view(
                text,
                recipient,
                request_key,
                error="접수 결과를 확인하지 못했습니다. 잠시 후 다시 시도해주세요.",
                protocol=protocol,
            )
            log.warning("Modal submission relay unavailable")
        else:
            updated = status_view(f"접수했습니다.\n전달 ID: {exchange['id']}")
            log.info("Modal submitted exchange=%s sender=%s", exchange["id"], body["user"]["id"])
        client.views_update(view_id=view["id"], view=updated)
