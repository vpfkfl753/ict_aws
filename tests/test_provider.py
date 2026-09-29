from types import SimpleNamespace

import httpx
import pytest

from tacit.provider import (
    ACPProvider,
    BedrockProvider,
    CodexProvider,
    OpenAIProvider,
    create_provider,
)


@pytest.mark.parametrize("backend", ["kiro", "opencode"])
def test_acp_provider_preserves_evidence_and_starts_fresh_sessions(monkeypatch, tmp_path, backend):
    (tmp_path / "evidence.md").write_text("B17 uses p2", encoding="utf-8")
    calls = []

    class Runtime:
        def __init__(self, selected, workspace, *, model):
            calls.append((selected, workspace, model))

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            calls.append("closed")

        async def prompt(self, request):
            assert "B17 uses p2" in request
            assert "evidence.md" in request
            assert "only local evidence" in request
            return SimpleNamespace(text=" response ", stop_reason="end_turn")

    monkeypatch.setattr("tacit.provider.AgentRuntime", Runtime)
    model = "openai/test" if backend == "opencode" else None
    provider = create_provider(tmp_path, backend, model)
    assert isinstance(provider, ACPProvider)
    assert provider.run("first") == "response"
    assert provider.run("unrelated peer") == "response"
    assert calls == [(backend, tmp_path, model), "closed"] * 2


@pytest.mark.parametrize(
    "text,reason", [("partial", "max_tokens"), ("", "end_turn"), ("a" * 24001, "end_turn")]
)
def test_acp_provider_does_not_publish_invalid_results(monkeypatch, tmp_path, text, reason):
    class Runtime:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def prompt(self, request):
            return SimpleNamespace(text=text, stop_reason=reason)

    monkeypatch.setattr("tacit.provider.AgentRuntime", Runtime)
    with pytest.raises(RuntimeError):
        create_provider(tmp_path, "kiro").run("question")


def test_factory_preserves_codex_default_and_rejects_bad_configuration(tmp_path):
    assert isinstance(create_provider(tmp_path), CodexProvider)
    with pytest.raises(ValueError):
        create_provider(tmp_path, "unknown")
    with pytest.raises(ValueError):
        create_provider(tmp_path, "opencode")


def test_product_evidence_does_not_implicitly_scan_files_or_trust_runtime_tools(
    monkeypatch, tmp_path
):
    class Runtime:
        def __init__(self, backend, workspace, *, model, allow_read):
            assert allow_read is False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def prompt(self, request):
            assert "ONLY_SELECTED" in request
            assert "NOT_SELECTED" not in request
            return SimpleNamespace(text="result", stop_reason="end_turn")

    (tmp_path / "unselected.md").write_text("NOT_SELECTED")
    monkeypatch.setattr("tacit.provider.AgentRuntime", Runtime)
    assert (
        ACPProvider(tmp_path, "kiro").run_evidence(
            "question", [{"path": "selected.md", "content": "ONLY_SELECTED"}]
        )
        == "result"
    )


class FakeBedrock:
    def __init__(self, response):
        self.response, self.calls = response, []

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def bedrock(monkeypatch, tmp_path, response):
    fake = FakeBedrock(response)
    monkeypatch.setattr("tacit.provider.boto3.client", lambda *args, **kwargs: fake)
    return create_provider(tmp_path, "bedrock"), fake


def test_bedrock_provider_joins_text_and_uses_default_model(monkeypatch, tmp_path):
    (tmp_path / "evidence.md").write_text("B17 uses p2", encoding="utf-8")
    content = [{"text": " first "}, {"reasoningContent": {}}, {"text": "second "}]
    response = {"stopReason": "end_turn", "output": {"message": {"content": content}}}
    provider, fake = bedrock(monkeypatch, tmp_path, response)
    assert isinstance(provider, BedrockProvider)
    assert provider.backend == "bedrock"
    assert provider.run("question") == "first second"
    call = fake.calls[0]
    assert call["modelId"] == "global.anthropic.claude-opus-4-6-v1"
    assert call["inferenceConfig"] == {"maxTokens": 4096}
    assert "B17 uses p2" in call["messages"][0]["content"][0]["text"]


