import json

import httpx
import pytest
from dotenv import dotenv_values

from tacit.provision import ProvisionError, provision
from tacit.setup import write_env


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    monkeypatch.delenv("SLACK_CONFIG_TOKEN", raising=False)
    monkeypatch.delenv("TACIT_TEAM_ID", raising=False)
    env = tmp_path / "slack.env"
    write_env(env, {"SLACK_CONFIG_TOKEN": "test-config-secret", "TACIT_TEAM_ID": "T1"})
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"display_information":{"name":"Tacit"}}')
    return env, manifest


def test_create_validates_first_and_never_creates_twice(inputs):
    env, manifest = inputs
    calls = []

    def handler(request):
        calls.append(request.url.path)
        assert request.headers["Authorization"] == "Bearer test-config-secret"
        body = json.loads(request.content)
        assert json.loads(body["manifest"])["display_information"]["name"] == "Tacit"
        if request.url.path.endswith("validate"):
            return httpx.Response(200, json={"ok": True})
        assert body["team_id"] == "T1"
        return httpx.Response(
            200,
            json={
                "ok": True,
                "app_id": "A123",
                "credentials": {"client_secret": "private-response-secret"},
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        first = provision(env, manifest, "create", client)
        second = provision(env, manifest, "create", client)
    assert calls == ["/api/apps.manifest.validate", "/api/apps.manifest.create"]
    assert first["created"] and not second["created"]
    assert "private-response-secret" not in json.dumps(first)
    assert dotenv_values(env)["SLACK_APP_ID"] == "A123"
    saved = env.with_name("slack-app.json")
    assert saved.stat().st_mode & 0o777 == 0o600
    assert (
        json.loads(saved.read_text())["credentials"]["client_secret"] == "private-response-secret"
    )


def test_invalid_manifest_does_not_create_app(inputs):
    env, manifest = inputs
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(
            200,
            json={
                "ok": False,
                "error": "invalid_manifest",
                "errors": [{"pointer": "/settings/interactivity", "message": "not echoed"}],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProvisionError, match="invalid_manifest.*interactivity"):
            provision(env, manifest, "create", client)
    assert calls == ["/api/apps.manifest.validate"]
    assert not env.with_name("slack-app.json").exists()


def test_unknown_creation_result_requires_reconciliation(inputs):
    env, manifest = inputs

    def handler(request):
        if request.url.path.endswith("validate"):
            return httpx.Response(200, json={"ok": True})
        raise httpx.ReadTimeout("private transport details", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProvisionError, match="network or response error"):
            provision(env, manifest, "create", client)
        with pytest.raises(ProvisionError, match="unknown outcome"):
            provision(env, manifest, "create", client)


def test_missing_token_does_not_call_slack(inputs):
    env, manifest = inputs
    env.write_text('TACIT_TEAM_ID="T1"\n')

    def handler(request):
        pytest.fail("Must not call Slack without credentials")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProvisionError, match="Set SLACK_CONFIG_TOKEN"):
            provision(env, manifest, "create", client)
