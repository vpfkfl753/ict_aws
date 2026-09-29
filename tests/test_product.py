import hashlib
import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from tacit.product_worker import ProductWorker
from tacit.relay import create_app
from tacit.retrieval import Corpus, Researcher, public_get
from tacit.slack_product import notify_product_once
from tacit.store import Conflict, Store
from tacit.workflow import Workflow

MEMBERS = {"UA": "a" * 32, "UB": "b" * 32, "UC": "c" * 32}
BRIDGE = "x" * 32


class Client:
    def __init__(self, app, token):
        self.http = TestClient(app, headers={"Authorization": "Bearer " + token})

    def request(self, method, path, **kwargs):
        response = self.http.request(method, path, **kwargs)
        response.raise_for_status()
        return response.json()


@pytest.fixture
def system(tmp_path):
    store = Store(str(tmp_path / "relay.sqlite"))
    app = create_app(store, MEMBERS, BRIDGE)
    flow = Workflow(store)
    return store, flow, Client(app, BRIDGE), {u: Client(app, t) for u, t in MEMBERS.items()}


def create(flow, key="one", mode="dm"):
    return flow.create(key, "UA", "UB", "baseline 조건 알려줘", mode)


def draft(flow, task, text="private B17 p2"):
    work = flow.claim("UA")
    if work["mode"] == "dm":
        flow.change(work["id"], "UA", "source", {"channel": "D1", "ts": "1"}, lease=work["lease"])
    flow.change(
        work["id"], "UA", "approval", hashlib.sha256(text.encode()).hexdigest(), lease=work["lease"]
    )
    return flow.get("UA", task["id"])


def approve(flow, task, text="private B17 p2", edited=None):
    current = flow.get("UA", task["id"])
    flow.change(
        task["id"], "UA", "edit" if edited else "approve", edited, version=current["version"]
    )
    work = flow.claim("UA")
    flow.change(work["id"], "UA", "publish", edited or text, lease=work["lease"])


def test_private_draft_never_stored_before_approval_and_edit_is_exact(system):
    store, flow, bridge, agents = system
    task = draft(flow, create(flow))
    assert task["context"] == ""
    with store.db() as db:
        raw = db.execute("SELECT body FROM workflows").fetchone()[0]
    assert "private B17 p2" not in raw
    assert flow.claim("UB") is None
    flow.change(task["id"], "UA", "edit", "Approved p2 only", version=1)
    work = flow.claim("UA")
    with pytest.raises(Conflict):
        flow.change(task["id"], "UA", "publish", "private B17 p2", lease=work["lease"])
    flow.change(task["id"], "UA", "publish", "Approved p2 only", lease=work["lease"])
    assert flow.get("UB")["context"] == "Approved p2 only"


def test_approval_actor_version_cancellation_and_draft_digest(system):
    _, flow, _, _ = system
    task = draft(flow, create(flow))
    for actor, version in [("UB", 1), ("UA", 0)]:
        with pytest.raises(Conflict):
            flow.change(task["id"], actor, "approve", version=version)
    flow.change(task["id"], "UA", "approve", version=1)
    with pytest.raises(Conflict):
        flow.change(task["id"], "UA", "approve", version=1)
    work = flow.claim("UA")
    with pytest.raises(Conflict):
        flow.change(task["id"], "UA", "publish", "mutated draft", lease=work["lease"])
    second = draft(flow, create(flow, "two"))
    flow.change(second["id"], "UA", "cancel", version=1)
    assert flow.get("UA", second["id"])["state"] == "cancelled"


def test_no_user_decisions_via_agent_api_or_lease_forgery(system):
    _, flow, bridge, agents = system
    task = draft(flow, create(flow))
    assert (
        agents["UA"]
        .http.post(
            "/v2/decisions/" + task["id"], json={"owner": "UA", "version": 1, "action": "approve"}
        )
        .status_code
        == 403
    )
    assert (
        agents["UA"]
        .http.post(
            "/v2/work/" + task["id"], json={"lease": "bad", "action": "approve", "value": "x"}
        )
        .status_code
        == 422
    )
    assert agents["UC"].http.get("/v2/work/" + task["id"]).status_code == 404
    assert (
        bridge.request(
            "GET", "/v2/exchanges/latest", params={"owner": "UC", "exchange_id": task["id"]}
        )
        is None
    )
    assert bridge.http.post("/v2/audit/" + task["id"], params={"owner": "UC"}).status_code == 409


def test_two_round_limit_auto_reply_only_quotes_and_private_result(system):
    _, flow, _, _ = system
    task = draft(flow, create(flow))
    approve(flow, task)
    for _ in range(2):
        work = flow.claim("UB")
        flow.change(task["id"], "UB", "question", "Which preprocessing?", lease=work["lease"])
        reply = flow.claim("UA")
        with pytest.raises(Conflict):
            flow.change(task["id"], "UA", "answer", "NEW PRIVATE FACT", lease=reply["lease"])
        flow.change(task["id"], "UA", "answer", "B17 p2", lease=reply["lease"])
    work = flow.claim("UB")
    with pytest.raises(Conflict):
        flow.change(task["id"], "UB", "question", "third?", lease=work["lease"])
    flow.change(task["id"], "UB", "ask_user", "Need your condition", lease=work["lease"])
    flow.change(task["id"], "UB", "user_answer", "MY PRIVATE CONDITION", version=2)
    work = flow.claim("UB")
    flow.change(task["id"], "UB", "result", "PRIVATE RECIPIENT EXPLANATION", lease=work["lease"])
    assert flow.get("UB")["result"] == "PRIVATE RECIPIENT EXPLANATION"
    assert "PRIVATE" not in json.dumps(flow.get("UA"))
    assert flow.claim("UB") is None


def test_new_reply_needs_second_approval(system):
    _, flow, _, _ = system
    task = draft(flow, create(flow))
    approve(flow, task)
    work = flow.claim("UB")
    flow.change(task["id"], "UB", "question", "new condition?", lease=work["lease"])
    reply = flow.claim("UA")
    new = "NEW PRIVATE CONDITION"
    flow.change(
        task["id"], "UA", "approval", hashlib.sha256(new.encode()).hexdigest(), lease=reply["lease"]
    )
    assert flow.get("UA")["version"] == 2
    assert new not in json.dumps(flow.get("UB"))
    assert flow.claim("UB") is None
    approve(flow, task, text=new)
    assert flow.get("UB")["answer"] == new


