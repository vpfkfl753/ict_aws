import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tacit.relay import create_app
from tacit.slack import notify_once, parse_send, register_handlers
from tacit.store import Store
from tacit.worker import Worker

ALICE = "UAAA"
BOB = "UBBB"
MEMBERS = {ALICE: "a" * 32, BOB: "b" * 32}
BRIDGE = "c" * 32


class Client:
    def __init__(self, app, token):
        self.http = TestClient(app, headers={"Authorization": f"Bearer {token}"})

    def request(self, method, path, **kwargs):
        response = self.http.request(method, path, **kwargs)
        response.raise_for_status()
        return response.json()


@pytest.fixture
def setup(tmp_path):
    store = Store(str(tmp_path / "relay.db"))
    app = create_app(store, MEMBERS, BRIDGE)
    return store, app, Client(app, BRIDGE), Client(app, MEMBERS[ALICE]), Client(app, MEMBERS[BOB])


class FakeSlack:
    def __init__(self):
        self.messages = []

    def send(self, exchange):
        self.messages.append(exchange["text"])
        return {"channel": "D123", "ts": "123.456"}

    def conversations_open(self, users):
        return {"channel": {"id": f"D-{users}"}}

    def chat_postMessage(self, **kwargs):
        self.messages.append(kwargs)


class Model:
    def __init__(self, response):
        self.response = response
        self.prompts = []

    def run(self, prompt):
        self.prompts.append(prompt)
        return self.response


def submit(bridge, key="test", sender=ALICE, recipient=BOB):
    return bridge.request(
        "POST",
        "/v1/exchanges",
        json={
            "request_key": key,
            "sender": sender,
            "recipient": recipient,
            "text": "이번 결과 baseline이랑 비교해봤어?",
        },
    )


def test_two_agents_original_message_context_and_private_notification(setup, tmp_path):
    store, app, bridge, alice, bob = setup
    exchange = submit(bridge)
    slack = FakeSlack()
    alice_model = Model("Baseline B17, preprocessing p2. Evidence: experiment.md")
    bob_model = Model("Local B24 uses p3; rerun with p2. Evidence: results.md")
    sender = Worker(alice, alice_model, slack, tmp_path / "alice")
    receiver = Worker(bob, bob_model, slack, tmp_path / "bob")
    assert not receiver.once()
    assert sender.once()
    assert slack.messages == [exchange["text"]]
    assert receiver.once()
    result = store.latest(BOB)
    assert result["state"] == "completed"
    assert result["source"] == {"channel": "D123", "ts": "123.456"}
    assert "Baseline B17" in bob_model.prompts[0]
    assert result["result"] == bob_model.response
    assert not sender.once()
    assert not receiver.once()
    notify_once(bridge, slack)
    assert slack.messages[-1]["channel"] == "D-UBBB"
    assert "Local B24" in json.dumps(slack.messages[-1])
    assert store.notifications() == []
    assert store.latest(ALICE, exchange["id"]) is None
    # Reversing the direction uses the same path, not a hard-coded sender role.
    reverse = submit(bridge, key="reverse", sender=BOB, recipient=ALICE)
    assert receiver.once()
    assert sender.once()
    assert store.latest(ALICE, reverse["id"])["state"] == "completed"


def test_duplicate_command_and_ownership(setup):
    store, app, bridge, alice, bob = setup
    exchange = submit(bridge)
    assert submit(bridge)["id"] == exchange["id"]
    work = alice.request("POST", "/v1/work/claim")
    assert alice.request("POST", "/v1/work/claim") is None
    response = bob.http.post(
        f"/v1/work/{exchange['id']}",
        json={
            "lease": work["lease"],
            "action": "error",
            "value": "forged",
        },
    )
    assert response.status_code == 409
    assert alice.http.get("/v1/notifications").status_code == 403
    assert bridge.http.post("/v1/work/claim").status_code == 403
    assert TestClient(app).post("/v1/work/claim").status_code == 401


def test_expired_lease_and_required_source(setup):
    store, app, bridge, alice, bob = setup
    exchange = submit(bridge)
    work = alice.request("POST", "/v1/work/claim")
    endpoint = f"/v1/work/{exchange['id']}"
    assert (
        alice.http.post(
            endpoint,
            json={
                "lease": work["lease"],
                "action": "context",
                "value": "no original yet",
            },
        ).status_code
        == 409
    )
    with store.db() as db:
        db.execute("UPDATE exchanges SET lease_until = ?", (time.time() - 1,))
    reclaimed = alice.request("POST", "/v1/work/claim")
    assert work["lease"] != reclaimed["lease"]
    assert (
        alice.http.post(
            endpoint,
            json={
                "lease": work["lease"],
                "action": "error",
                "value": "stale completion",
            },
        ).status_code
        == 409
    )