@pytest.mark.parametrize(
    "response",
    [
        {"stopReason": "max_tokens", "output": {"message": {"content": [{"text": "partial"}]}}},
        {"stopReason": "end_turn", "output": {"message": {"content": []}}},
    ],
)
def test_bedrock_provider_rejects_truncated_or_empty_results(monkeypatch, tmp_path, response):
    provider, _ = bedrock(monkeypatch, tmp_path, response)
    with pytest.raises(RuntimeError):
        provider.run_evidence("question", [])


def test_bedrock_errors_do_not_leak_prompt(monkeypatch, tmp_path):
    from botocore.exceptions import ClientError

    class Failing:
        def converse(self, **kwargs):
            raise ClientError({"Error": {"Code": "AccessDeniedException"}}, "Converse")

    monkeypatch.setattr("tacit.provider.boto3.client", lambda *args, **kwargs: Failing())
    with pytest.raises(RuntimeError) as error:
        create_provider(tmp_path, "bedrock").run("SECRET_PROMPT")
    assert "AccessDeniedException" in str(error.value)
    assert "SECRET_PROMPT" not in str(error.value)


def gateway(monkeypatch, body, status=200):
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return httpx.Response(status, json=body, request=httpx.Request("POST", url))

    monkeypatch.setenv("TACIT_OPENAI_BASE_URL", "https://gateway.example/v1/")
    monkeypatch.setenv("TACIT_OPENAI_API_KEY", "test-key")
    monkeypatch.setattr("tacit.provider.httpx.post", post)
    return calls


def test_openai_provider_posts_chat_completion(monkeypatch, tmp_path):
    calls = gateway(
        monkeypatch, {"choices": [{"finish_reason": "stop", "message": {"content": " ok "}}]}
    )
    provider = create_provider(tmp_path, "openai", "gpt-test")
    assert isinstance(provider, OpenAIProvider)
    assert provider.run_evidence("question", [{"path": "a.md", "content": "A"}]) == "ok"
    url, kwargs = calls[0]
    assert url == "https://gateway.example/v1/chat/completions"
    assert kwargs["headers"] == {"Authorization": "Bearer test-key"}
    assert kwargs["json"]["model"] == "gpt-test"
    assert kwargs["json"]["max_tokens"] == 4096


@pytest.mark.parametrize(
    "body,status",
    [
        ({"choices": [{"finish_reason": "length", "message": {"content": "partial"}}]}, 200),
        ({"choices": [{"finish_reason": "stop", "message": {"content": ""}}]}, 200),
        ({"error": "denied"}, 401),
    ],
)
def test_openai_provider_rejects_invalid_results_without_leaking_key(
    monkeypatch, tmp_path, body, status
):
    gateway(monkeypatch, body, status)
    with pytest.raises(RuntimeError) as error:
        create_provider(tmp_path, "openai", "gpt-test").run("question")
    assert "test-key" not in str(error.value)


@pytest.mark.parametrize("missing", ["TACIT_OPENAI_BASE_URL", "TACIT_OPENAI_API_KEY", "model"])
def test_openai_provider_requires_configuration(monkeypatch, tmp_path, missing):
    gateway(monkeypatch, {})
    if missing != "model":
        monkeypatch.delenv(missing)
    with pytest.raises(ValueError):
        create_provider(tmp_path, "openai", None if missing == "model" else "gpt-test")


def test_bedrock_falls_back_to_next_model_only_for_availability_errors(monkeypatch, tmp_path):
    from botocore.exceptions import ClientError

    calls = []

    class Flaky:
        def converse(self, **kwargs):
            calls.append(kwargs["modelId"])
            if kwargs["modelId"] == "claude":
                raise ClientError({"Error": {"Code": "AccessDeniedException"}}, "Converse")
            if kwargs["modelId"] == "broken":
                raise ClientError({"Error": {"Code": "ValidationException"}}, "Converse")
            return {"stopReason": "end_turn", "output": {"message": {"content": [{"text": "ok"}]}}}

    monkeypatch.setattr("tacit.provider.boto3.client", lambda *args, **kwargs: Flaky())
    assert create_provider(tmp_path, "bedrock", "claude, nova").run_evidence("q", []) == "ok"
    assert calls == ["claude", "nova"]
    with pytest.raises(RuntimeError, match="ValidationException"):
        create_provider(tmp_path, "bedrock", "broken,nova").run_evidence("q", [])
    assert calls[-1] == "broken"
