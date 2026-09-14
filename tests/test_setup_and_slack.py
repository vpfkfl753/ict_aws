import json
import sys

import pytest
from dotenv import dotenv_values

from tacit.setup import main
from tacit.worker import SlackSender


def test_generated_configuration_has_distinct_tokens_and_correct_owners(monkeypatch, tmp_path):
    output = tmp_path / "settings"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "setup",
            "--team",
            "T1",
            "--owner",
            "UA",
            "--peer",
            "UB",
            "--workspace",
            str(tmp_path),
            "--output",
            str(output),
        ],
    )
    main()
    relay = dotenv_values(output / "relay.env")
    owner = dotenv_values(output / "owner.env")
    peer = dotenv_values(output / "peer.env")
    members = json.loads(relay["TACIT_MEMBERS"])
    assert members["UA"] == owner["TACIT_AGENT_TOKEN"]
    assert members["UB"] == peer["TACIT_AGENT_TOKEN"]
    assert len({*members.values(), relay["TACIT_BRIDGE_TOKEN"]}) == 3
    assert owner["TACIT_WORKSPACE"] == str(tmp_path)
    assert (output / "owner.env").stat().st_mode & 0o777 == 0o600


def test_slack_sender_validates_identity_and_posts_original(monkeypatch):
    class SlackClient:
        def __init__(self, **kwargs):
            self.sent = []

        def auth_test(self):
            return {"user_id": "UA", "team_id": "T1"}

        def conversations_open(self, users):
            assert users == "UB"
            return {"channel": {"id": "D12"}}

        def chat_postMessage(self, **kwargs):
            self.sent.append(kwargs)
            return {"channel": "D12", "ts": "1.2"}

    monkeypatch.setattr("tacit.worker.WebClient", SlackClient)
    with pytest.raises(ValueError):
        SlackSender("token", "UB", "T1")
    sender = SlackSender("token", "UA", "T1")
    assert sender.send({"id": "exchange", "recipient": "UB", "text": "original text"}) == {
        "channel": "D12",
        "ts": "1.2",
    }
    assert sender.client.sent[0]["text"] == "original text"
    assert sender.client.sent[0]["channel"] == "D12"