def test_user_private_clarification_cannot_start_another_peer_question(system):
    _, flow, _, _ = system
    task = draft(flow, create(flow))
    approve(flow, task)
    work = flow.claim("UB")
    flow.change(task["id"], "UB", "ask_user", "condition?", lease=work["lease"])
    flow.change(task["id"], "UB", "user_answer", "private clarification", version=2)
    work = flow.claim("UB")
    with pytest.raises(Conflict):
        flow.change(task["id"], "UB", "question", "followup", lease=work["lease"])
    assert flow.claim("UA") is None
    assert "private clarification" not in json.dumps(flow.get("UA"))


def test_agent_only_answer_returns_to_requester_only_after_responder_approval(system):
    _, flow, _, _ = system
    task = draft(flow, create(flow, mode="agent"))
    approve(flow, task)
    response = flow.claim("UB")
    with pytest.raises(Conflict):
        flow.change(task["id"], "UB", "result", "LOCAL RESPONSE", lease=response["lease"])
    flow.change(
        task["id"],
        "UB",
        "approval",
        hashlib.sha256(b"LOCAL RESPONSE").hexdigest(),
        lease=response["lease"],
    )
    assert "LOCAL RESPONSE" not in json.dumps(flow.get("UA"))
    with pytest.raises(Conflict):
        flow.change(task["id"], "UA", "approve", version=2)
    flow.change(task["id"], "UB", "approve", version=2)
    publishing = flow.claim("UB")
    flow.change(task["id"], "UB", "publish", "LOCAL RESPONSE", lease=publishing["lease"])
    assert flow.get("UA")["state"] == "completed"
    assert flow.get("UA")["result"] == "LOCAL RESPONSE"
    assert flow.get("UA")["events"][-1]["kind"] == "agent_result"


def test_expired_lease_reclaims_and_pending_user_does_not_block_other_work(system):
    store, flow, _, _ = system
    task = draft(flow, create(flow))
    other = create(flow, "other")
    work = flow.claim("UA")
    assert work["id"] == other["id"]
    with store.db() as db:
        work["lease_until"] = time.time() - 1
        flow.save(db, work)
    replacement = flow.claim("UA")
    assert replacement["lease"] != work["lease"]
    with pytest.raises(Conflict):
        flow.change(other["id"], "UA", "source", {"channel": "D1", "ts": "1"}, lease=work["lease"])
    assert flow.get("UA", task["id"])["state"] == "approval_wait"


def test_agent_only_broadcast_is_idempotent_and_scoped(system):
    _, flow, bridge, _ = system
    payload = dict(
        request_key="broadcast", sender="UA", recipient="broadcast", text="question", mode="agent"
    )
    result = bridge.request("POST", "/v2/exchanges", json=payload)
    assert len(result["exchanges"]) == 2
    assert bridge.request("POST", "/v2/exchanges", json=payload) == result
    assert len(flow.recent("UB")) == 1
    work = flow.claim("UA")
    flow.change(
        work["id"], "UA", "approval", hashlib.sha256(b"context").hexdigest(), lease=work["lease"]
    )
    assert flow.get("UA", work["id"])["source"] is None


def test_settings_are_own_user_and_cannot_expand_roots(system, tmp_path, monkeypatch):
    _, flow, _, agents = system
    workspace = tmp_path / "allowed"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    model = Model(workspace)
    worker = ProductWorker(
        agents["UA"],
        model,
        Slack(),
        tmp_path / "state",
        owner="UA",
        allowed_roots=[workspace],
        bot_user="UBOT",
        wait_seconds=0,
    )
    flow.settings("UA", {"workspace": str(outside)})
    with pytest.raises(ValueError, match="outside"):
        worker.configure()
    assert agents["UB"].request("GET", "/v2/worker/settings")["workspace"] == ""
    assert (
        agents["UA"].http.post("/v2/settings", params={"owner": "UB"}, json={}).status_code == 403
    )
    with pytest.raises(Conflict):
        flow.settings("UA", {"web": "false"})


class Slack:
    def __init__(self):
        self.client = self
        self.messages = []
        self.originals = []
        self.updates = []

    def send(self, task):
        self.originals.append(task["text"])
        return {"channel": "D1", "ts": "1"}

    def conversations_open(self, users):
        return {"channel": {"id": "D" + users}}

    def chat_postMessage(self, **kwargs):
        self.messages.append(kwargs)
        return {"channel": kwargs["channel"], "ts": str(len(self.messages))}

    def chat_update(self, **kwargs):
        self.updates.append(kwargs)
        return {"channel": kwargs["channel"], "ts": kwargs["ts"]}

    def chat_postEphemeral(self, **kwargs):
        self.messages.append({**kwargs, "ephemeral": True})
        return {"message_ts": str(len(self.messages))}


class Model:
    backend = "kiro"
    model = None

    def __init__(self, workspace):
        self.workspace = workspace

    def run_evidence(self, prompt, sources):
        if prompt.startswith("Choose further"):
            return '{"files":["evidence.md"]}'
        if "recipient agent" in prompt:
            return '{"action":"result","text":"내 조건과 B17 p2가 다릅니다."}'
        return "PRIVATE_DRAFT: B17 p2. evidence.md"


def test_product_workers_use_local_previews_and_real_transition_contract(
    system, tmp_path, monkeypatch
):
    store, flow, bridge, agents = system
    workspace = tmp_path / "corpus"
    workspace.mkdir()
    (workspace / "evidence.md").write_text("B17 p2 local condition")
    monkeypatch.setattr("tacit.product_worker.create_provider", lambda *args: Model(workspace))
    slack = Slack()
    workers = {
        u: ProductWorker(
            agents[u],
            Model(workspace),
            slack,
            tmp_path / u,
            owner=u,
            allowed_roots=[workspace],
            bot_user="UBOT",
            wait_seconds=0,
        )
        for u in ("UA", "UB")
    }
    task = create(flow)
    notify_product_once(bridge, slack)
    assert workers["UA"].once()
    assert slack.originals == [task["text"]]
    with store.db() as db:
        assert "PRIVATE_DRAFT" not in db.execute("SELECT body FROM workflows").fetchone()[0]
    assert "PRIVATE_DRAFT" in json.dumps(slack.messages)
    assert not workers["UB"].once()
    bridge.request(
        "POST",
        "/v2/decisions/" + task["id"],
        json={"owner": "UA", "version": 1, "action": "approve"},
    )
    assert workers["UA"].once()
    assert workers["UB"].once()
    assert flow.get("UB")["state"] == "completed"
    for path in (tmp_path / "UA").glob("*.json"):
        assert path.stat().st_mode & 0o777 == 0o600
    for _ in range(10):
        notify_product_once(bridge, slack)
    assert any(
        "내 조건" in json.dumps(message, ensure_ascii=False)
        for message in slack.messages + slack.updates
    )
    assert flow.deliveries() == []


