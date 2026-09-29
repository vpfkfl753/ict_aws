"""Slack Home, settings, scoped audit, approval and clarification controls."""

import hashlib
import json
import re
import time
import uuid

from tacit.presentation import card_blocks, compact, draft_text, plain_blocks


def modal(title, callback, metadata, blocks, submit="저장"):
    return {
        "type": "modal",
        "title": {"type": "plain_text", "text": title},
        "callback_id": callback,
        "private_metadata": json.dumps(metadata),
        "submit": {"type": "plain_text", "text": submit},
        "close": {"type": "plain_text", "text": "닫기"},
        "blocks": blocks,
    }


def text_input(name, label, value="", optional=False, multiline=False):
    element = {
        "type": "plain_text_input",
        "action_id": "value",
        "multiline": multiline,
        "max_length": 3000,
    }
    if value:
        element["initial_value"] = value
    return {
        "type": "input",
        "block_id": name,
        "optional": optional,
        "label": {"type": "plain_text", "text": label},
        "element": element,
    }


def settings_view(settings):
    blocks = [
        text_input("workspace", "자료 폴더 (로컬 허용 범위 안에서)", settings["workspace"], True),
        text_input(
            "backend",
            "실행기: kiro / codex / opencode / bedrock / openai (빈칸: 로컬 기본)",
            settings["backend"],
            True,
        ),
        text_input("model", "모델 ID (선택)", settings["model"], True),
    ]
    options = [
        {"text": {"type": "plain_text", "text": label}, "value": key}
        for key, label in (
            ("web", "웹검색"),
            ("history", "대화 기록 참고"),
            ("auto_receive", "자동 tacit receive"),
            ("auto_send", "자동 tacit send"),
            ("auto_share", "승인 없이 자동 공유"),
        )
    ]
    selected = [o for o in options if settings.get(o["value"], False)]
    element = {"type": "checkboxes", "action_id": "value", "options": options}
    if selected:
        element["initial_options"] = selected
    blocks.append(
        {
            "type": "input",
            "block_id": "flags",
            "optional": True,
            "label": {"type": "plain_text", "text": "자동화"},
            "element": element,
        }
    )
    return modal("Tacit 설정", "tacit_settings_save", {}, blocks)


def notify_product_once(relay, client):
    for item in relay.request("GET", "/v2/deliveries"):
        thread = item["thread"]
        channel = (
            thread["channel"]
            if thread
            else client.conversations_open(users=item["owner"])["channel"]["id"]
        )
        title, blocks = card_blocks(item["card"])
        if not thread:
            response = client.chat_postMessage(
                channel=channel,
                text=title,
                blocks=blocks,
                client_msg_id=str(
                    uuid.uuid5(uuid.NAMESPACE_URL, f"tacit-card:{item['id']}:{item['owner']}")
                ),
                unfurl_links=False,
                unfurl_media=False,
            )
            thread = {"channel": channel, "ts": response["ts"]}
        else:
            client.chat_update(channel=channel, ts=thread["ts"], text=title, blocks=blocks)
        if (
            item["kind"] in {"approval_requested", "user_question"}
            and item.get("actor") == item["owner"]
            and item.get("event_version") == item["card"]["version"]
            and item["card"]["owner"] == item["owner"]
            and item["card"]["state"] in {"approval_wait", "user_wait"}
        ):
            alert = (
                "공유할 내용을 확인해주세요."
                if item["kind"] == "approval_requested"
                else "Agent의 확인 질문에 답해주세요."
            )
            client.chat_postMessage(
                channel=channel,
                text=alert,
                blocks=plain_blocks(alert + "\n" + compact(item["card"]["text"], 80)),
                client_msg_id=str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"tacit-attention:{item['id']}:{item['owner']}:{item['card']['version']}:{item['kind']}",
                    )
                ),
                unfurl_links=False,
                unfurl_media=False,
            )
        # Keep internal activity in the store. Never mirror logs into Slack replies.
        relay.request(
            "POST",
            f"/v2/deliveries/{item['id']}",
            json={
                "owner": item["owner"],
                "seq": item["seq"],
                "channel": channel,
                "ts": thread["ts"],
            },
        )


