from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from enan_ultra.config import Settings
from enan_ultra.services.key_rotation import KeyRotation
from enan_ultra.services.provider_errors import (
    ProviderError, error_from_response, install_attempt_budget,
    remaining_provider_attempts, reset_attempt_budget, retry_after_seconds,
)
from enan_ultra.services.providers import OpenAICompatibleAdapter, ProviderRouter


APP_KEY = "test-only-app-data-key-with-sufficient-entropy-123456789"


def _settings_env(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    for name, value in {
        "TELEGRAM_BOT_TOKEN": "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi",
        "ADMIN_CHAT_ID": "123456",
        "ADMIN_USER_IDS": "123456",
        "GEMINI_API_KEY": "test-gemini-key",
        "GEMINI_MODEL": "test-gemini-model",
        "CLOUDFLARE_ACCOUNT_ID": "0123456789abcdef0123456789abcdef",
        "CLOUDFLARE_MODEL": "@cf/test-model",
        "APP_DATA_KEY": APP_KEY,
        "DASHBOARD_AUTH_TOKEN": "test-only-dashboard-auth-token-1234567890abcdef",
    }.items():
        monkeypatch.setenv(name, value)


def test_settings_provider_policy_and_aliases(monkeypatch, tmp_path: Path) -> None:
    _settings_env(monkeypatch, tmp_path)
    monkeypatch.setenv("AI_PROVIDER_ORDER", "gemini,openrouter,groq")
    monkeypatch.setenv("AI_FREE_ONLY", "true")
    monkeypatch.setenv("AI_FREE_MODEL_ALLOWLIST", "gemini::test-free-model,groq::free-chat")
    monkeypatch.setenv("CLOUDFLARE_API_KEYS", "legacy-one, legacy-two")
    settings = Settings.load()
    assert settings.provider_order == ("gemini", "openrouter", "groq")
    assert settings.ai_free_only is True
    assert settings.ai_free_model_allowlist == (
        "gemini::test-free-model", "groq::free-chat",
    )
    assert settings.cloudflare_keys == ("legacy-one", "legacy-two")

    monkeypatch.setenv("AI_PROVIDER_ORDER", "gemini,unknown-provider")
    with pytest.raises(ValueError, match="AI_PROVIDER_ORDER"):
        Settings.load()


def test_paid_fallback_requires_positive_estimated_cost_cap(monkeypatch, tmp_path: Path) -> None:
    _settings_env(monkeypatch, tmp_path)
    monkeypatch.setenv("AI_FREE_ONLY", "true")
    monkeypatch.setenv("AI_ALLOW_PAID_FALLBACK", "true")
    monkeypatch.setenv("AI_MAX_ESTIMATED_COST_USD", "0")
    with pytest.raises(ValueError, match="AI_MAX_ESTIMATED_COST_USD"):
        Settings.load()


def test_clamav_configuration_is_opt_in_and_validates_socket_path(monkeypatch, tmp_path: Path) -> None:
    _settings_env(monkeypatch, tmp_path)
    settings = Settings.load()
    assert settings.malware_scan_enabled is False
    assert settings.clamav_socket_path == Path("/var/run/clamav/clamd.ctl")

    monkeypatch.setenv("MALWARE_SCAN_ENABLED", "true")
    monkeypatch.setenv("CLAMAV_SOCKET_PATH", "relative/clamd.ctl")
    with pytest.raises(ValueError, match="CLAMAV_SOCKET_PATH"):
        Settings.load()


def test_strict_free_allowlist_and_paid_fallback_cost_gate() -> None:
    async def scenario() -> None:
        router = object.__new__(ProviderRouter)
        router.settings = SimpleNamespace(
            ai_free_only=True,
            ai_allow_paid_fallback=False,
            ai_free_model_allowlist=("groq::free-chat",),
            ai_max_estimated_cost_usd=0,
            provider_request_reserve_usd=0.10,
        )
        assert not await router._model_allowed(
            "groq", "paid-chat", input_chars=20, max_tokens=40,
            modality="text", image_count=0,
        )
        assert await router._model_allowed(
            "groq", "free-chat", input_chars=20, max_tokens=40,
            modality="text", image_count=0,
        )

        router.settings.ai_allow_paid_fallback = True
        router.settings.ai_max_estimated_cost_usd = 0.05
        assert not await router._model_allowed(
            "groq", "paid-chat", input_chars=20, max_tokens=40,
            modality="text", image_count=0,
        )
        router.settings.provider_request_reserve_usd = 0.02
        assert await router._model_allowed(
            "groq", "paid-chat", input_chars=20, max_tokens=40,
            modality="text", image_count=0,
        )

    asyncio.run(scenario())


def test_provider_attempt_budget_is_shared_and_bounded() -> None:
    token = install_attempt_budget(2)
    try:
        from enan_ultra.services.provider_errors import consume_provider_attempt

        assert remaining_provider_attempts() == 2
        assert consume_provider_attempt() is True
        assert consume_provider_attempt() is True
        assert consume_provider_attempt() is False
        assert remaining_provider_attempts() == 0
    finally:
        reset_attempt_budget(token)
    assert remaining_provider_attempts() is None


def test_retry_after_supports_seconds_dates_and_provider_duration_headers() -> None:
    assert retry_after_seconds({"retry-after": "7"}) == 7
    assert retry_after_seconds({"retry-after-ms": "1250"}) == 1.25
    assert retry_after_seconds({"x-ratelimit-reset-requests": "2m59.5s"}) == 179.5
    assert retry_after_seconds({"retry-after": "not-a-date"}) is None


def test_openrouter_stream_preserves_usage_from_sse_events(monkeypatch, tmp_path: Path) -> None:
    from enan_ultra.services.openrouter import OpenRouter

    async def scenario() -> None:
        _settings_env(monkeypatch, tmp_path)
        client = OpenRouter(Settings.load())
        response = httpx.Response(
            200,
            text=(
                'data: {"choices":[{"delta":{"content":"streamed answer"}}]}\n\n'
                'data: {"choices":[],"usage":{"prompt_tokens":7,"completion_tokens":3}}\n\n'
                "data: [DONE]\n\n"
            ),
        )

        async def stream_request(method, path, consume, **kwargs):
            return await consume(response)

        monkeypatch.setattr(client, "_stream_request", stream_request)
        tokens: list[str] = []

        async def on_token(token: str) -> None:
            tokens.append(token)

        result = await client._completion_for_model(
            "test-model", [{"role": "user", "content": "test"}], [], 50, on_token,
        )
        assert result.text == "streamed answer"
        assert result.usage == {"prompt_tokens": 7, "completion_tokens": 3}
        assert tokens == ["streamed answer"]

    asyncio.run(scenario())


def test_provider_http_error_is_sanitized_and_has_retry_metadata() -> None:
    request = httpx.Request("POST", "https://provider.example.test/chat")
    response = httpx.Response(
        429,
        headers={"Retry-After": "15", "x-request-id": "req-test-123"},
        json={"error": {"message": "secret prompt and private response"}},
        request=request,
    )
    error = error_from_response("test-provider", response)
    assert error.category == "rate_limited"
    assert error.retry_after == 15
    assert error.request_id == "req-test-123"
    assert "secret prompt" not in str(error)


def test_circuit_breaker_opens_after_threshold_and_resets_on_success() -> None:
    async def scenario() -> None:
        router = object.__new__(ProviderRouter)
        router.settings = SimpleNamespace(
            ai_circuit_breaker_threshold=2, ai_circuit_breaker_cooldown=60,
        )
        router._circuits = {}
        router._state_lock = asyncio.Lock()
        router.failure_notifier = None
        error = ProviderError("unavailable", category="temporary_unavailable")
        await router._record_failure("groq", error)
        assert not await router._circuit_is_open("groq")
        await router._record_failure("groq", error)
        assert await router._circuit_is_open("groq")
        await router._record_success("groq")
        assert not await router._circuit_is_open("groq")

    asyncio.run(scenario())


def test_web_search_route_obeys_estimated_cost_cap() -> None:
    class _Adapter:
        name = "openrouter"

        def configured_model(self) -> str:
            return "search-model"

        def capabilities(self) -> dict[str, object]:
            return {"input": ["text"], "output": ["text"], "tools": True,
                    "streaming": True, "local": False}

    class _OpenRouter:
        called = False

        async def estimate_model_cost(self, *_args, **_kwargs) -> float:
            return 0.20

        async def complete(self, *_args, **_kwargs):
            self.called = True
            raise AssertionError("cost rejection should happen before a provider request")

    async def scenario() -> None:
        router = object.__new__(ProviderRouter)
        router.settings = SimpleNamespace(
            provider_order=("openrouter",), default_model="auto", fallback_models=(),
            ai_free_only=False, ai_allow_paid_fallback=False,
            ai_free_model_allowlist=(), ai_max_estimated_cost_usd=0.10,
            ai_max_total_attempts=5, ai_request_timeout=5,
        )
        router.adapters = {"openrouter": _Adapter()}
        router.openrouter = _OpenRouter()
        with pytest.raises(ProviderError) as raised:
            await router.complete(
                [{"role": "user", "content": "search this"}], web_search=True,
            )
        assert raised.value.category == "cost_limit"
        assert router.openrouter.called is False

    asyncio.run(scenario())


def test_http_adapter_does_not_claim_unconfigured_capabilities() -> None:
    settings = SimpleNamespace(
        ai_tool_capable_models=("groq::tool-model",),
        ai_image_capable_models=(),
        ai_streaming_models=("groq::stream-model",),
        ai_request_timeout=5,
        ai_max_retries_per_key=0,
        ai_circuit_breaker_cooldown=60,
    )
    adapter = OpenAICompatibleAdapter(
        settings, "groq", "https://provider.example.test/v1", "", "default-model",
        keys=(),
    )
    try:
        assert adapter.capabilities("ordinary-model") == {
            "input": ["text"], "output": ["text"], "tools": False,
            "streaming": False, "local": False,
        }
        assert adapter.capabilities("tool-model")["tools"] is True
        assert adapter.capabilities("stream-model")["streaming"] is True
    finally:
        asyncio.run(adapter.close())


def test_quota_cooldown_prevents_sibling_key_rotation() -> None:
    async def scenario() -> None:
        rotation = KeyRotation("groq", ("first", "second"), quota_cooldown_seconds=60)
        attempted: list[str] = []

        async def limited(key: str | None, _index: int) -> None:
            attempted.append(key or "")
            raise ProviderError("quota exhausted", 402, retry_after=400)

        with pytest.raises(ProviderError, match="quota exhausted") as raised:
            await rotation.run(limited)
        assert raised.value.category == "quota_or_billing"
        assert attempted == ["first"]
        with pytest.raises(ProviderError, match="cooling down"):
            await rotation.run(limited)
        assert attempted == ["first"]
        assert rotation.diagnostics()["cooldown_category"] == "quota_or_billing"
        assert rotation.diagnostics()["cooldown_seconds"] >= 399

    asyncio.run(scenario())


def test_invalid_credential_quarantine_persists_only_a_fingerprint(tmp_path: Path) -> None:
    async def scenario() -> None:
        state_path = tmp_path / "provider-health.json"
        rotation = KeyRotation("groq", ("secret-test-key", "second"), state_path=state_path)

        async def reject(key: str | None, _index: int) -> None:
            if key == "secret-test-key":
                raise ProviderError("invalid key", 401)

        await rotation.run(reject)
        content = state_path.read_text(encoding="utf-8")
        assert "secret-test-key" not in content
        assert KeyRotation("groq", ("secret-test-key", "second"), state_path=state_path).diagnostics()[
            "quarantined_credentials"
        ] == 1

    asyncio.run(scenario())


def test_openrouter_diagnostics_read_the_underlying_credential_state() -> None:
    from enan_ultra.services.providers import _OpenRouterAdapter

    class _Client:
        key_rotation = KeyRotation("openrouter", ("one", "two"))

    async def scenario() -> None:
        router = object.__new__(ProviderRouter)
        router.adapters = {
            "openrouter": _OpenRouterAdapter(_Client(), SimpleNamespace(default_model="auto")),
        }
        diagnostics = await router.provider_diagnostics()
        assert diagnostics[0]["credentials"] == 2
        assert diagnostics[0]["available_credentials"] == 2
        assert diagnostics[0]["quarantined_credentials"] == 0

    asyncio.run(scenario())


def test_request_attempt_budget_stops_before_second_credential() -> None:
    async def scenario() -> None:
        settings = SimpleNamespace(
            groq_keys=("first", "second"), ai_max_retries_per_key=0,
            ai_request_timeout=5, ai_circuit_breaker_cooldown=60,
            database_path=None,
        )
        adapter = OpenAICompatibleAdapter(
            settings, "groq", "https://provider.example.test/v1", "", "test-model",
        )
        seen: list[str] = []

        async def respond(request: httpx.Request) -> httpx.Response:
            seen.append(request.headers.get("authorization", ""))
            return httpx.Response(401, request=request)

        await adapter.close()
        adapter.client = httpx.AsyncClient(
            base_url="https://provider.example.test/v1",
            transport=httpx.MockTransport(respond),
        )
        token = install_attempt_budget(1)
        try:
            with pytest.raises(ProviderError, match="attempt limit") as raised:
                await adapter._request("POST", "/chat/completions", json={"model": "test"})
            assert raised.value.category == "attempt_limit"
            assert seen == ["Bearer first"]
        finally:
            reset_attempt_budget(token)
            await adapter.close()

    asyncio.run(scenario())


def test_openrouter_quota_error_does_not_try_internal_model_fallback(monkeypatch, tmp_path: Path) -> None:
    from enan_ultra.services.openrouter import OpenRouter

    _settings_env(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    monkeypatch.setenv("FALLBACK_MODELS", "fallback-model")
    settings = Settings.load()

    async def scenario() -> None:
        client = OpenRouter(settings)
        requests: list[str] = []

        async def respond(request: httpx.Request) -> httpx.Response:
            requests.append(request.url.path)
            if request.url.path.endswith("/models"):
                return httpx.Response(200, json={"data": []}, request=request)
            return httpx.Response(429, headers={"Retry-After": "17"}, request=request)

        await client.close()
        client.client = httpx.AsyncClient(
            base_url=settings.openrouter_base_url,
            transport=httpx.MockTransport(respond),
        )
        try:
            with pytest.raises(ProviderError) as raised:
                await client.complete(
                    [{"role": "user", "content": "hello"}], primary_model="primary-model",
                )
            assert raised.value.category == "rate_limited"
            assert raised.value.retry_after == 17
            assert requests.count("/api/v1/chat/completions") == 1
        finally:
            await client.close()

    asyncio.run(scenario())