def test_notice_survives_restart_after_approval_transition(system, tmp_path, monkeypatch):
    _, flow, _, agents = system
    model = Model(tmp_path)
    slack = Slack()
    worker = ProductWorker(
        agents["UA"],
        model,
        slack,
        tmp_path / "state",
        owner="UA",
        allowed_roots=[tmp_path],
        bot_user="UBOT",
        wait_seconds=0,
    )
    create(flow, mode="agent")
    task = flow.claim("UA")
    flow.delivered(task["id"], "UA", 1, "DUBOT", "1")
    worker.approval(task, "private draft")
    assert slack.messages == []
    restarted = ProductWorker(
        agents["UA"],
        model,
        slack,
        tmp_path / "state",
        owner="UA",
        allowed_roots=[tmp_path],
        bot_user="UBOT",
        wait_seconds=0,
    )
    restarted.flush_notices()
    assert len(slack.messages) == 1
    restarted.flush_notices()
    assert len(slack.messages) == 1


def test_idle_worker_uses_one_180_second_request(tmp_path):
    calls = []

    class Relay:
        def request(self, *args, **kwargs):
            calls.append((args, kwargs))
            return {"work": None, "requests": []}

    worker = ProductWorker(
        Relay(),
        Model(tmp_path),
        Slack(),
        tmp_path / "state",
        owner="UA",
        allowed_roots=[tmp_path],
        bot_user="UBOT",
    )
    assert not worker.once()
    assert calls == [
        (
            ("POST", "/v2/work/claim"),
            {
                "params": {"wait_seconds": 180, "include_requests": True},
                "timeout": 200,
            },
        )
    ]
    with pytest.raises(ValueError):
        ProductWorker(
            Relay(),
            Model(tmp_path),
            Slack(),
            tmp_path / "state",
            owner="UA",
            allowed_roots=[tmp_path],
            bot_user="UBOT",
            wait_seconds=181,
        )


def test_v2_claim_accepts_180_and_keeps_old_response_shape(system):
    _, flow, _, agents = system
    task = create(flow)
    assert (
        agents["UA"].request("POST", "/v2/work/claim", params={"wait_seconds": 180})["id"]
        == task["id"]
    )
    assert agents["UB"].request("POST", "/v2/work/claim") is None
    for value in (-1, 181):
        assert (
            agents["UA"].http.post("/v2/work/claim", params={"wait_seconds": value}).status_code
            == 422
        )
    assert (
        agents["UA"]
        .http.post("/v2/work/claim", headers={"Authorization": "Bearer invalid"})
        .status_code
        == 401
    )


@pytest.mark.parametrize("kind", ["work", "audit"])
def test_combined_long_poll_wakes_for_work_or_own_audit(system, kind):
    _, flow, _, agents = system
    task = draft(flow, create(flow))
    # Another owner's pending request must not wake this worker.
    flow.request_audit("UB", task["id"])
    action = (
        (lambda: create(flow, "later"))
        if kind == "work"
        else (lambda: flow.request_audit("UA", task["id"]))
    )
    timer = threading.Timer(0.1, action)
    timer.start()
    started = time.monotonic()
    try:
        batch = agents["UA"].request(
            "POST", "/v2/work/claim", params={"wait_seconds": 4, "include_requests": True}
        )
    finally:
        timer.join()
    assert time.monotonic() - started < 3
    if kind == "work":
        assert batch["work"]["state"] == "prepare_running"
        assert batch["requests"] == []
    else:
        assert batch["work"] is None
        assert len(batch["requests"]) == 1
        assert batch["requests"][0]["owner"] == "UA"


def test_legacy_audit_request_does_not_send_slack_logs_or_lease_work(system, tmp_path):
    _, flow, _, agents = system
    task = create(flow)
    flow.delivered(task["id"], "UA", 1, "DUBOT", "1")
    flow.request_audit("UA", task["id"])
    slack = Slack()
    worker = ProductWorker(
        agents["UA"],
        Model(tmp_path),
        slack,
        tmp_path / "state",
        owner="UA",
        allowed_roots=[tmp_path],
        bot_user="UBOT",
        wait_seconds=0,
    )
    assert worker.once()
    assert slack.messages == []
    assert flow.local_requests("UA") == []
    assert flow.get("UA", task["id"])["state"] == "prepare_pending"


def test_combined_poll_deadline_returns_empty_envelope(system):
    _, _, _, agents = system
    start = time.monotonic()
    batch = agents["UA"].request(
        "POST", "/v2/work/claim", params={"wait_seconds": 1, "include_requests": True}
    )
    assert batch == {"work": None, "requests": []}
    assert 0.9 <= time.monotonic() - start < 3


@pytest.mark.parametrize("mode", ["dm", "agent"])
def test_updated_workers_complete_both_directions_with_approval(
    system, tmp_path, monkeypatch, mode
):
    _, flow, bridge, agents = system
    monkeypatch.setattr("tacit.product_worker.create_provider", lambda *args: Model(tmp_path))
    slack = Slack()
    workers = {
        u: ProductWorker(
            agents[u],
            Model(tmp_path),
            slack,
            tmp_path / u,
            owner=u,
            allowed_roots=[tmp_path],
            bot_user="UBOT",
            wait_seconds=0,
        )
        for u in ("UA", "UB")
    }
    for sender, recipient in (("UA", "UB"), ("UB", "UA")):
        task = flow.create(sender + mode, sender, recipient, "test context", mode)
        assert workers[sender].once()
        assert flow.get(sender, task["id"])["state"] == "approval_wait"
        assert not workers[recipient].once()
        bridge.request(
            "POST",
            "/v2/decisions/" + task["id"],
            json={"owner": sender, "version": 1, "action": "approve"},
        )
        assert workers[sender].once()
        assert workers[recipient].once()
        if mode == "agent":
            assert flow.get(sender, task["id"])["state"] == "approval_wait"
            bridge.request(
                "POST",
                "/v2/decisions/" + task["id"],
                json={"owner": recipient, "version": 2, "action": "approve"},
            )
            assert workers[recipient].once()
        assert flow.get(recipient, task["id"])["state"] == "completed"
    assert len(slack.originals) == (2 if mode == "dm" else 0)


