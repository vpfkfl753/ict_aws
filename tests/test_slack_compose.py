import httpx
import pytest
from slack_sdk.errors import SlackApiError

from tacit.slack import register_handlers
from tacit.slack_compose import register_compose


class App:
    def __init__(self):
        self.handlers = {}

    def command(self, name):
        def register(handler):
            self.handlers[name] = handler
            return handler

        return register

    view = command


class Slack:
    def __init__(self):
        self.opened = []
        self.updated = []

    def views_open(self, **kwargs):
        self.opened.append(kwargs)

    def views_update(self, **kwargs):
        self.updated.append(kwargs)


def submission(text="hello", recipient="UB", team="T1"):
    return {"team": {"id": team}, "user": {"id": "UA"}}, {
        "id": "V1",
        "private_metadata": "unique-request",
        "state": {
            "values": {
                "recipient": {"user": {"selected_user": recipient}},
                "message": {"text": {"value": text}},
            }
        },
    }


def test_bare_or_unresolved_command_opens_modal_without_dispatching():
    app, slack = App(), Slack()
    register_handlers(app, None, "T1")
    for text in ("", "@사용자 B 안녕", "이번 결과 비교했어?"):
        acknowledgements = []
        app.handlers["/tacit-send"](
            ack=lambda: acknowledgements.append(True),
            command={"team_id": "T1", "user_id": "UA", "trigger_id": "trigger", "text": text},
            respond=lambda response: None,
            client=slack,
        )
        assert acknowledgements == [True]
        view = slack.opened[-1]["view"]
        assert view["callback_id"] == "tacit_compose"
        assert view["blocks"][1]["element"].get("initial_value", "") == text


def test_submission_acks_before_network_and_preserves_idempotency():
    app, slack, events = App(), Slack(), []

    class Relay:
        def request(self, method, path, **kwargs):
            assert events == ["ack"]
            assert kwargs["json"] == {
                "request_key": "T1:modal:unique-request",
                "sender": "UA",
                "recipient": "UB",
                "text": "hello",
            }
            return {"id": "exchange"}

    register_compose(app, Relay(), "T1")
    body, view = submission()
    app.handlers["tacit_compose"](
        ack=lambda **kwargs: events.append("ack"),
        body=body,
        view=view,
        client=slack,
    )
    assert slack.updated[0]["view_id"] == "V1"
    assert "exchange" in slack.updated[0]["view"]["blocks"][0]["text"]["text"]


def test_invalid_submission_does_not_dispatch():
    app, slack = App(), Slack()
    register_compose(app, None, "T1")
    for text, recipient, team in (("", "UB", "T1"), ("hi", "UA", "T1"), ("hi", "UB", "T2")):
        body, view = submission(text, recipient, team)
        replies = []
        app.handlers["tacit_compose"](
            ack=lambda **kwargs: replies.append(kwargs), body=body, view=view, client=slack
        )
        assert replies[0]["response_action"] == "errors"
    assert slack.updated == []


def test_failed_submission_restores_inputs_and_request_key():
    app, slack = App(), Slack()

    class Relay:
        def request(self, *args, **kwargs):
            raise httpx.ConnectError("offline")

    register_compose(app, Relay(), "T1")
    body, view = submission()
    app.handlers["tacit_compose"](ack=lambda **kwargs: None, body=body, view=view, client=slack)
    restored = slack.updated[0]["view"]
    assert restored["private_metadata"] == "unique-request"
    assert restored["blocks"][-1]["element"]["initial_value"] == "hello"


@pytest.mark.parametrize("failure", ["api", "channel", "bot", "malformed", "next_page", "empty"])
def test_v2_unknown_conversation_never_dispatches(failure):
    app, replies, dispatched = App(), [], []

    class Members:
        def conversations_members(self, **kwargs):
            if failure == "malformed":
                return {}
            if failure == "empty":
                return {"members": []}
            if failure == "next_page" and "cursor" not in kwargs:
                return {"members": ["UA"], "response_metadata": {"next_cursor": "next"}}
            raise RuntimeError("lookup failed")

    class Relay:
        def request(self, *args, **kwargs):
            dispatched.append(kwargs)
            return {"id": "unexpected"}

    register_handlers(app, Relay(), "T1", protocol=2, bot_user=None if failure == "bot" else "UBOT")
    app.handlers["/tacit-send"](
        ack=lambda: None,
        command={
            "team_id": "T1",
            "user_id": "UA",
            "trigger_id": "trigger",
            "channel_id": None if failure == "channel" else "D1",
            "text": "<@UB> hello",
        },
        respond=replies.append,
        client=Members(),
    )
    assert dispatched == []
    assert "전송을 중단" in replies[0]


@pytest.mark.parametrize(
    ("channel", "error", "expected"),
    [
        ("D1", "channel_not_found", "dm"),
        ("D1", "ratelimited", None),
        ("C1", "not_in_channel", None),
    ],
)
def test_v2_human_dm_hidden_from_bot_is_dm_mode(channel, error, expected):
    app, sent, replies = App(), [], []

    class Members:
        def conversations_members(self, **kwargs):
            raise SlackApiError("lookup failed", {"ok": False, "error": error})

    class Relay:
        def request(self, method, path, **kwargs):
            sent.append(kwargs["json"])
            return {"id": "accepted"}

    register_handlers(app, Relay(), "T1", protocol=2, bot_user="UBOT")
    app.handlers["/tacit-send"](
        ack=lambda: None,
        command={
            "team_id": "T1",
            "user_id": "UA",
            "trigger_id": "trigger",
            "channel_id": channel,
            "text": "<@UB> hello",
        },
        respond=replies.append,
        client=Members(),
    )
    assert [s["mode"] for s in sent] == ([expected] if expected else [])
    assert expected or "전송을 중단" in replies[0]


@pytest.mark.parametrize("mode", ["agent", "dm", "broadcast", "paginated"])
def test_v2_confirmed_conversation_preserves_delivery_mode(mode):
    app, sent = App(), []

    class Members:
        def conversations_members(self, **kwargs):
            assert mode != "broadcast"
            if mode == "paginated" and "cursor" not in kwargs:
                return {"members": ["UA"], "response_metadata": {"next_cursor": "next"}}
            return {"members": ["UA", "UBOT" if mode != "dm" else "UB"]}

    class Relay:
        def request(self, method, path, **kwargs):
            sent.append(kwargs["json"])
            return {"id": "accepted"}

    register_handlers(app, Relay(), "T1", protocol=2, bot_user="UBOT")
    app.handlers["/tacit-send"](
        ack=lambda: None,
        command={
            "team_id": "T1",
            "user_id": "UA",
            "trigger_id": "trigger",
            "channel_id": "D1",
            "text": "@broadcast hello" if mode == "broadcast" else "<@UB> hello",
        },
        respond=lambda response: None,
        client=Members(),
    )
    assert len(sent) == 1
    assert sent[0]["mode"] == ("dm" if mode == "dm" else "agent")
