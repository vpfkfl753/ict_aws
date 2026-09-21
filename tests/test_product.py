import hashlib
import json
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

    def send(self, task):
        self.originals.append(task["text"])
        return {"channel": "D1", "ts": "1"}

    def conversations_open(self, users):
        return {"channel": {"id": "D" + users}}

    def chat_postMessage(self, **kwargs):
        self.messages.append(kwargs)
        return {"channel": kwargs["channel"], "ts": str(len(self.messages))}


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
    assert any("내 조건" in json.dumps(message, ensure_ascii=False) for message in slack.messages)
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