def test_corpus_excludes_credentials_symlinks_and_can_find_late_files(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "private.md"
    outside.write_text("OUTSIDE")
    (root / "escape.md").symlink_to(outside)
    (root / "nested").symlink_to(tmp_path, target_is_directory=True)
    (root / "token.json").write_text("SECRET")
    (root / ".env").write_text("SECRET")
    for i in range(40):
        (root / f"file-{i:02}.md").write_text("unrelated filler")
    (root / "z-target.md").write_text("desired baseline B17")
    corpus = Corpus(root)
    assert corpus.search("baseline")[0]["path"] == "z-target.md"
    assert not {"escape.md", "token.json", ".env"} & set(corpus.paths)
    with pytest.raises(ValueError):
        corpus.read("../private.md")
    (root / "z-target.md").unlink()
    (root / "z-target.md").symlink_to(outside)
    with pytest.raises(ValueError):
        corpus.read("z-target.md")


def test_private_queries_cannot_be_sent_to_public_web(tmp_path, monkeypatch):
    (tmp_path / "public-web-queries.txt").write_text("public baseline definition\n")
    calls = []

    class Planner:
        def run_evidence(self, prompt, sources):
            return json.dumps(
                {
                    "queries": ["PRIVATE_SECRET"],
                    "web_queries": ["PRIVATE_SECRET"],
                    "web_indices": [0],
                }
            )

    monkeypatch.setattr("tacit.retrieval.web_search", lambda q: calls.append(q) or [])
    Researcher(Planner(), tmp_path, lambda *a: None, web=False).collect("PRIVATE_SECRET")
    assert calls == []
    Researcher(Planner(), tmp_path, lambda *a: None, web=True).collect("PRIVATE_SECRET")
    assert calls == ["public baseline definition"]


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com",
        "https://127.0.0.1",
        "https://[::1]",
        "https://user:pass@example.com",
        "https://example.com:8765",
    ],
)
def test_web_never_accesses_private_or_credential_urls(url):
    with pytest.raises(ValueError):
        public_get(url)


def test_auto_receive_off_hides_results_from_delivery_but_preserves_private_lookup(system):
    _, flow, _, _ = system
    flow.settings("UB", {"auto_receive": False})
    task = draft(flow, create(flow))
    approve(flow, task)
    work = flow.claim("UB")
    flow.change(task["id"], "UB", "result", "PRIVATE RESULT", lease=work["lease"])
    for _ in range(10):
        for item in flow.deliveries():
            assert "PRIVATE RESULT" not in item["text"]
            flow.delivered(item["id"], item["owner"], item["seq"], "D1", "1")
    assert flow.get("UB")["result"] == "PRIVATE RESULT"


def test_receive_selects_incoming_not_newer_outgoing(system):
    _, flow, bridge, _ = system
    incoming = flow.create("in", "UB", "UA", "incoming")
    flow.create("out", "UA", "UB", "outgoing")
    response = bridge.request(
        "GET", "/v2/exchanges/latest", params={"owner": "UA", "received_only": True}
    )
    assert response["id"] == incoming["id"]


def test_slack_handlers_register_and_reject_other_user_approval(system):
    from tacit.slack_product import register_product

    _, flow, bridge, _ = system
    task = draft(flow, create(flow))

    class App:
        def __init__(self):
            self.handlers = {}

        def action(self, key):
            def register(fn):
                self.handlers[fn.__name__] = fn
                return fn

            return register

        event = view = action

    app = App()
    register_product(app, bridge, "TEAM")
    replies, acknowledgments = [], []
    body = {
        "team": {"id": "TEAM"},
        "user": {"id": "UB"},
        "actions": [
            {"action_id": "tacit_approve", "value": json.dumps({"id": task["id"], "version": 1})}
        ],
    }
    app.handlers["decision"](
        lambda: acknowledgments.append(True),
        body,
        None,
        lambda *args, **kwargs: replies.append((args, kwargs)),
    )
    assert acknowledgments == [True]
    assert replies
    assert flow.get("UA")["state"] == "approval_wait"
    body["user"]["id"] = "UA"
    app.handlers["decision"](lambda: None, body, None, lambda *args, **kwargs: None)
    assert flow.get("UA")["state"] == "publish_pending"


def test_automatic_dm_ignores_messages_posted_by_our_worker(system):
    from tacit.slack_product import register_product

    _, flow, bridge, _ = system
    task = create(flow)
    flow.settings("UA", {"auto_send": True})

    class App:
        handlers = {}

        def action(self, key):
            def register(fn):
                self.handlers[fn.__name__] = fn
                return fn

            return register

        event = view = action

    class SlackMembers:
        def conversations_members(self, channel):
            return {"members": ["UA", "UB"]}

    app = App()
    register_product(app, bridge, "TEAM")
    event = {
        "channel_type": "im",
        "channel": "D1",
        "ts": "123",
        "user": "UA",
        "text": "message",
        "client_msg_id": task["id"],
    }
    app.handlers["automatic"](event, {"team_id": "TEAM"}, SlackMembers())
    assert len(flow.recent("UA")) == 1
    event["client_msg_id"] = "human-generated"
    app.handlers["automatic"](event, {"team_id": "TEAM"}, SlackMembers())
    assert len(flow.recent("UA")) == 2
    newest = flow.get("UA")
    assert newest["source"] == {"channel": "D1", "ts": "123"}