def test_model_failure_is_visible_without_forwarding_private_exception(setup, tmp_path):
    store, app, bridge, alice, bob = setup
    submit(bridge)

    class FailingModel:
        def run(self, prompt):
            raise RuntimeError("PRIVATE source text and token")

    Worker(alice, FailingModel(), FakeSlack(), tmp_path / "a").once()
    result = store.latest(BOB)
    assert result["state"] == "prepare_failed"
    assert "PRIVATE" not in result["error"]
    assert len(store.notifications()) == 1


def test_sender_uses_persisted_source_after_reclaim(setup, tmp_path):
    store, app, bridge, alice, bob = setup
    exchange = submit(bridge)
    work = alice.request("POST", "/v1/work/claim")
    alice.request(
        "POST",
        f"/v1/work/{exchange['id']}",
        json={
            "lease": work["lease"],
            "action": "source",
            "value": {"channel": "D1", "ts": "1.2"},
        },
    )
    with store.db() as db:
        db.execute("UPDATE exchanges SET lease_until = 0")
    slack = FakeSlack()
    Worker(alice, Model("context"), slack, tmp_path / "a").once()
    assert slack.messages == []
    assert store.latest(BOB)["state"] == "interpret_pending"


def test_slash_ack_happens_before_network_and_uses_sender_identity():
    order = []

    class FakeApp:
        def __init__(self):
            self.handlers = {}

        def command(self, name):
            def register(handler):
                self.handlers[name] = handler
                return handler

            return register

    class Relay:
        def request(self, method, path, **kwargs):
            assert order == ["ack"]
            assert kwargs["json"]["sender"] == ALICE
            order.append("request")
            return {"id": "test"}

    app = FakeApp()
    register_handlers(app, Relay(), "T1")
    app.handlers["/tacit-send"](
        ack=lambda: order.append("ack"),
        command={"team_id": "T1", "trigger_id": "1", "user_id": ALICE, "text": "<@UBBB> hi"},
        respond=lambda text: order.append("respond"),
    )
    assert order == ["ack", "request", "respond"]


def test_parse_send():
    assert parse_send("<@U123|name> hello\nworld") == ("U123", "hello\nworld")
    with pytest.raises(ValueError):
        parse_send("@name hello")


def test_codex_adapter_uses_real_cli_contract_and_separates_credentials(monkeypatch, tmp_path):
    from tacit.provider import CodexProvider

    monkeypatch.setenv("SLACK_USER_TOKEN", "secret")
    monkeypatch.setenv("TACIT_AGENT_TOKEN", "secret")

    def run(command, **kwargs):
        assert command[:2] == ["codex", "exec"]
        assert "read-only" in command
        assert "--ignore-user-config" in command
        assert "SLACK_USER_TOKEN" not in kwargs["env"]
        assert "TACIT_AGENT_TOKEN" not in kwargs["env"]
        assert kwargs["input"].startswith("probe")
        assert '"local_sources"' in kwargs["input"]
        Path(command[command.index("--output-last-message") + 1]).write_text("response")
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr("tacit.provider.subprocess.run", run)
    assert CodexProvider(tmp_path).run("probe") == "response"


@pytest.mark.parametrize("backends", [("kiro", "opencode"), ("opencode", "kiro")])
def test_mixed_backends_complete_exchange(setup, tmp_path, monkeypatch, backends):
    from types import SimpleNamespace

    from tacit.provider import create_provider

    used = []

    class Runtime:
        def __init__(self, backend, workspace, **kwargs):
            self.backend = backend

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def prompt(self, prompt):
            used.append(self.backend)
            if len(used) == 2:
                assert "B17/p2" in prompt
            return SimpleNamespace(text="B17/p2 versus B24/p3", stop_reason="end_turn")

    monkeypatch.setattr("tacit.provider.AgentRuntime", Runtime)
    store, app, bridge, alice, bob = setup
    exchange = submit(bridge)
    slack = FakeSlack()
    for client, backend, name in zip((alice, bob), backends, ("alice", "bob"), strict=True):
        provider = create_provider(
            tmp_path, backend, "openai/test" if backend == "opencode" else None
        )
        assert Worker(client, provider, slack, tmp_path / name).once()
    assert used == list(backends)
    assert store.latest(BOB, exchange["id"])["state"] == "completed"
    assert slack.messages == [exchange["text"]]
