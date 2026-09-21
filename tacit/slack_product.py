"""Slack Home, settings, scoped audit, approval and clarification controls."""

import json
import re
import uuid

from tacit.product_worker import plain_blocks


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
            "실행기: kiro / codex / opencode (빈칸: 로컬 기본)",
            settings["backend"],
            True,
        ),
        text_input("model", "모델 ID (선택)", settings["model"], True),
    ]
    options = [
        {"text": {"type": "plain_text", "text": label}, "value": key}
        for key, label in (
            ("web", "공개 검색어 파일로 웹 검색"),
            ("auto_receive", "수신 설명 자동 표시"),
            ("auto_send", "참여 DM에서 맥락 자동 준비"),
        )
    ]
    selected = [o for o in options if settings[o["value"]]]
    element = {"type": "checkboxes", "action_id": "value", "options": options}
    if selected:
        element["initial_options"] = selected
    blocks.append(
        {
            "type": "input",
            "block_id": "flags",
            "optional": True,
            "label": {"type": "plain_text", "text": "자동화 (공유 승인은 항상 필요)"},
            "element": element,
        }
    )
    return modal("Tacit 설정", "tacit_settings_save", {}, blocks)


def notify_product_once(relay, client):
    labels = {
        "created": "작업 접수",
        "original_sent": "원문 전송",
        "approval_requested": "공유 승인 대기",
        "approve": "공유 승인",
        "edit": "수정본 공유 승인",
        "cancel": "공유 취소",
        "context": "승인한 맥락",
        "question": "Agent 질문",
        "answer": "Agent 답변",
        "result": "나를 위한 설명",
        "agent_result": "질문에 대한 승인된 Agent 답변",
        "failed": "작업 실패: 로컬 워커를 확인해주세요",
    }
    for item in relay.request("GET", "/v2/deliveries"):
        thread = item["thread"]
        channel = (
            thread["channel"]
            if thread
            else client.conversations_open(users=item["owner"])["channel"]["id"]
        )
        text = labels.get(item["kind"], item["kind"]) + " · " + item["id"] + "\n" + item["text"]
        kwargs = {"thread_ts": thread["ts"]} if thread else {}
        response = client.chat_postMessage(
            channel=channel,
            text="Tacit 작업 " + item["id"],
            blocks=plain_blocks(text),
            client_msg_id=str(
                uuid.uuid5(uuid.NAMESPACE_URL, f"tacit:{item['id']}:{item['owner']}:{item['seq']}")
            ),
            unfurl_links=False,
            unfurl_media=False,
            **kwargs,
        )
        relay.request(
            "POST",
            f"/v2/deliveries/{item['id']}",
            json={
                "owner": item["owner"],
                "seq": item["seq"],
                "channel": channel,
                "ts": response["ts"],
            },
        )


def register_product(app, relay, team):
    def valid(body):
        return body.get("team", {}).get("id", body.get("team_id")) == team

    def home(client, owner):
        settings = relay.request("GET", "/v2/settings", params={"owner": owner})
        recent = relay.request("GET", "/v2/exchanges", params={"owner": owner})
        agents = relay.request("GET", "/v1/agents")
        text = f"Tacit · 내 설정과 작업\n실행기: {settings['backend']} / {settings['model'] or '기본 모델'}\n자료 폴더: {settings['workspace'] or '로컬 워커 기본 폴더'}\n웹 검색: {settings['web']} · 자동 수신: {settings['auto_receive']} · 자동 준비: {settings['auto_send']}\n최근 연결: {agents.get(owner) or '대기'}\n자료 원문과 승인 전 초안은 로컬에 보관합니다."
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
                        "text": f"{task['id']}\n{task['sender']} → {task['recipient']} · {task['state']}",
                    },
                    "accessory": {
                        "type": "button",
                        "action_id": "tacit_history",
                        "text": {"type": "plain_text", "text": "기록"},
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
        if settings["backend"] not in {"", "kiro", "codex", "opencode"}:
            ack(
                response_action="errors",
                errors={"backend": "kiro, codex, opencode 중 선택해주세요."},
            )
            return
        if settings["backend"] == "opencode" and not settings["model"].startswith("openai/"):
            ack(response_action="errors", errors={"model": "openai/모델 이름 형식이 필요합니다."})
            return
        flags = {o["value"] for o in values["flags"]["value"].get("selected_options", [])}
        settings.update({key: key in flags for key in ("web", "auto_receive", "auto_send")})
        ack()
        relay.request("POST", "/v2/settings", params={"owner": body["user"]["id"]}, json=settings)
        home(client, body["user"]["id"])

    @app.action("tacit_history")
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
            else f"{task['id']} · {task['state']}\n"
            + "\n\n".join(
                f"{e['seq']}. {e['kind']} · {e['actor']}\n{e['text']}" for e in task["events"]
            )
        )
        view = {
            "type": "modal",
            "title": {"type": "plain_text", "text": "작업 기록"},
            "close": {"type": "plain_text", "text": "닫기"},
            "blocks": plain_blocks(text[:60000]),
        }
        if task:
            view["blocks"].append(
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "action_id": "tacit_local_audit",
                            "text": {"type": "plain_text", "text": "내 로컬 접근·도구 기록 받기"},
                            "value": task["id"],
                        }
                    ],
                }
            )
        client.views_open(trigger_id=body["trigger_id"], view=view)

    @app.action("tacit_local_audit")
    def local_audit(ack, body, client):
        ack()
        if valid(body):
            relay.request(
                "POST",
                "/v2/audit/" + body["actions"][0]["value"],
                params={"owner": body["user"]["id"]},
            )
            client.chat_postMessage(
                channel=body["user"]["id"],
                text="내 워커에 로컬 감사 기록을 요청했습니다. 워커가 연결되면 이 대화에 표시합니다.",
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
        if not task or task["owner"] != owner or task["version"] != meta["version"]:
            respond("현재 작업 소유자·버전과 맞지 않습니다. 최신 승인 메시지를 사용해주세요.")
            return
        kind = action["action_id"][6:]
        if kind in {"approve", "cancel"}:
            try:
                relay.request(
                    "POST",
                    f"/v2/decisions/{meta['id']}",
                    json={"owner": owner, "version": meta["version"], "action": kind},
                )
                respond(
                    response_type="ephemeral",
                    replace_original=False,
                    text="승인했습니다." if kind == "approve" else "공유를 취소했습니다.",
                )
            except Exception:
                respond("처리 상태가 바뀌었거나 연결되지 않았습니다. Home에서 상태를 확인해주세요.")
            return
        # Slack sends the displayed preview in the interaction payload; it is
        # transiently used to fill the editor and never persisted before submit.
        preview = "".join(
            b.get("text", {}).get("text", "")
            for b in body.get("message", {}).get("blocks", [])
            if b.get("type") == "section"
        )
        draft = preview.split("\n\n", 1)[-1] if kind == "edit" else ""
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
            relay.request(
                "POST",
                f"/v2/decisions/{meta['id']}",
                json={
                    "owner": body["user"]["id"],
                    "version": meta["version"],
                    "action": meta["action"],
                    "value": text,
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