def test_web_pins_checked_public_ip_and_keeps_tls_hostname(monkeypatch):
    from contextlib import contextmanager

    monkeypatch.setattr(
        "tacit.retrieval.socket.getaddrinfo", lambda *args: [(2, 1, 6, "", ("93.184.216.34", 443))]
    )

    class Response:
        is_redirect = False
        headers = {"content-type": "text/html"}

        def raise_for_status(self):
            pass

        def iter_bytes(self):
            yield b"<html><body>Public text</body></html>"

    class HTTP:
        def __init__(self, **kwargs):
            assert kwargs["trust_env"] is False
            assert kwargs["follow_redirects"] is False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        @contextmanager
        def stream(self, method, url, **kwargs):
            assert url.host == "93.184.216.34"
            assert kwargs["headers"]["Host"] == "example.com"
            assert kwargs["extensions"]["sni_hostname"] == "example.com"
            yield Response()

    monkeypatch.setattr("tacit.retrieval.httpx.Client", HTTP)
    assert "Public text" in public_get("https://example.com/page").parts


def test_card_updates_one_parent_without_any_thread_logs(system):
    from tacit.presentation import card_blocks

    _, flow, bridge, _ = system

    class Cards(Slack):
        def __init__(self):
            super().__init__()
            self.updates = []

        def chat_update(self, **kwargs):
            self.updates.append(kwargs)

    slack = Cards()
    task = create(flow)
    notify_product_once(bridge, slack)
    draft(flow, task)
    approve(flow, task)
    work = flow.claim("UB")
    full = "핵심 답변입니다. " + "자세한 근거가 있습니다. " * 100
    flow.change(task["id"], "UB", "result", full, lease=work["lease"])
    for _ in range(10):
        notify_product_once(bridge, slack)
    roots = [m for m in slack.messages if not m.get("thread_ts")]
    assert len(roots) == 2  # Exactly one main card per participant.
    assert slack.updates
    assert all(not m.get("reply_broadcast") for m in slack.messages)
    assert all(not m.get("thread_ts") for m in slack.messages)
    assert len(slack.messages) == 2
    assert flow.get("UB")["result"] == full
    for update in slack.updates:
        visible = "\n".join(b.get("text", {}).get("text", "") for b in update["blocks"])
        assert len(visible) < 500
        assert task["id"] not in visible
        assert "completed" not in visible
    assert card_blocks({**flow.get("UB"), "viewer": "UB"})[0] == "답변이 왔어요"


def test_missing_parent_queues_preview_without_creating_another_main_message(system, tmp_path):
    _, flow, _, agents = system
    slack = Slack()
    worker = ProductWorker(
        agents["UA"],
        Model(tmp_path),
        slack,
        tmp_path / "local",
        owner="UA",
        allowed_roots=[tmp_path],
        bot_user="UBOT",
        wait_seconds=0,
    )
    create(flow, mode="agent")
    work = flow.claim("UA")
    worker.approval(work, "오후 2시라는 뜻이에요.")
    worker.flush_notices()
    assert worker.notices_waiting
    assert slack.messages == []
    flow.delivered(work["id"], "UA", 1, "DOWNER", "parent.1")
    worker.flush_notices()
    assert not worker.notices_waiting
    assert len(slack.messages) == 1
    assert "thread_ts" not in slack.messages[0]
    assert slack.messages[0]["channel"] == "DOWNER"
    assert slack.messages[0]["ephemeral"] is True
    assert slack.messages[0]["user"] == "UA"


def test_card_payload_excludes_private_draft_and_other_users_result(system):
    from tacit.presentation import card_snapshot

    _, flow, _, _ = system
    task = create(flow)
    task.update(
        state="completed",
        result="PRIVATE RECIPIENT RESULT",
        approved_edit="PRIVATE DRAFT",
        user_answer="PRIVATE ANSWER",
    )
    assert "PRIVATE" not in json.dumps(card_snapshot(task, "UA"))
    assert "PRIVATE" not in json.dumps(card_snapshot(task, "UB", auto_receive=False))
    assert card_snapshot(task, "UB")["result"] == "PRIVATE RECIPIENT RESULT"


class CardApp:
    def __init__(self):
        self.handlers = {}

    def action(self, key):
        def register(fn):
            self.handlers[fn.__name__] = fn
            return fn

        return register

    event = view = action


@pytest.mark.parametrize("stale", [False, True])
def test_root_review_requests_local_preview_without_uploading_or_approving(system, stale):
    from tacit.slack_product import register_product

    store, flow, bridge, _ = system
    private = "공유 예정인 문장. " * 320 + "마지막의 중요한 조건도 보여야 합니다."
    task = draft(flow, create(flow), private)
    flow.delivered(task["id"], "UA", 1, "DOWNER", "root.1")
    views, responses = [], []

    class UI:
        def views_open(self, **kwargs):
            views.append(kwargs["view"])

    app = CardApp()
    register_product(app, bridge, "TEAM")
    body = {
        "team": {"id": "TEAM"},
        "user": {"id": "UA"},
        "trigger_id": "trigger",
        "actions": [{"value": json.dumps({"id": task["id"], "version": 0 if stale else 1})}],
    }
    app.handlers["review"](lambda: None, body, UI(), lambda msg: responses.append(msg))
    assert views == []
    if stale:
        assert responses
        assert flow.local_requests("UA") == []
    else:
        requests = flow.local_requests("UA")
        assert len(requests) == 1 and requests[0]["kind"] == "preview"
        assert flow.local_requests("UB") == []
    assert flow.get("UA")["state"] == "approval_wait"
    with store.db() as db:
        assert private not in db.execute("SELECT body FROM workflows").fetchone()[0]


def test_root_review_rejects_other_user_before_reading_private_slack_thread(system):
    from tacit.slack_product import register_product

    _, flow, bridge, _ = system
    task = draft(flow, create(flow))
    app, responses = CardApp(), []
    register_product(app, bridge, "TEAM")
    body = {
        "team": {"id": "TEAM"},
        "user": {"id": "UB"},
        "actions": [{"value": json.dumps({"id": task["id"], "version": 1})}],
    }
    app.handlers["review"](lambda: None, body, None, lambda msg: responses.append(msg))
    assert responses


