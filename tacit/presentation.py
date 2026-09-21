"""Small main cards, complete evidence in their threads and review modals."""

import json
import re

LABELS = {
    "created": "접수했어요",
    "original_sent": "메시지를 보냈어요",
    "approval_requested": "공유 내용을 준비했어요",
    "approve": "공유를 승인했어요",
    "edit": "수정한 내용을 승인했어요",
    "cancel": "공유하지 않았어요",
    "context": "공유한 배경",
    "question": "추가 질문",
    "answer": "답변",
    "result": "나를 위한 설명",
    "agent_result": "답변이 왔어요",
    "failed": "연결을 확인해주세요",
    "user_question": "확인이 필요해요",
    "user_answer": "답변을 받았어요",
}


def readable_mentions(text):
    return re.sub(r"<@[UW][A-Z0-9]+(?:\|([^>\n]+))?>", lambda m: m.group(1) or "사용자", text)


def compact(text, limit=280):
    lines = []
    for line in readable_mentions(text).splitlines():
        line = re.sub(r"^\s*(?:#{1,6}\s*|[-*■]\s+)", "", line).strip()
        line = line.replace("**", "").replace("`", "")
        if line:
            lines.append(line)
    value = "\n".join(lines)
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def plain_blocks(text, *, exact=False):
    if not exact:
        text = readable_mentions(text)
    return [
        {"type": "section", "text": {"type": "plain_text", "text": text[i : i + 2900]}}
        for i in range(0, len(text), 2900)
    ]


def card_snapshot(task, viewer, auto_receive=True):
    # Deliberately exclude private drafts, clarification answers and other fields.
    data = {
        k: task[k]
        for k in ("id", "text", "state", "owner", "version", "mode", "sender", "recipient")
    }
    data["viewer"] = viewer
    data["result"] = (
        task["result"]
        if task["state"] == "completed"
        and (task["mode"] == "agent" or (viewer == task["recipient"] and auto_receive))
        else ""
    )
    return data


def card_blocks(task):
    state, own = task["state"], task["owner"] == task["viewer"]
    detail = ""
    if state == "approval_wait":
        title = "공유할 내용을 확인해주세요" if own else "상대의 공유 확인을 기다려요"
    elif state == "user_wait":
        title = "한 가지만 알려주세요" if own else "상대의 답변을 기다려요"
    elif state == "cancelled":
        title = "추가 배경은 공유하지 않았어요"
    elif state == "failed":
        title = "연결을 확인해주세요"
        detail = "내 Agent의 연결·로그인을 확인한 뒤 다시 보내주세요."
    elif state == "completed":
        title = "답변이 왔어요" if task.get("result") else "전달을 마쳤어요"
        detail = compact(task.get("result", ""))
    elif state.startswith("interpret") and task["viewer"] == task["sender"]:
        title = "배경을 공유했어요"
    else:
        title = "필요한 내용을 확인하고 있어요"
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": title}},
        {
            "type": "context",
            "elements": [{"type": "plain_text", "text": compact(task["text"], 110) or "대화"}],
        },
    ]
    if detail:
        blocks += plain_blocks(detail)
    buttons = []
    if own and state in {"approval_wait", "user_wait"}:
        buttons.append(
            {
                "type": "button",
                "style": "primary",
                "action_id": "tacit_review",
                "text": {
                    "type": "plain_text",
                    "text": "내용 확인" if state == "approval_wait" else "답변하기",
                },
                "value": json.dumps({"id": task["id"], "version": task["version"]}),
            }
        )
        if state == "approval_wait":
            buttons.append(
                {
                    "type": "button",
                    "action_id": "tacit_cancel",
                    "text": {"type": "plain_text", "text": "공유 안 함"},
                    "value": json.dumps({"id": task["id"], "version": task["version"]}),
                }
            )
    if task.get("result") and len(task["result"]) > 280:
        buttons.append(
            {
                "type": "button",
                "action_id": "tacit_result",
                "text": {"type": "plain_text", "text": "답변 전체 보기"},
                "value": task["id"],
            }
        )
    if buttons:
        blocks.append({"type": "actions", "elements": buttons})
    return title, blocks


def draft_blocks(text, task_id, version, question=False):
    kind = "question" if question else "draft"
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "확인 질문" if question else "공유할 내용"},
        }
    ]
    for index, block in enumerate(plain_blocks(text, exact=True)):
        block["block_id"] = f"tacit_{kind}_{task_id}_{version}_{index}"
        blocks.append(block)
    return blocks


def draft_text(blocks, task_id, version, question=False):
    prefix = f"tacit_{'question' if question else 'draft'}_{task_id}_{version}_"
    return "".join(
        b.get("text", {}).get("text", "")
        for b in blocks
        if b.get("type") == "section" and b.get("block_id", "").startswith(prefix)
    )