def register_product(app, relay, team):
    def valid(body):
        return body.get("team", {}).get("id", body.get("team_id")) == team

    def home(client, owner):
        settings = relay.request("GET", "/v2/settings", params={"owner": owner})
        recent = relay.request("GET", "/v2/exchanges", params={"owner": owner})
        agents = relay.request("GET", "/v1/agents")
        seen = agents.get(owner)
        connection = "연결됨" if seen and time.time() - seen < 600 else "연결 대기"
        text = f"내 Agent · {connection}\n{settings['backend'] or '로컬 기본 실행기'} · {settings['model'] or '기본 모델'}\n자료 폴더: {settings['workspace'] or '로컬에서 지정한 폴더'}"
        blocks = plain_blocks(text)
        blocks.append(
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "action_id": "tacit_settings",
                        "text": {"type": "plain_text", "text": "설정"},
                    },
                    {
                        "type": "button",
                        "action_id": "tacit_refresh",
                        "text": {"type": "plain_text", "text": "새로고침"},
                    },
                ],
            }
        )
        for task in recent[:15]:
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "plain_text",
                        "text": compact(task.get("text") or "대화 기록", 100)
                        + "\n"
                        + {
                            "completed": "완료",
                            "cancelled": "공유 안 함",
                            "failed": "연결 확인 필요",
                            "approval_wait": "공유 확인 대기",
                            "user_wait": "답변 대기",
                        }.get(task["state"], "처리 중"),
                    },
                    "accessory": {
                        "type": "button",
                        "action_id": "tacit_result",
                        "text": {"type": "plain_text", "text": "답변"},
                        "value": task["id"],
                    },
                }
            )
        client.views_publish(user_id=owner, view={"type": "home", "blocks": blocks})

    @app.event("app_home_opened")
    def opened(event, body, client):
        if valid(body) and event.get("tab") == "home":
            home(client, event["user"])

    @app.action("tacit_refresh")
    def refresh(ack, body, client):
        ack()
        if valid(body):
            home(client, body["user"]["id"])

    @app.action("tacit_settings")
    def settings(ack, body, client):
        ack()
        if valid(body):
            current = relay.request("GET", "/v2/settings", params={"owner": body["user"]["id"]})
            client.views_open(trigger_id=body["trigger_id"], view=settings_view(current))

    @app.view("tacit_settings_save")
    def save(ack, body, view, client):
        if not valid(body):
            ack()
            return
        values = view["state"]["values"]
        settings = {
            key: (values[key]["value"].get("value") or "").strip()
            for key in ("workspace", "backend", "model")
        }
        if settings["backend"] not in {"", "kiro", "codex", "opencode", "bedrock", "openai"}:
            ack(
                response_action="errors",
                errors={"backend": "kiro, codex, opencode, bedrock, openai 중 선택해주세요."},
            )
            return
        if settings["backend"] == "opencode" and not settings["model"].startswith("openai/"):
            ack(response_action="errors", errors={"model": "openai/모델 이름 형식이 필요합니다."})
            return
        if settings["backend"] == "openai" and not settings["model"]:
            ack(response_action="errors", errors={"model": "openai 실행기는 모델 ID가 필요합니다."})
            return
        flags = {o["value"] for o in values["flags"]["value"].get("selected_options", [])}
        settings.update(
            {
                key: key in flags
                for key in ("web", "history", "auto_receive", "auto_send", "auto_share")
            }
        )
        ack()
        relay.request("POST", "/v2/settings", params={"owner": body["user"]["id"]}, json=settings)
        home(client, body["user"]["id"])

    @app.action(re.compile(r"^tacit_(history|result)$"))
    def history(ack, body, client):
        ack()
        if not valid(body):
            return
        task = relay.request(
            "GET",
            "/v2/exchanges/latest",
            params={"owner": body["user"]["id"], "exchange_id": body["actions"][0]["value"]},
        )
        text = (
            "조회 가능한 기록이 없습니다."
            if not task
            else task.get("result") or "아직 답변을 준비하고 있어요."
        )
        view = {
            "type": "modal",
            "title": {"type": "plain_text", "text": "답변"},
            "close": {"type": "plain_text", "text": "닫기"},
            "blocks": plain_blocks(text[:60000]),
        }
        client.views_open(trigger_id=body["trigger_id"], view=view)

    @app.action("tacit_local_audit")
    def local_audit(ack, body, client):
        ack()
        # Old audit buttons no longer publish logs to Slack.

    @app.action("tacit_review")
    def review(ack, body, client, respond):
        ack()
        if not valid(body):
            return
        meta = json.loads(body["actions"][0]["value"])
        owner = body["user"]["id"]
        task = relay.request(
            "GET", "/v2/exchanges/latest", params={"owner": owner, "exchange_id": meta["id"]}
        )
        if (
            not task
            or task["owner"] != owner
            or task["version"] != meta["version"]
            or task["state"] not in {"approval_wait", "user_wait"}
        ):
            respond("이미 처리된 요청이에요. 최신 카드를 확인해주세요.")
            return
        relay.request(
            "POST",
            "/v2/previews/" + meta["id"],
            params={"owner": owner, "version": meta["version"]},
        )

    @app.action(re.compile(r"^tacit_(approve|cancel|edit|user_answer)$"))
    def decision(ack, body, client, respond):
        ack()
        if not valid(body):
            return
        action = body["actions"][0]
        meta = json.loads(action["value"])
        owner = body["user"]["id"]
        task = relay.request(
            "GET", "/v2/exchanges/latest", params={"owner": owner, "exchange_id": meta["id"]}
        )
        kind = action["action_id"][6:]
        if (
            not task
            or task["owner"] != owner
            or task["version"] != meta["version"]
            or task["state"] != ("user_wait" if kind == "user_answer" else "approval_wait")
        ):
            respond("현재 작업 소유자·버전과 맞지 않습니다. 최신 승인 메시지를 사용해주세요.")
            return
        if kind in {"approve", "cancel"}:
            try:
                relay.request(
                    "POST",
                    f"/v2/decisions/{meta['id']}",
                    json={"owner": owner, "version": meta["version"], "action": kind},
                )
                # An ephemeral preview in the DM is replaced so its buttons disappear.
                respond(
                    response_type="ephemeral",
                    replace_original=bool(body.get("container", {}).get("is_ephemeral")),
                    text="승인했습니다." if kind == "approve" else "공유를 취소했습니다.",
                )
            except Exception:
                respond("처리 상태가 바뀌었거나 연결되지 않았습니다. Home에서 상태를 확인해주세요.")
            return
        # Slack sends the displayed preview in the clicked button or, for normal
        # messages, the payload; it only fills the editor and is never persisted.
        shown = meta.pop("text", "")
        preview = "".join(
            b.get("text", {}).get("text", "")
            for b in body.get("message", {}).get("blocks", [])
            if b.get("type") == "section"
        )
        draft = (
            (
                (shown if isinstance(shown, str) else "")
                or draft_text(
                    body.get("message", {}).get("blocks", []), meta["id"], meta["version"]
                )
                or preview.split("\n\n", 1)[-1]
            )
            if kind == "edit"
            else ""
        )
        meta["action"] = "edit" if kind == "edit" else "user_answer"
        blocks = [
            text_input(
                "answer",
                "최종 공유 내용" if kind == "edit" else "내 Agent에게 답변",
                draft[:3000],
                multiline=True,
            )
        ]
        if len(draft) > 3000:
            blocks = [
                text_input("answer", "최종 공유 내용 (앞부분)", draft[:3000], multiline=True),
                text_input("rest", "최종 공유 내용 (뒷부분)", draft[3000:5000], True, True),
            ]
        client.views_open(
            trigger_id=body["trigger_id"],
            view=modal(
                "공유 수정·승인" if kind == "edit" else "확인 질문 답변",
                "tacit_decision_save",
                meta,
                blocks,
                "승인·전달" if kind == "edit" else "답변",
            ),
        )

    @app.view("tacit_decision_save")
    def decision_save(ack, body, view, client):
        if not valid(body):
            ack()
            return
        meta = json.loads(view["private_metadata"])
        values = view["state"]["values"]
        text = (values["answer"]["value"].get("value") or "") + (
            values.get("rest", {}).get("value", {}).get("value") or ""
        )
        if not text.strip():
            ack(response_action="errors", errors={"answer": "내용을 입력해주세요."})
            return
        ack()
        try:
            action = meta["action"]
            if action == "edit":
                task = relay.request(
                    "GET",
                    "/v2/exchanges/latest",
                    params={"owner": body["user"]["id"], "exchange_id": meta["id"]},
                )
                if task and hashlib.sha256(text.encode()).hexdigest() == task.get("digest"):
                    action = "approve"
            relay.request(
                "POST",
                f"/v2/decisions/{meta['id']}",
                json={
                    "owner": body["user"]["id"],
                    "version": meta["version"],
                    "action": action,
                    "value": text if action != "approve" else None,
                },
            )
        except Exception:
            client.chat_postMessage(
                channel=body["user"]["id"],
                text="Tacit: 이미 처리되었거나 연결되지 않았습니다. Home에서 상태를 확인해주세요.",
            )

    @app.event("message")
    def automatic(event, body, client):
        if (
            not valid(body)
            or event.get("channel_type") != "im"
            or event.get("subtype")
            or event.get("bot_id")
            or not event.get("user")
            or event.get("thread_ts")
        ):
            return
        owner = event["user"]
        members = relay.request("GET", "/v1/agents")
        if owner not in members:
            return
        settings = relay.request("GET", "/v2/settings", params={"owner": owner})
        if not settings["auto_send"]:
            return
        # Only events Slack actually exposes to this installation are considered.
        peers = client.conversations_members(channel=event["channel"])["members"]
        recipients = [p for p in peers if p != owner and p in members]
        if len(recipients) != 1 or not event.get("text"):
            return
        if event.get("app_id") or event.get("client_msg_id", "").startswith("tacit"):
            return
        if event.get("client_msg_id"):
            original = relay.request(
                "GET",
                "/v2/exchanges/latest",
                params={"owner": owner, "exchange_id": event["client_msg_id"]},
            )
            if original and original["sender"] == owner:
                return
        relay.request(
            "POST",
            "/v2/exchanges",
            json={
                "request_key": f"{team}:event:{event['channel']}:{event['ts']}",
                "sender": owner,
                "recipient": recipients[0],
                "text": event["text"][:3000],
                "source": {"channel": event["channel"], "ts": event["ts"]},
            },
        )