def test_private_clarification_updates_only_own_main_card(system):
    _, flow, _, _ = system
    task = draft(flow, create(flow))
    approve(flow, task)
    work = flow.claim("UB")
    flow.change(task["id"], "UB", "ask_user", "PRIVATE QUESTION", lease=work["lease"])
    seen = []
    for _ in range(10):
        for item in flow.deliveries():
            seen.append(item)
            assert "PRIVATE QUESTION" not in json.dumps(item)
            flow.delivered(item["id"], item["owner"], item["seq"], "D" + item["owner"], "1")
    questions = [item for item in seen if item["kind"] == "user_question"]
    assert [item["owner"] for item in questions] == ["UB"]
    assert questions[0]["card"]["state"] == "user_wait"


@pytest.mark.parametrize("edited", [False, True])
def test_review_submission_preserves_exact_user_text_and_approval_kind(system, edited):
    from tacit.slack_product import register_product

    _, flow, bridge, _ = system
    original = "오후 2시라는 뜻이에요."
    task = draft(flow, create(flow), original)
    value = "오후 3시로 수정해서 공유해요." if edited else original
    app = CardApp()
    register_product(app, bridge, "TEAM")
    view = {
        "private_metadata": json.dumps({"id": task["id"], "version": 1, "action": "edit"}),
        "state": {"values": {"answer": {"value": {"value": value}}}},
    }
    app.handlers["decision_save"](
        lambda **kwargs: None, {"team": {"id": "TEAM"}, "user": {"id": "UA"}}, view, None
    )
    current = flow.get("UA")
    assert current["state"] == "publish_pending"
    assert current["events"][-1]["kind"] == ("edit" if edited else "approve")
    work = flow.claim("UA")
    flow.change(task["id"], "UA", "publish", value, lease=work["lease"])
    assert flow.get("UB")["context"] == value


class DMSlack(Slack):
    def __init__(self, history=(), fail=False):
        super().__init__()
        self.history, self.fail, self.reads = list(history), fail, []

    def conversations_history(self, **kwargs):
        self.reads.append(kwargs)
        if self.fail:
            raise RuntimeError("missing_scope")
        return {"messages": self.history}


class Recorder(Model):
    def __init__(self, workspace, ask=False):
        super().__init__(workspace)
        self.ask, self.calls = ask, []

    def run_evidence(self, prompt, sources):
        self.calls.append((prompt, sources))
        if self.ask and "recipient agent" in prompt and '"user_answer": null' in prompt:
            return '{"action":"ask_user","text":"LOCAL 내 쪽 조건을 알려주세요."}'
        return super().run_evidence(prompt, sources)


def dm_workers(agents, slack, tmp_path, monkeypatch, model):
    workspace = tmp_path / "corpus"
    workspace.mkdir(exist_ok=True)
    monkeypatch.setattr("tacit.product_worker.create_provider", lambda *args: model)
    return {
        u: ProductWorker(
            agents[u],
            model,
            slack,
            tmp_path / u,
            owner=u,
            allowed_roots=[workspace],
            bot_user="UBOT",
            wait_seconds=0,
        )
        for u in ("UA", "UB")
    }


def ephemerals(slack, user):
    return [m for m in slack.messages if m.get("ephemeral") and m["user"] == user]


def visible(message):
    return json.dumps(message["blocks"], ensure_ascii=False)


def clicked(message, action_id, user):
    button = next(
        e
        for b in message["blocks"]
        if b["type"] == "actions"
        for e in b["elements"]
        if e["action_id"] == action_id
    )
    # Ephemeral interaction payloads carry the container but not the message.
    return {
        "team": {"id": "TEAM"},
        "user": {"id": user},
        "trigger_id": "trigger",
        "container": {"type": "message", "is_ephemeral": True},
        "actions": [button],
    }


class Views:
    def __init__(self):
        self.opened = []

    def views_open(self, **kwargs):
        self.opened.append(kwargs["view"])


def test_dm_mode_keeps_both_users_in_original_dm_without_bot_thread(system, tmp_path, monkeypatch):
    store, flow, bridge, agents = system
    slack = DMSlack()
    workers = dm_workers(agents, slack, tmp_path, monkeypatch, Model(tmp_path / "corpus"))
    task = create(flow)
    assert workers["UA"].once()
    assert not workers["UA"].notices_waiting
    [preview] = slack.messages
    assert preview["ephemeral"] and preview["channel"] == "D1" and preview["user"] == "UA"
    assert "PRIVATE_DRAFT" in visible(preview)
    with store.db() as db:
        assert "PRIVATE_DRAFT" not in db.execute("SELECT body FROM workflows").fetchone()[0]
    bridge.request(
        "POST",
        "/v2/decisions/" + task["id"],
        json={"owner": "UA", "version": 1, "action": "approve"},
    )
    assert workers["UA"].once()
    assert workers["UB"].once()
    assert flow.get("UB")["state"] == "completed"
    [result] = ephemerals(slack, "UB")
    assert result["channel"] == "D1"
    assert "나를 위한 설명" in visible(result) and "내 조건과 B17 p2" in visible(result)
    assert len(ephemerals(slack, "UA")) == 1
    for worker in workers.values():
        worker.flush_notices()
        worker.once()
    assert len(slack.messages) == 2
    assert not any(m.get("ephemeral") is None for m in slack.messages)


def test_dm_result_is_not_pushed_again_as_a_new_bot_message(system, tmp_path, monkeypatch):
    _, flow, bridge, agents = system
    slack = DMSlack()
    workers = dm_workers(agents, slack, tmp_path, monkeypatch, Model(tmp_path / "corpus"))
    task = create(flow)
    notify_product_once(bridge, slack)
    workers["UA"].once()
    bridge.request(
        "POST",
        "/v2/decisions/" + task["id"],
        json={"owner": "UA", "version": 1, "action": "approve"},
    )
    workers["UA"].once()
    workers["UB"].once()
    for _ in range(10):
        notify_product_once(bridge, slack)
    posted = [m for m in slack.messages if not m.get("ephemeral")]
    assert all("내 조건" not in json.dumps(m, ensure_ascii=False) for m in posted)
    assert any("내 조건" in json.dumps(u, ensure_ascii=False) for u in slack.updates)
    assert any("내 조건" in visible(m) for m in ephemerals(slack, "UB"))
    assert flow.deliveries() == []


