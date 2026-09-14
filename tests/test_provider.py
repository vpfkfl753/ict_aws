from types import SimpleNamespace

import pytest

from tacit.provider import ACPProvider, CodexProvider, create_provider


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
