import pytest

from tacit.check_slack import check
from tacit.setup import write_env


def test_check_validates_scopes_identity_and_socket_without_messages(tmp_path):
    slack = tmp_path / "slack.env"
    worker = tmp_path / "worker.env"
    write_env(slack, {"TACIT_TEAM_ID": "T1", "SLACK_BOT_TOKEN": "bot", "SLACK_APP_TOKEN": "app"})
    write_env(worker, {"TACIT_TEAM_ID": "T1", "TACIT_OWNER": "U1", "SLACK_USER_TOKEN": "user"})
    calls = []

    class Response(dict):
        headers = {"x-oauth-scopes": "chat:write,im:write,commands"}

    class Client:
        def __init__(self, token, timeout):
            self.token = token

        def auth_test(self):
            calls.append((self.token, "auth.test"))
            return Response(
                team_id="T1", user_id="U1", **({"bot_id": "B1"} if self.token == "bot" else {})
            )

        def apps_connections_open(self, *, app_token):
            assert app_token == self.token
            calls.append((self.token, "apps.connections.open"))
            return {"url": "wss://secret-connection"}

    results = check(slack, worker, Client)
    assert all(item["ok"] for item in results)
    assert "secret-connection" not in str(results)
    assert calls == [("bot", "auth.test"), ("user", "auth.test"), ("app", "apps.connections.open")]


def test_check_reports_missing_tokens_without_network(tmp_path):
    slack = tmp_path / "slack.env"
    worker = tmp_path / "worker.env"
    write_env(slack, {"TACIT_TEAM_ID": "T1"})
    write_env(worker, {"TACIT_TEAM_ID": "T1", "TACIT_OWNER": "U1"})
    results = check(slack, worker, lambda **kwargs: None)
    assert len([item for item in results if not item["ok"]]) == 3


@pytest.mark.parametrize("actual_owner,ok", [("U1", True), ("OTHER", False)])
def test_peer_can_check_own_identity_without_connector_tokens(tmp_path, actual_owner, ok):
    worker = tmp_path / "peer.env"
    write_env(worker, {"TACIT_TEAM_ID": "T1", "TACIT_OWNER": "U1", "SLACK_USER_TOKEN": "user"})

    class Response(dict):
        headers = {"x-oauth-scopes": "chat:write,im:write"}

    class Client:
        def __init__(self, token, timeout):
            assert token == "user"

        def auth_test(self):
            return Response(team_id="T1", user_id=actual_owner)

        def apps_connections_open(self, **kwargs):
            pytest.fail("Peer must not request a Socket Mode connection")

    findings = check(tmp_path / "missing-slack.env", worker, Client, worker_only=True)
    assert [finding["check"] for finding in findings] == ["configuration", "SLACK_USER_TOKEN"]
    assert findings[-1]["ok"] is ok