def test_dm_result_waits_for_view_button_when_auto_receive_is_off(system, tmp_path, monkeypatch):
    from tacit.slack_product import register_product

    _, flow, bridge, agents = system
    flow.settings("UB", {"auto_receive": False})
    slack = DMSlack()
    workers = dm_workers(agents, slack, tmp_path, monkeypatch, Model(tmp_path / "corpus"))
    task = draft(flow, create(flow), "PRIVATE_DRAFT: B17 p2. evidence.md")
    approve(flow, task, "PRIVATE_DRAFT: B17 p2. evidence.md")
    assert workers["UB"].once()
    [ready] = ephemerals(slack, "UB")
    assert ready["channel"] == "D1"
    assert "설명이 준비됐어요" in visible(ready) and "내 조건" not in visible(ready)
    app, ui = CardApp(), Views()
    register_product(app, bridge, "TEAM")
    app.handlers["history"](lambda: None, clicked(ready, "tacit_result", "UB"), ui)
    assert "내 조건과 B17 p2" in json.dumps(ui.opened, ensure_ascii=False)
    app.handlers["history"](lambda: None, clicked(ready, "tacit_result", "UA"), ui)
    assert "내 조건" not in json.dumps(ui.opened[-1], ensure_ascii=False)


def test_dm_clarification_is_answered_from_original_dm(system, tmp_path, monkeypatch):
    from tacit.slack_product import register_product

    store, flow, bridge, agents = system
    slack = DMSlack()
    workers = dm_workers(
        agents, slack, tmp_path, monkeypatch, Recorder(tmp_path / "corpus", ask=True)
    )
    task = draft(flow, create(flow), "PRIVATE_DRAFT: B17 p2. evidence.md")
    approve(flow, task, "PRIVATE_DRAFT: B17 p2. evidence.md")
    assert workers["UB"].once()
    assert flow.get("UB")["state"] == "user_wait"
    [question] = ephemerals(slack, "UB")
    assert question["channel"] == "D1" and "LOCAL 내 쪽 조건" in visible(question)
    with store.db() as db:
        assert "LOCAL" not in db.execute("SELECT body FROM workflows").fetchone()[0]
    app, ui, replies = CardApp(), Views(), []
    register_product(app, bridge, "TEAM")
    body = clicked(question, "tacit_user_answer", "UB")
    for user in ("UA", "UB"):
        app.handlers["decision"](
            lambda: None,
            {**body, "user": {"id": user}},
            ui,
            lambda *a, **k: replies.append((a, k)),
        )
    assert len(replies) == 1 and len(ui.opened) == 1
    meta = json.loads(ui.opened[0]["private_metadata"])
    assert meta == {"id": task["id"], "version": 2, "action": "user_answer"}
    app.handlers["decision_save"](
        lambda **kwargs: None,
        {"team": {"id": "TEAM"}, "user": {"id": "UB"}},
        {**ui.opened[0], "state": {"values": {"answer": {"value": {"value": "내 조건은 p3"}}}}},
        None,
    )
    assert flow.get("UB")["state"] == "interpret_pending"
    app.handlers["decision"](lambda: None, body, ui, lambda *a, **k: replies.append((a, k)))
    assert len(replies) == 2 and len(ui.opened) == 1
    assert workers["UB"].once()
    assert flow.get("UB")["state"] == "completed"
    assert "내 조건과 B17 p2" in visible(ephemerals(slack, "UB")[-1])


def test_ephemeral_edit_prefills_shown_preview_and_rejects_stale_buttons(
    system, tmp_path, monkeypatch
):
    from tacit.slack_product import register_product

    store, flow, bridge, agents = system
    slack = DMSlack()
    workers = dm_workers(agents, slack, tmp_path, monkeypatch, Model(tmp_path / "corpus"))
    task = create(flow)
    workers["UA"].once()
    [preview] = slack.messages
    app, ui, replies = CardApp(), Views(), []
    register_product(app, bridge, "TEAM")

    def respond(*args, **kwargs):
        replies.append((args, kwargs))

    app.handlers["decision"](lambda: None, clicked(preview, "tacit_edit", "UA"), ui, respond)
    [editor] = ui.opened
    assert editor["blocks"][0]["element"]["initial_value"] == "PRIVATE_DRAFT: B17 p2. evidence.md"
    assert json.loads(editor["private_metadata"]) == {
        "id": task["id"],
        "version": 1,
        "action": "edit",
    }
    with store.db() as db:
        assert "PRIVATE_DRAFT" not in db.execute("SELECT body FROM workflows").fetchone()[0]
    app.handlers["decision"](lambda: None, clicked(preview, "tacit_approve", "UA"), ui, respond)
    assert replies[-1][1]["replace_original"] is True
    assert flow.get("UA")["state"] == "publish_pending"
    for action in ("tacit_edit", "tacit_approve", "tacit_cancel"):
        app.handlers["decision"](lambda: None, clicked(preview, action, "UA"), ui, respond)
    assert len(ui.opened) == 1 and len(replies) == 4
    assert all("최신" in args[0] for args, _ in replies[1:])
    assert flow.get("UA")["state"] == "publish_pending"


def test_long_draft_is_not_embedded_in_edit_button(system, tmp_path):
    _, flow, _, agents = system
    worker = ProductWorker(
        agents["UA"],
        Model(tmp_path),
        Slack(),
        tmp_path / "state",
        owner="UA",
        allowed_roots=[tmp_path],
        bot_user="UBOT",
        wait_seconds=0,
    )
    create(flow, mode="agent")
    work = flow.claim("UA")
    worker.approval(work, "가" * 2500)
    [outbox] = (tmp_path / "state").glob("*-outbox.json")
    values = [json.loads(a["value"]) for a in json.loads(outbox.read_text())["actions"]]
    assert all(set(v) == {"id", "version"} for v in values)


def test_dm_preview_requested_from_card_is_shown_in_bot_thread(system, tmp_path, monkeypatch):
    _, flow, bridge, agents = system
    slack = DMSlack()
    workers = dm_workers(agents, slack, tmp_path, monkeypatch, Model(tmp_path / "corpus"))
    task = create(flow)
    notify_product_once(bridge, slack)
    workers["UA"].once()
    assert ephemerals(slack, "UA")[0]["channel"] == "D1"
    flow.request_preview("UA", task["id"], 1)
    workers["UA"].once()
    assert [m["channel"] for m in ephemerals(slack, "UA")] == ["D1", "DUA"]


HISTORY = [
    {"ts": "1", "user": "UA", "text": "CURRENT ORIGINAL"},
    {"ts": "0.9", "user": "UB", "text": "<@UA> 어제 B17 결과 봤어?"},
    {"ts": "0.8", "bot_id": "B1", "user": "UBOT", "text": "BOT NOISE"},
    {"ts": "0.7", "subtype": "channel_join", "user": "UB", "text": "JOINED"},
    {"ts": "0.65", "bot_id": "B1", "user": "UB", "text": "Tacit으로 보낸 원문"},
    {"ts": "0.6", "user": "UA", "text": "p2 전처리로 다시 돌렸어 <@UC|철수>"},
    {"ts": "0.5", "user": "UC", "text": "OUTSIDER"},
]


def history_sources(model):
    return [s for _, sources in model.calls for s in sources if s.get("path") == "slack:dm-history"]


def test_dm_history_is_local_evidence_for_sender_and_recipient(system, tmp_path, monkeypatch):
    store, flow, bridge, agents = system
    slack = DMSlack(HISTORY)
    model = Recorder(tmp_path / "corpus")
    workers = dm_workers(agents, slack, tmp_path, monkeypatch, model)
    task = create(flow)
    workers["UA"].once()
    [sender] = {s["content"]: s for s in history_sources(model)}.values()
    assert slack.reads[0]["channel"] == "D1" and slack.reads[0]["latest"] == "1"
    lines = sender["content"].splitlines()[1:]
    assert lines == [
        "나: p2 전처리로 다시 돌렸어 철수",
        "상대: Tacit으로 보낸 원문",
        "상대: 나 어제 B17 결과 봤어?",
    ]
    for noise in ("CURRENT", "BOT", "JOINED", "OUTSIDER", "<@"):
        assert noise not in sender["content"]
    bridge.request(
        "POST",
        "/v2/decisions/" + task["id"],
        json={"owner": "UA", "version": 1, "action": "approve"},
    )
    workers["UA"].once()
    model.calls.clear()
    workers["UB"].once()
    [recipient] = {s["content"]: s for s in history_sources(model)}.values()
    assert recipient["content"].splitlines()[1:] == [
        "상대: p2 전처리로 다시 돌렸어 철수",
        "나: Tacit으로 보낸 원문",
        "나: 상대 어제 B17 결과 봤어?",
    ]
    with store.db() as db:
        assert "B17 결과" not in db.execute("SELECT body FROM workflows").fetchone()[0]
    audit = (tmp_path / "UB" / f"{task['id']}-audit.jsonl").read_text()
    assert '"kind": "history", "count": 3' in audit


def test_dm_history_is_capped_and_skipped_for_agent_mode(system, tmp_path, monkeypatch):
    _, flow, _, agents = system
    long = [
        {"ts": f"0.{i:03}", "user": "UB", "text": f"{i} " + "긴 대화 " * 60}
        for i in range(999, 940, -1)
    ]
    slack = DMSlack(long)
    model = Recorder(tmp_path / "corpus")
    workers = dm_workers(agents, slack, tmp_path, monkeypatch, model)
    create(flow)
    workers["UA"].once()
    item = history_sources(model)[-1]
    lines = item["content"].splitlines()[1:]
    assert item["truncated"] and len(lines) <= 30 and len(item["content"]) <= 6100
    assert lines[-1].startswith("상대: 999 ")
    create(flow, "agent", mode="agent")
    model.calls.clear()
    workers["UA"].once()
    assert len(slack.reads) == 1 and history_sources(model) == []


@pytest.mark.parametrize("failure", ["preference", "error"])
def test_dm_history_respects_preference_and_tolerates_errors(
    system, tmp_path, monkeypatch, failure
):
    _, flow, _, agents = system
    if failure == "preference":
        flow.settings("UA", {"history": False})
    slack = DMSlack(HISTORY, fail=failure == "error")
    model = Recorder(tmp_path / "corpus")
    workers = dm_workers(agents, slack, tmp_path, monkeypatch, model)
    task = create(flow)
    assert workers["UA"].once()
    assert flow.get("UA", task["id"])["state"] == "approval_wait"
    assert history_sources(model) == []
    assert len(slack.reads) == (0 if failure == "preference" else 1)
    audit = (tmp_path / "UA" / f"{task['id']}-audit.jsonl").read_text()
    assert ("history_failed" in audit) == (failure == "error")


def test_history_preference_default_validation_and_home_checkbox(system):
    from tacit.slack_product import register_product, settings_view

    _, flow, bridge, _ = system
    assert flow.settings("UA")["history"] is True
    with pytest.raises(Conflict):
        flow.settings("UA", {"history": "yes"})
    view = settings_view(flow.settings("UA"))
    flags = next(b for b in view["blocks"] if b["block_id"] == "flags")["element"]
    assert "history" in {o["value"] for o in flags["initial_options"]}
    published = []

    class Home:
        def views_publish(self, **kwargs):
            published.append(kwargs)

    app = CardApp()
    register_product(app, bridge, "TEAM")
    values = {k: {"value": {"value": ""}} for k in ("workspace", "backend", "model")}
    values["flags"] = {"value": {"selected_options": [{"value": "web"}]}}
    app.handlers["save"](
        lambda **kwargs: None,
        {"team": {"id": "TEAM"}, "user": {"id": "UA"}},
        {"state": {"values": values}},
        Home(),
    )
    assert flow.settings("UA")["history"] is False and flow.settings("UA")["web"] is True
    assert published


def test_dm_notice_rejected_by_slack_falls_back_to_tacit_card(system, tmp_path, monkeypatch):
    from slack_sdk.errors import SlackApiError

    _, flow, bridge, agents = system

    class Rejecting(DMSlack):
        def chat_postEphemeral(self, **kwargs):
            if kwargs["channel"] == "D1":
                raise SlackApiError("rejected", {"error": "channel_not_found"})
            return super().chat_postEphemeral(**kwargs)

    slack = Rejecting()
    workers = dm_workers(agents, slack, tmp_path, monkeypatch, Model(tmp_path / "corpus"))
    create(flow)
    assert workers["UA"].once()
    assert workers["UA"].notices_waiting and slack.messages == []
    notify_product_once(bridge, slack)
    workers["UA"].flush_notices()
    assert [m["channel"] for m in ephemerals(slack, "UA")] == ["DUA"]
    assert flow.get("UA")["state"] == "approval_wait"
