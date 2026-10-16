"""Provider-neutral model routing for OpenRouter and direct API providers."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Any, Protocol

import httpx

from enan_ultra.config import Settings
from enan_ultra.models import AssistantResult
from enan_ultra.services.openrouter import OpenRouter, ProviderError
from enan_ultra.services.key_rotation import KeyRotation, provider_state_path
from enan_ultra.services.provider_errors import (
    consume_provider_attempt, error_from_response, install_attempt_budget,
    remaining_provider_attempts, reset_attempt_budget,
)

logger = logging.getLogger(__name__)
TokenCallback = Callable[[str], Awaitable[None]]


class ProviderAdapter(Protocol):
    name: str

    def capabilities(self) -> dict[str, Any]: ...
    def configured_model(self) -> str: ...
    async def complete(
        self, model: str, messages: list[dict[str, Any]], *, tools: list[dict[str, Any]] | None,
        max_tokens: int, on_token: TokenCallback | None = None,
    ) -> AssistantResult: ...
    async def list_models(self) -> list[str]: ...
    async def health(self) -> dict[str, Any]: ...
    async def stream(
        self, model: str, messages: list[dict[str, Any]], *, tools: list[dict[str, Any]] | None,
        max_tokens: int, on_token: TokenCallback | None = None,
    ) -> AssistantResult: ...
    async def close(self) -> None: ...


class _HttpAdapter:
    name = ""

    def __init__(
        self, settings: Settings, base_url: str, headers: dict[str, str],
        keys: tuple[str, ...] = (), key_header: str | None = None,
    ) -> None:
        self.settings = settings
        self.key_rotation = KeyRotation(
            self.name, keys, max_retries_per_key=getattr(settings, "ai_max_retries_per_key", 1),
            state_path=provider_state_path(settings, self.name) if self.name else None,
            quota_cooldown_seconds=getattr(settings, "ai_circuit_breaker_cooldown", 60),
        )
        self.key_header = key_header
        request_timeout = float(getattr(settings, "ai_request_timeout", 30))
        self.client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Content-Type": "application/json", **headers},
            timeout=httpx.Timeout(
                connect=min(8, request_timeout), read=request_timeout,
                write=request_timeout, pool=10,
            ),
            follow_redirects=False,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=5),
        )
        self.last_health: tuple[float, dict[str, Any]] = (0.0, {"status": "unknown"})

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        original_headers = dict(kwargs.pop("headers", {}) or {})

        async def request(key: str | None, _key_index: int) -> httpx.Response:
            if method.upper() == "POST" and not consume_provider_attempt():
                raise ProviderError("The per-request provider attempt limit was reached", category="attempt_limit")
            headers = dict(original_headers)
            if key and self.key_header:
                headers[self.key_header] = self._credential_header_value(key)
            response = await self.client.request(method, url, headers=headers, **kwargs)
            if response.status_code >= 400:
                raise error_from_response(self.name, response)
            return response

        return await self.key_rotation.run(request)

    async def _stream_request(
        self, method: str, url: str, consume: Callable[[httpx.Response], Awaitable[Any]],
        **kwargs: Any,
    ) -> Any:
        original_headers = dict(kwargs.pop("headers", {}) or {})

        async def request(key: str | None, _key_index: int) -> Any:
            if method.upper() == "POST" and not consume_provider_attempt():
                raise ProviderError("The per-request provider attempt limit was reached", category="attempt_limit")
            headers = dict(original_headers)
            if key and self.key_header:
                headers[self.key_header] = self._credential_header_value(key)
            async with self.client.stream(method, url, headers=headers, **kwargs) as response:
                if response.status_code >= 400:
                    raise error_from_response(self.name, response)
                return await consume(response)

        return await self.key_rotation.run(request)

    def _credential_header_value(self, key: str) -> str:
        return f"Bearer {key}" if self.key_header == "Authorization" else key

    async def close(self) -> None:
        await self.client.aclose()

    def capabilities(self, model: str | None = None) -> dict[str, Any]:
        qualified = f"{self.name}::{model or self.configured_model()}".casefold()
        tools = qualified in {item.casefold() for item in getattr(self.settings, "ai_tool_capable_models", ())}
        images = qualified in {item.casefold() for item in getattr(self.settings, "ai_image_capable_models", ())}
        streaming = (
            self.name != "gemini"
            and qualified in {item.casefold() for item in getattr(self.settings, "ai_streaming_models", ())}
        )
        inputs = ["text"]
        if images:
            inputs.append("image")
        return {
            "input": inputs, "output": ["text"], "tools": tools,
            "streaming": streaming, "local": self.name == "ollama",
        }

    async def stream(
        self, model: str, messages: list[dict[str, Any]], *, tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 1400, on_token: TokenCallback | None = None,
    ) -> AssistantResult:
        """Stream text deltas through the callback and return the final result."""
        return await self.complete(model, messages, tools=tools, max_tokens=max_tokens, on_token=on_token)

    async def health(self) -> dict[str, Any]:
        now = time.monotonic()
        if now - self.last_health[0] < 30:
            return dict(self.last_health[1])
        try:
            models = await self.list_models()
            state = {
                "status": "healthy", "models": len(models),
                "probe_latency_ms": round((time.monotonic() - now) * 1000),
            }
        except Exception as exc:
            state = {
                "status": "unavailable", "error": type(exc).__name__,
                "probe_latency_ms": round((time.monotonic() - now) * 1000),
            }
        self.last_health = (now, state)
        return dict(state)

    @staticmethod
    def _text_and_images(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return messages

    @staticmethod
    def _tool_calls(message: dict[str, Any]) -> tuple[dict[str, Any], ...]:
        calls = []
        for item in message.get("tool_calls") or []:
            function = item.get("function") or {}
            calls.append({
                "id": str(item.get("id") or ""),
                "type": "function",
                "function": {
                    "name": str(function.get("name") or ""),
                    "arguments": function.get("arguments") if isinstance(function.get("arguments"), str)
                    else json.dumps(function.get("arguments") or {}, ensure_ascii=False),
                },
            })
        return tuple(calls)


class OpenAICompatibleAdapter(_HttpAdapter):
    """OpenAI-compatible provider endpoint adapter."""

    def __init__(
        self, settings: Settings, name: str, base_url: str, key: str, model: str,
        *, keys: tuple[str, ...] | None = None,
    ) -> None:
        headers = {"Authorization": f"Bearer {key or 'ollama'}"}
        super().__init__(settings, base_url, headers)
        self.name = name
        self.client.headers.pop("Authorization", None)
        configured_keys = keys if keys is not None else getattr(settings, f"{name}_keys", ())
        self.key_rotation = KeyRotation(
            name, configured_keys or ((key,) if key else ()),
            max_retries_per_key=getattr(settings, "ai_max_retries_per_key", 1),
            state_path=provider_state_path(settings, name),
            quota_cooldown_seconds=getattr(settings, "ai_circuit_breaker_cooldown", 60),
        )
        self.key_header = "Authorization" if name != "ollama" else None
        self._model = model

    def configured_model(self) -> str:
        return self._model

    async def list_models(self) -> list[str]:
        response = await self._request("GET", "/models")
        response.raise_for_status()
        payload = response.json()
        return [str(item.get("id")) for item in payload.get("data", []) if item.get("id")]

    async def complete(
        self, model: str, messages: list[dict[str, Any]], *, tools: list[dict[str, Any]] | None,
        max_tokens: int, on_token: TokenCallback | None = None,
    ) -> AssistantResult:
        payload: dict[str, Any] = {"model": model, "messages": messages, "max_tokens": max_tokens}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
            payload["parallel_tool_calls"] = False
        if on_token and not tools:
            payload["stream"] = True
            if self.name == "openai":
                payload["stream_options"] = {"include_usage": True}
            async def consume(response: httpx.Response) -> AssistantResult:
                self._check(response)
                pieces: list[str] = []
                usage: dict[str, Any] = {}
                try:
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        raw = line[5:].strip()
                        if not raw or raw == "[DONE]":
                            continue
                        try:
                            body = json.loads(raw)
                            usage = body.get("usage") or usage
                            if "prompt_eval_count" in body or "eval_count" in body:
                                usage = {"input_tokens": body.get("prompt_eval_count", 0),
                                         "output_tokens": body.get("eval_count", 0)}
                            delta = ((body.get("choices") or [{}])[0].get("delta") or {})
                            token = delta.get("content")
                        except (ValueError, TypeError, IndexError):
                            continue
                        if isinstance(token, str) and token:
                            pieces.append(token)
                            await on_token(token)
                except Exception as exc:
                    if pieces:
                        try:
                            setattr(exc, "partial_output", True)
                        except Exception:
                            pass
                    raise
                text = "".join(pieces).strip()
                if not text:
                    raise ProviderError(f"{self.name} returned an empty stream")
                return AssistantResult(text=text, model=f"{self.name}::{model}", usage=usage)

            return await self._stream_request("POST", "/chat/completions", consume, json=payload)
        response = await self._request("POST", "/chat/completions", json=payload)
        self._check(response)
        body = response.json()
        choices = body.get("choices") or []
        if not choices:
            raise ProviderError(f"{self.name} returned no choices")
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, list):
            content = "".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
        result = AssistantResult(
            text=str(content or "").strip(), model=f"{self.name}::{body.get('model') or model}",
            usage=body.get("usage") or {}, tool_calls=self._tool_calls(message),
        )
        if on_token and result.text:
            await on_token(result.text)
        return result

    def _check(self, response: httpx.Response) -> None:
        if response.status_code >= 400:
            raise error_from_response(self.name, response)


class AnthropicAdapter(_HttpAdapter):
    name = "anthropic"
    _api_version = "2023-06-01"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings, settings.anthropic_base_url, {
            "anthropic-version": self._api_version,
        })
        self.key_rotation = KeyRotation(
            "anthropic", settings.anthropic_keys,
            max_retries_per_key=getattr(settings, "ai_max_retries_per_key", 1),
            state_path=provider_state_path(settings, "anthropic"),
            quota_cooldown_seconds=getattr(settings, "ai_circuit_breaker_cooldown", 60),
        )
        self.key_header = "x-api-key"

    def configured_model(self) -> str:
        return self.settings.anthropic_model

    async def list_models(self) -> list[str]:
        response = await self._request("GET", "/v1/models", params={"limit": 100})
        self._check(response)
        return [str(item.get("id")) for item in response.json().get("data", []) if item.get("id")]

    async def complete(
        self, model: str, messages: list[dict[str, Any]], *, tools: list[dict[str, Any]] | None,
        max_tokens: int, on_token: TokenCallback | None = None,
    ) -> AssistantResult:
        system_parts: list[str] = []
        converted: list[dict[str, Any]] = []
        for item in messages:
            role = str(item.get("role", "user"))
            content = item.get("content", "")
            if role == "system":
                if isinstance(content, str):
                    system_parts.append(content)
                continue
            if role == "tool":
                converted.append({"role": "user", "content": [{
                    "type": "tool_result", "tool_use_id": item.get("tool_call_id", ""),
                    "content": str(content),
                }]})
                continue
            if role == "assistant" and item.get("tool_calls"):
                blocks: list[dict[str, Any]] = []
                if content:
                    blocks.append({"type": "text", "text": str(content)})
                for call in item.get("tool_calls") or []:
                    function = call.get("function") or {}
                    try:
                        args = json.loads(function.get("arguments") or "{}")
                    except (ValueError, TypeError):
                        args = {}
                    blocks.append({"type": "tool_use", "id": call.get("id", ""), "name": function.get("name", ""), "input": args})
                converted.append({"role": "assistant", "content": blocks})
                continue
            converted.append({"role": "assistant" if role == "assistant" else "user", "content": self._anthropic_content(content)})
        request: dict[str, Any] = {"model": model, "max_tokens": max_tokens, "messages": converted}
        if system_parts:
            request["system"] = "\n\n".join(system_parts)
        if tools:
            request["tools"] = [{
                "name": tool["function"]["name"], "description": tool["function"].get("description", ""),
                "input_schema": tool["function"].get("parameters", {"type": "object", "properties": {}}),
            } for tool in tools if tool.get("type") == "function"]
        response = await self._request("POST", "/v1/messages", json=request)
        self._check(response)
        body = response.json()
        text_parts: list[str] = []
        calls: list[dict[str, Any]] = []
        for index, block in enumerate(body.get("content") or []):
            if block.get("type") == "text":
                text_parts.append(str(block.get("text", "")))
            elif block.get("type") == "tool_use":
                calls.append({
                    "id": str(block.get("id") or f"anthropic-{index}"), "type": "function",
                    "function": {"name": str(block.get("name") or ""),
                                 "arguments": json.dumps(block.get("input") or {}, ensure_ascii=False)},
                })
        result = AssistantResult(
            text="\n".join(text_parts).strip(), model=f"anthropic::{body.get('model') or model}",
            usage={"input_tokens": (body.get("usage") or {}).get("input_tokens", 0),
                   "output_tokens": (body.get("usage") or {}).get("output_tokens", 0)},
            tool_calls=tuple(calls),
        )
        if on_token and result.text:
            await on_token(result.text)
        return result

    @staticmethod
    def _anthropic_content(content: Any) -> Any:
        if isinstance(content, str):
            return content
        if not isinstance(content, list):
            return str(content)
        blocks: list[dict[str, Any]] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text":
                blocks.append({"type": "text", "text": str(part.get("text", ""))})
            elif part.get("type") == "image_url":
                url = str((part.get("image_url") or {}).get("url", ""))
                if url.startswith("data:") and ";base64," in url:
                    header, encoded = url[5:].split(";base64,", 1)
                    blocks.append({"type": "image", "source": {
                        "type": "base64", "media_type": header, "data": encoded,
                    }})
        return blocks or ""

    @staticmethod
    def _check(response: httpx.Response) -> None:
        if response.status_code >= 400:
            raise error_from_response("Anthropic", response)


class GeminiAdapter(_HttpAdapter):
    name = "gemini"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings, settings.gemini_base_url, {})
        self.key_rotation = KeyRotation(
            "gemini", settings.gemini_keys,
            max_retries_per_key=getattr(settings, "ai_max_retries_per_key", 1),
            state_path=provider_state_path(settings, "gemini"),
            quota_cooldown_seconds=getattr(settings, "ai_circuit_breaker_cooldown", 60),
        )
        self.key_header = "x-goog-api-key"

    def configured_model(self) -> str:
        return self.settings.gemini_model

    async def list_models(self) -> list[str]:
        response = await self._request("GET", "/models")
        self._check(response)
        models = response.json().get("models", [])
        return [str(item.get("name", "")).removeprefix("models/") for item in models
                if "generateContent" in item.get("supportedGenerationMethods", []) and item.get("name")]

    async def complete(
        self, model: str, messages: list[dict[str, Any]], *, tools: list[dict[str, Any]] | None,
        max_tokens: int, on_token: TokenCallback | None = None,
    ) -> AssistantResult:
        system_parts: list[str] = []
        contents: list[dict[str, Any]] = []
        for item in messages:
            role = str(item.get("role", "user"))
            content = item.get("content", "")
            if role == "system":
                if isinstance(content, str):
                    system_parts.append(content)
                continue
            if role == "tool":
                try:
                    tool_result = json.loads(str(content))
                except (ValueError, TypeError):
                    tool_result = {"result": str(content)}
                contents.append({"role": "user", "parts": [{"functionResponse": {
                    "name": str(item.get("name", "tool")), "response": tool_result,
                }}]})
                continue
            parts: list[dict[str, Any]] = []
            if isinstance(content, str):
                parts.append({"text": content})
            elif isinstance(content, list):
                for part in content:
                    if part.get("type") == "text":
                        parts.append({"text": str(part.get("text", ""))})
                    elif part.get("type") == "image_url":
                        url = str((part.get("image_url") or {}).get("url", ""))
                        if url.startswith("data:") and ";base64," in url:
                            mime, encoded = url[5:].split(";base64,", 1)
                            parts.append({"inlineData": {"mimeType": mime, "data": encoded}})
            if role == "assistant" and item.get("tool_calls"):
                for call in item.get("tool_calls") or []:
                    function = call.get("function") or {}
                    try:
                        args = json.loads(function.get("arguments") or "{}")
                    except (ValueError, TypeError):
                        args = {}
                    parts.append({"functionCall": {"name": function.get("name", ""), "args": args}})
            contents.append({"role": "model" if role == "assistant" else "user", "parts": parts})
        request: dict[str, Any] = {"contents": contents, "generationConfig": {"maxOutputTokens": max_tokens}}
        if system_parts:
            request["systemInstruction"] = {"parts": [{"text": "\n\n".join(system_parts)}]}
        if tools:
            request["tools"] = [{"functionDeclarations": [{
                "name": tool["function"]["name"], "description": tool["function"].get("description", ""),
                "parameters": tool["function"].get("parameters", {"type": "object", "properties": {}}),
            } for tool in tools if tool.get("type") == "function"]}]
        response = await self._request("POST", f"/models/{model}:generateContent", json=request)
        self._check(response)
        body = response.json()
        candidate = (body.get("candidates") or [{}])[0]
        text_parts: list[str] = []
        calls: list[dict[str, Any]] = []
        for index, part in enumerate((candidate.get("content") or {}).get("parts") or []):
            if isinstance(part.get("text"), str):
                text_parts.append(part["text"])
            function_call = part.get("functionCall")
            if isinstance(function_call, dict):
                calls.append({
                    "id": f"gemini-{index}", "type": "function",
                    "function": {"name": function_call.get("name", ""),
                                 "arguments": json.dumps(function_call.get("args") or {}, ensure_ascii=False)},
                })
        usage = body.get("usageMetadata") or {}
        result = AssistantResult(
            text="\n".join(text_parts).strip(), model=f"gemini::{model}",
            usage={"input_tokens": usage.get("promptTokenCount", 0),
                   "output_tokens": usage.get("candidatesTokenCount", 0)},
            tool_calls=tuple(calls),
        )
        if on_token and result.text:
            await on_token(result.text)
        return result

    @staticmethod
    def _check(response: httpx.Response) -> None:
        if response.status_code >= 400:
            raise error_from_response("Gemini", response)


@dataclass(slots=True)
class _CircuitState:
    failures: int = 0
    opened_until: float = 0.0
    last_category: str = ""


class ProviderRouter:
    """Provider facade; OpenRouter keeps media/file operations, adapters route chat/tools."""

    def __init__(self, settings: Settings, openrouter: OpenRouter | None) -> None:
        self.settings = settings
        self.openrouter = openrouter
        self.adapters: dict[str, ProviderAdapter] = {}
        self._circuits: dict[str, _CircuitState] = {}
        self._state_lock = asyncio.Lock()
        self.failure_notifier: Callable[[dict[str, Any]], Any] | None = None
        if settings.openrouter_keys and openrouter:
            self.adapters["openrouter"] = _OpenRouterAdapter(openrouter, settings)
        if settings.gemini_keys:
            self.adapters["gemini"] = GeminiAdapter(settings)
        for name in ("groq",):
            keys = getattr(settings, f"{name}_keys")
            if keys:
                self.adapters[name] = OpenAICompatibleAdapter(
                    settings, name, getattr(settings, f"{name}_base_url"),
                    getattr(settings, f"{name}_key"), getattr(settings, f"{name}_model"),
                    keys=keys,
                )
        if settings.cloudflare_keys:
            self.adapters["cloudflare"] = OpenAICompatibleAdapter(
                settings, "cloudflare", settings.cloudflare_base_url, settings.cloudflare_key,
                settings.cloudflare_model, keys=settings.cloudflare_keys,
            )
        if settings.huggingface_keys:
            self.adapters["huggingface"] = OpenAICompatibleAdapter(
                settings, "huggingface", settings.huggingface_base_url,
                settings.huggingface_key, settings.huggingface_model,
                keys=settings.huggingface_keys,
            )
        if settings.ollama_base_url:
            self.adapters["ollama"] = OpenAICompatibleAdapter(
                settings, "ollama", settings.ollama_base_url, "", settings.ollama_model,
            )

    async def close(self) -> None:
        await asyncio_gather_close(self.adapters.values())
        if self.openrouter:
            await self.openrouter.close()

    def _label(self, provider: str, model: str) -> str:
        return f"{provider}::{model}"

    def _parse(self, model: str) -> tuple[str, str]:
        if "::" in model:
            provider, model_id = model.split("::", 1)
            return provider.casefold(), model_id
        return "openrouter", model

    def default_model(self) -> str:
        if self.settings.default_model.lower() != "auto":
            provider, model = self._parse(self.settings.default_model)
            if provider in self.adapters and model:
                return self._label(provider, model)
        for provider in self.settings.provider_order:
            adapter = self.adapters.get(provider)
            if adapter and adapter.configured_model():
                return self._label(provider, adapter.configured_model())
        return ""

    def _chain(self, primary: str | None, local_only: bool) -> list[tuple[str, str]]:
        primary = primary or self.default_model()
        candidates: list[str] = [primary] if primary and primary != "auto" else []
        if local_only:
            local = self.adapters.get("ollama")
            if local and local.configured_model():
                candidates.append(self._label("ollama", local.configured_model()))
        else:
            free_only = getattr(self.settings, "ai_free_only", False)
            if free_only:
                approved = {
                    item.casefold(): item
                    for item in getattr(self.settings, "ai_free_model_allowlist", ())
                }
                for provider in self.settings.provider_order:
                    candidates.extend(
                        item for qualified, item in approved.items()
                        if qualified.startswith(f"{provider}::")
                    )
                if getattr(self.settings, "ai_allow_paid_fallback", False):
                    candidates.extend(self.settings.fallback_models)
                    for provider in self.settings.provider_order:
                        adapter = self.adapters.get(provider)
                        if adapter and adapter.configured_model():
                            candidates.append(self._label(provider, adapter.configured_model()))
            else:
                candidates.extend(self.settings.fallback_models)
                for provider in self.settings.provider_order:
                    adapter = self.adapters.get(provider)
                    if adapter and adapter.configured_model():
                        candidates.append(self._label(provider, adapter.configured_model()))
        parsed: list[tuple[str, str]] = []
        for item in candidates:
            provider, model = self._parse(item)
            if provider not in self.adapters or not model or (local_only and provider != "ollama"):
                continue
            candidate = (provider, model)
            if candidate not in parsed:
                parsed.append(candidate)
        return parsed

    async def _adapter_capabilities(self, adapter: ProviderAdapter, model: str) -> dict[str, Any]:
        if isinstance(adapter, _HttpAdapter):
            return adapter.capabilities(model)
        if isinstance(adapter, _OpenRouterAdapter):
            return await adapter.capabilities_for(model)
        return adapter.capabilities()

    def _ensure_reliability_state(self) -> None:
        # Some unit tests construct a router without its real initializer.
        if not hasattr(self, "_circuits"):
            self._circuits = {}
        if not hasattr(self, "_state_lock"):
            self._state_lock = asyncio.Lock()
        if not hasattr(self, "failure_notifier"):
            self.failure_notifier = None

    async def _circuit_is_open(self, provider: str) -> bool:
        self._ensure_reliability_state()
        state = self._circuits.get(provider)
        return bool(state and state.opened_until > time.monotonic())

    async def _record_success(self, provider: str) -> None:
        self._ensure_reliability_state()
        async with self._state_lock:
            self._circuits[provider] = _CircuitState()

    async def _record_failure(self, provider: str, error: Exception) -> None:
        self._ensure_reliability_state()
        if isinstance(error, ProviderError):
            category = error.category
        elif isinstance(error, (httpx.TimeoutException, httpx.NetworkError)):
            category = "temporary_unavailable"
        else:
            return
        breaker_threshold = int(getattr(self.settings, "ai_circuit_breaker_threshold", 3))
        cooldown = float(getattr(self.settings, "ai_circuit_breaker_cooldown", 60))
        now = time.monotonic()
        async with self._state_lock:
            state = self._circuits.setdefault(provider, _CircuitState())
            state.last_category = category
            if category == "invalid_credential":
                state.failures = breaker_threshold
                state.opened_until = float("inf")
            elif category in {"rate_limited", "quota_or_billing"}:
                state.failures += 1
                retry_after = getattr(error, "retry_after", None)
                retry_delay = (
                    float(retry_after) if isinstance(retry_after, (int, float)) else 0.0
                )
                if category == "quota_or_billing":
                    delay = max(cooldown, 300.0, retry_delay)
                else:
                    delay = max(cooldown, retry_delay)
                state.opened_until = max(state.opened_until, now + max(1.0, delay))
            elif category == "temporary_unavailable":
                state.failures += 1
                if state.failures >= breaker_threshold:
                    state.opened_until = now + cooldown

    def _notify_provider_failure(self, correlation_id: str, failures: list[dict[str, str]]) -> None:
        callback = getattr(self, "failure_notifier", None)
        if not callback:
            return
        event = {"correlation_id": correlation_id, "failures": failures[:8]}
        try:
            result = callback(event)
            if asyncio.iscoroutine(result):
                asyncio.create_task(result, name="provider-failure-alert")
        except Exception as exc:
            logger.info("Provider failure alert could not be scheduled (%s)", type(exc).__name__)

    async def _candidate_cost(
        self, provider: str, model: str, *, input_chars: int, max_tokens: int,
        modality: str, image_count: int,
    ) -> float | None:
        if provider == "ollama":
            return 0.0
        if provider == "openrouter" and self.openrouter:
            estimate_model = getattr(self.openrouter, "estimate_model_cost", None)
            if callable(estimate_model):
                return await estimate_model(
                    model, input_chars=input_chars, max_tokens=max_tokens,
                    modality=modality, image_count=image_count,
                )
            return None
        reserve = float(getattr(self.settings, "provider_request_reserve_usd", 0.10))
        return reserve if reserve > 0 else None

    async def _has_unpriced_candidate(
        self, chain: list[tuple[str, str]], *, input_chars: int, max_tokens: int,
        modality: str, image_count: int,
    ) -> bool:
        free_allowlist = {
            item.casefold() for item in getattr(self.settings, "ai_free_model_allowlist", ())
        }
        for provider, model in chain:
            if not await self._model_allowed(
                provider, model, input_chars=input_chars, max_tokens=max_tokens,
                modality=modality, image_count=image_count,
            ):
                continue
            if provider == "ollama" or (
                getattr(self.settings, "ai_free_only", False)
                and self._label(provider, model).casefold() in free_allowlist
            ):
                continue
            if await self._candidate_cost(
                provider, model, input_chars=input_chars, max_tokens=max_tokens,
                modality=modality, image_count=image_count,
            ) is None:
                return True
        return False

    async def _model_allowed(
        self, provider: str, model: str, *, input_chars: int, max_tokens: int,
        modality: str, image_count: int,
    ) -> bool:
        if provider == "ollama" or not getattr(self.settings, "ai_free_only", False):
            return True
        qualified = self._label(provider, model).casefold()
        allowlist = {item.casefold() for item in getattr(self.settings, "ai_free_model_allowlist", ())}
        if qualified in allowlist:
            return True
        if not getattr(self.settings, "ai_allow_paid_fallback", False):
            return False
        max_cost = float(getattr(self.settings, "ai_max_estimated_cost_usd", 0.0))
        if max_cost <= 0:
            return False
        estimate = await self._candidate_cost(
            provider, model, input_chars=input_chars, max_tokens=max_tokens,
            modality=modality, image_count=image_count,
        )
        return estimate is not None and estimate <= max_cost

    async def complete(
        self, messages: list[dict[str, Any]], *, primary_model: str | None = None,
        modality: str = "text", tools: list[dict[str, Any]] | None = None,
        web_search: bool = False, on_token: TokenCallback | None = None,
        max_tokens: int = 1400, local_only: bool = False,
    ) -> AssistantResult:
        token = install_attempt_budget(int(getattr(self.settings, "ai_max_total_attempts", 5)))
        try:
            return await self._complete_with_budget(
                messages, primary_model=primary_model, modality=modality, tools=tools,
                web_search=web_search, on_token=on_token, max_tokens=max_tokens,
                local_only=local_only,
            )
        finally:
            reset_attempt_budget(token)

    async def _complete_with_budget(
        self, messages: list[dict[str, Any]], *, primary_model: str | None = None,
        modality: str = "text", tools: list[dict[str, Any]] | None = None,
        web_search: bool = False, on_token: TokenCallback | None = None,
        max_tokens: int = 1400, local_only: bool = False,
    ) -> AssistantResult:
        correlation_id = uuid.uuid4().hex[:16]
        if web_search:
            if local_only:
                raise ProviderError("Web search is disabled in local-only mode")
            if not self.openrouter or "openrouter" not in self.adapters:
                raise ProviderError("Web search currently requires a configured OpenRouter provider")
            request_text_chars = sum(len(str(item.get("content", ""))) for item in messages)
            image_count = sum(
                1 for item in messages if isinstance(item.get("content"), list)
                for part in item["content"] if isinstance(part, dict) and part.get("type") == "image_url"
            )
            candidates = self._chain(
                self._label("openrouter", self.adapters["openrouter"].configured_model()), False,
            )
            selected_model: str | None = None
            for provider, model in candidates:
                if provider != "openrouter" or not await self._model_allowed(
                    provider, model, input_chars=request_text_chars, max_tokens=max_tokens,
                    modality=modality, image_count=image_count,
                ):
                    continue
                capabilities = await self._adapter_capabilities(self.adapters[provider], model)
                if capabilities.get("tools"):
                    selected_model = model
                    break
            if not selected_model:
                raise ProviderError(
                    "No OpenRouter model is approved and capable of web search.",
                    category="unsupported_capability", request_id=correlation_id,
                )
            cost_limit = float(getattr(self.settings, "ai_max_estimated_cost_usd", 0.0))
            free_allowlist = {
                item.casefold() for item in getattr(self.settings, "ai_free_model_allowlist", ())
            }
            label = self._label("openrouter", selected_model).casefold()
            if getattr(self.settings, "ai_free_only", False) and label in free_allowlist:
                estimated_cost = 0.0
            else:
                estimated_cost = await self._candidate_cost(
                    "openrouter", selected_model, input_chars=request_text_chars,
                    max_tokens=max_tokens, modality=modality, image_count=image_count,
                )
            if cost_limit > 0 and (estimated_cost is None or estimated_cost > cost_limit):
                raise ProviderError(
                    "The estimated cost exceeds the configured per-request limit or is unknown.",
                    category="cost_limit", request_id=correlation_id,
                )
            if remaining_provider_attempts() == 0:
                raise ProviderError(
                    "The per-request provider attempt limit was reached.",
                    category="attempt_limit", request_id=correlation_id,
                )
            if await self._circuit_is_open("openrouter"):
                raise ProviderError(
                    "OpenRouter is cooling down after a recent provider failure.",
                    category="circuit_open", request_id=correlation_id,
                )
            started = time.monotonic()
            try:
                result = await asyncio.wait_for(
                    self.openrouter.complete(
                        messages, primary_model=selected_model,
                        modality=modality, tools=tools, web_search=True,
                        on_token=on_token, max_tokens=max_tokens, routing_preapproved=True,
                    ),
                    timeout=float(getattr(self.settings, "ai_request_timeout", 30)),
                )
                await self._record_success("openrouter")
                return replace(
                    result, provider="openrouter",
                    latency_ms=round((time.monotonic() - started) * 1000),
                    correlation_id=correlation_id,
                )
            except (httpx.HTTPError, ProviderError, TimeoutError) as exc:
                safe_error = exc if isinstance(exc, ProviderError) else ProviderError(
                    "OpenRouter web search temporarily failed", category="temporary_unavailable",
                )
                await self._record_failure("openrouter", safe_error)
                logger.warning(
                    "AI provider failed provider=openrouter category=%s correlation_id=%s",
                    safe_error.category, correlation_id,
                )
                self._notify_provider_failure(correlation_id, [{
                    "provider": "openrouter", "category": safe_error.category,
                }])
                raise ProviderError(
                    "Web search is temporarily unavailable. Please try again later.",
                    category=safe_error.category, request_id=correlation_id,
                ) from exc
        chain = self._chain(primary_model, local_only)
        if not chain:
            raise ProviderError("No configured provider is available for this privacy mode", category="unavailable")
        request_text_chars = sum(
            len(str(item.get("content", ""))) for item in messages
        )
        image_count = sum(
            1 for item in messages if isinstance(item.get("content"), list)
            for part in item["content"] if isinstance(part, dict) and part.get("type") == "image_url"
        )
        estimated_request_cost = await self.estimate_cost(
            request_text_chars, primary_model=primary_model, max_tokens=max_tokens,
            modality=modality, local_only=local_only,
            image_count=image_count,
        )
        cost_limit = float(getattr(self.settings, "ai_max_estimated_cost_usd", 0.0))
        unknown_estimate = cost_limit > 0 and await self._has_unpriced_candidate(
            chain, input_chars=request_text_chars, max_tokens=max_tokens,
            modality=modality, image_count=image_count,
        )
        if cost_limit > 0 and (unknown_estimate or estimated_request_cost > cost_limit):
            raise ProviderError(
                "The estimated cost exceeds the configured per-request limit or is unknown.",
                category="cost_limit", request_id=correlation_id,
            )
        errors: list[dict[str, str]] = []
        attempts = 0
        max_attempts = int(getattr(self.settings, "ai_max_total_attempts", 5))
        timeout = float(getattr(self.settings, "ai_request_timeout", 30))
        for provider, model in chain:
            remaining = remaining_provider_attempts()
            if attempts >= max_attempts or remaining == 0:
                break
            if await self._circuit_is_open(provider):
                errors.append({"provider": provider, "category": "circuit_open"})
                continue
            adapter = self.adapters[provider]
            if not await self._model_allowed(
                provider, model, input_chars=request_text_chars, max_tokens=max_tokens,
                modality=modality, image_count=image_count,
            ):
                continue
            capabilities = await self._adapter_capabilities(adapter, model)
            if modality not in capabilities["input"]:
                continue
            if tools and not capabilities["tools"]:
                continue
            attempts += 1
            token_state = {"emitted": False}

            async def forward_token(token: str) -> None:
                token_state["emitted"] = True
                if on_token:
                    await on_token(token)

            started = time.monotonic()
            try:
                result = await asyncio.wait_for(
                    adapter.complete(
                        model, messages, tools=tools, max_tokens=max_tokens,
                        on_token=forward_token if capabilities.get("streaming") and not tools else None,
                    ),
                    timeout=timeout,
                )
                await self._record_success(provider)
                normalized = replace(
                    result, provider=provider,
                    latency_ms=round((time.monotonic() - started) * 1000),
                    correlation_id=correlation_id,
                )
                logger.info(
                    "AI provider succeeded provider=%s model=%s latency_ms=%s correlation_id=%s",
                    provider, result.model, normalized.latency_ms, correlation_id,
                )
                return normalized
            except (httpx.HTTPError, ProviderError, ValueError, KeyError, TimeoutError) as exc:
                error = exc if isinstance(exc, ProviderError) else ProviderError(
                    f"{provider} request temporarily failed", category="temporary_unavailable",
                )
                await self._record_failure(provider, error)
                category = error.category or "provider_rejected"
                errors.append({"provider": provider, "category": category})
                logger.warning(
                    "AI provider failed provider=%s category=%s correlation_id=%s",
                    provider, category, correlation_id,
                )
                if token_state["emitted"]:
                    raise ProviderError(
                        "The provider stream was interrupted after partial output; it was not replayed.",
                        category="partial_output", request_id=correlation_id,
                    ) from exc
                if category == "invalid_request":
                    raise ProviderError(
                        "The provider rejected this request format. Please try a simpler request.",
                        status_code=error.status_code, category=category,
                        request_id=correlation_id,
                    ) from exc
                if category == "attempt_limit":
                    break
        if errors:
            alertable = [
                item for item in errors if item["category"] not in {"circuit_open", "attempt_limit"}
            ]
            if alertable:
                self._notify_provider_failure(correlation_id, alertable)
            categories = ",".join(dict.fromkeys(item["category"] for item in errors))
            raise ProviderError(
                "All available AI providers are temporarily unavailable. Please try again later.",
                category="all_providers_failed" if categories else "unavailable",
                request_id=correlation_id,
            )
        raise ProviderError(
            "No configured provider model supports this request or is approved by the active cost policy.",
            category="unsupported_capability", request_id=correlation_id,
        )

    async def models(self, *, input_modality: str | None = None, output_modality: str | None = None) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for name, adapter in self.adapters.items():
            try:
                for model in await adapter.list_models():
                    if getattr(self.settings, "ai_free_only", False):
                        approved = {
                            item.casefold() for item in getattr(
                                self.settings, "ai_free_model_allowlist", (),
                            )
                        }
                        if self._label(name, model).casefold() not in approved:
                            continue
                    capabilities = await self._adapter_capabilities(adapter, model)
                    result.append({"id": self._label(name, model), "provider": name,
                                   "architecture": {"input_modalities": capabilities["input"],
                                                    "output_modalities": capabilities["output"]},
                                   "supported_parameters": ["tools"] if capabilities["tools"] else [],
                                   "streaming": bool(capabilities.get("streaming"))})
            except Exception as exc:
                logger.info("Provider catalog %s unavailable (%s)", name, type(exc).__name__)
        if input_modality:
            result = [item for item in result if input_modality in item["architecture"]["input_modalities"]]
        if output_modality:
            result = [item for item in result if output_modality in item["architecture"]["output_modalities"]]
        return result

    async def validate_model(self, model_id: str, modality: str = "text") -> bool:
        provider, model = self._parse(model_id)
        adapter = self.adapters.get(provider)
        if not adapter:
            return False
        try:
            if model != adapter.configured_model() and model not in await adapter.list_models():
                return False
            if not await self._model_allowed(
                provider, model, input_chars=1, max_tokens=1, modality=modality, image_count=0,
            ):
                return False
            capabilities = await self._adapter_capabilities(adapter, model)
            return modality in capabilities.get("input", [])
        except Exception:
            return False

    async def suggested_models(self, modality: str = "text", count: int = 20) -> list[str]:
        return [item["id"] for item in (await self.models(input_modality=modality))[:count]]

    async def estimate_cost(
        self, input_chars: int, *, primary_model: str | None = None, max_tokens: int = 1400,
        modality: str = "text", image_count: int = 0, local_only: bool = False,
    ) -> float:
        chain = self._chain(primary_model, local_only)
        if not chain:
            return 0.0
        estimate = 0.0
        free_allowlist = {
            item.casefold() for item in getattr(self.settings, "ai_free_model_allowlist", ())
        }
        for provider, model in chain:
            if not await self._model_allowed(
                provider, model, input_chars=input_chars, max_tokens=max_tokens,
                modality=modality, image_count=image_count,
            ):
                continue
            if provider == "ollama" or (
                getattr(self.settings, "ai_free_only", False)
                and self._label(provider, model).casefold() in free_allowlist
            ):
                continue
            candidate = await self._candidate_cost(
                provider, model, input_chars=input_chars, max_tokens=max_tokens,
                modality=modality, image_count=image_count,
            )
            estimate += candidate if candidate is not None else float(
                getattr(self.settings, "provider_request_reserve_usd", 0.10),
            )
        return estimate

    async def estimate_image_cost(self) -> float:
        return await self.openrouter.estimate_image_cost() if self.openrouter else 0.0

    async def estimate_transcription_cost(self, audio_bytes: int, max_tokens: int = 1000) -> float:
        return await self.openrouter.estimate_transcription_cost(audio_bytes, max_tokens) if self.openrouter else 0.0

    async def transcribe(self, data: bytes, filename: str, mime_type: str) -> str:
        if not self.openrouter:
            raise ProviderError("No configured transcription provider is available")
        return await self.openrouter.transcribe(data, filename, mime_type)

    async def synthesize(self, text: str, voice: str | None = None) -> bytes:
        if not self.openrouter:
            raise ProviderError("No configured speech generation provider is available")
        return await self.openrouter.synthesize(text, voice)

    async def generate_image(self, prompt: str) -> tuple[bytes | None, str | None]:
        if not self.openrouter:
            raise ProviderError("No configured image generation provider is available")
        return await self.openrouter.generate_image(prompt)

    async def upload_file(self, data: bytes, filename: str, mime_type: str) -> str:
        if not self.openrouter:
            raise ProviderError("Remote file fallback requires OpenRouter")
        return await self.openrouter.upload_file(data, filename, mime_type)

    async def delete_file(self, file_id: str) -> None:
        if self.openrouter:
            await self.openrouter.delete_file(file_id)

    async def complete_with_file(self, *args: Any, **kwargs: Any) -> AssistantResult:
        if not self.openrouter:
            raise ProviderError("Remote file fallback requires OpenRouter")
        return await self.openrouter.complete_with_file(*args, **kwargs)

    async def provider_status(self) -> list[dict[str, Any]]:
        result = []
        for name, adapter in self.adapters.items():
            try:
                status = await adapter.health()
            except Exception as exc:
                status = {"status": "unavailable", "error": type(exc).__name__}
            diagnostics = await self._provider_diagnostic(name, adapter)
            result.append({"provider": name, "model": adapter.configured_model(), **status, **diagnostics})
        return result

    async def _provider_diagnostic(
        self, name: str, adapter: ProviderAdapter,
    ) -> dict[str, Any]:
        self._ensure_reliability_state()
        state = self._circuits.get(name, _CircuitState())
        rotation = getattr(adapter, "key_rotation", None) or getattr(
            getattr(adapter, "router", None), "key_rotation", None,
        )
        keys = rotation.diagnostics() if rotation else {
            "credentials": 0, "available_credentials": 0, "quarantined_credentials": 0,
            "cooldown_seconds": 0, "cooldown_category": None,
        }
        circuit_remaining = (
            max(0, int(state.opened_until - time.monotonic()))
            if state.opened_until != float("inf") else None
        )
        rotation_remaining = int(keys.get("cooldown_seconds", 0) or 0)
        remaining = (
            None if circuit_remaining is None
            else max(circuit_remaining, rotation_remaining)
        )
        return {
            **keys,
            "circuit": "open" if state.opened_until > time.monotonic() or rotation_remaining else "closed",
            "cooldown_seconds": remaining,
            "consecutive_failures": state.failures,
            "last_error_category": state.last_category or keys.get("cooldown_category"),
        }

    async def provider_diagnostics(self) -> list[dict[str, Any]]:
        """Return local circuit and credential counts without making provider requests."""
        self._ensure_reliability_state()
        return [
            {
                "provider": name,
                "model": adapter.configured_model(),
                **await self._provider_diagnostic(name, adapter),
            }
            for name, adapter in self.adapters.items()
        ]

    async def recheck_provider(self, provider: str) -> dict[str, Any]:
        """Run an explicit provider catalog check and retain rejected-key quarantine."""
        adapter = self.adapters.get(provider.casefold())
        if not adapter:
            return {"provider": provider, "status": "not_configured"}
        rotation = getattr(adapter, "key_rotation", None) or getattr(
            getattr(adapter, "router", None), "key_rotation", None,
        )
        if rotation:
            await rotation.clear_quarantine()
        if isinstance(adapter, _OpenRouterAdapter):
            adapter.router._models_cache = (0, [])
        try:
            models = await adapter.list_models()
            await self._record_success(provider.casefold())
            return {"provider": provider.casefold(), "status": "healthy", "models": len(models)}
        except Exception as exc:
            error = exc if isinstance(exc, ProviderError) else ProviderError(
                f"{provider} health check failed", category="temporary_unavailable",
            )
            await self._record_failure(provider.casefold(), error)
            return {
                "provider": provider.casefold(), "status": "unavailable",
                "category": error.category,
            }


class _OpenRouterAdapter:
    name = "openrouter"

    def __init__(self, router: OpenRouter, settings: Settings) -> None:
        self.router = router
        self.settings = settings
        self.last_health: tuple[float, dict[str, Any]] = (0.0, {"status": "unknown"})

    def configured_model(self) -> str:
        configured = self.settings.default_model
        if configured == "auto" or ("::" in configured and configured.split("::", 1)[0].casefold() != "openrouter"):
            return "openrouter/free"
        return configured.removeprefix("openrouter::")

    def capabilities(self) -> dict[str, Any]:
        return {"input": ["text", "image", "audio", "file", "video"],
                "output": ["text", "image", "audio"], "tools": True,
                "streaming": True, "local": False}

    async def capabilities_for(self, model: str) -> dict[str, Any]:
        if model in {"free", "openrouter/free"}:
            return self.capabilities()
        try:
            record = next(
                (item for item in await self.router.models() if item.get("id") == model), None,
            )
        except Exception:
            record = None
        if not record:
            return {"input": ["text"], "output": ["text"], "tools": False,
                    "streaming": False, "local": False}
        architecture = record.get("architecture") or {}
        supported = record.get("supported_parameters") or []
        return {
            "input": architecture.get("input_modalities") or ["text"],
            "output": architecture.get("output_modalities") or ["text"],
            "tools": "tools" in supported,
            "streaming": True,
            "local": False,
        }

    async def complete(
        self, model: str, messages: list[dict[str, Any]], *, tools: list[dict[str, Any]] | None,
        max_tokens: int, on_token: TokenCallback | None = None,
    ) -> AssistantResult:
        modality = "image" if any(
            part.get("type") == "image_url"
            for message in messages
            for part in (message.get("content") if isinstance(message.get("content"), list) else [])
            if isinstance(part, dict)
        ) else "text"
        result = await self.router.complete(
            messages, primary_model=model, modality=modality,
            tools=tools, on_token=on_token, max_tokens=max_tokens,
            routing_preapproved=True,
        )
        return AssistantResult(result.text, f"openrouter::{result.model}", result.usage, result.tool_calls, result.citations)

    async def list_models(self) -> list[str]:
        return [str(item.get("id")) for item in await self.router.models() if item.get("id")]

    async def health(self) -> dict[str, Any]:
        now = time.monotonic()
        if now - self.last_health[0] < 30:
            return dict(self.last_health[1])
        try:
            models = await self.router.models()
            status = {
                "status": "healthy", "models": len(models),
                "probe_latency_ms": round((time.monotonic() - now) * 1000),
            }
        except Exception as exc:
            status = {
                "status": "unavailable", "error": type(exc).__name__,
                "probe_latency_ms": round((time.monotonic() - now) * 1000),
            }
        self.last_health = (now, status)
        return dict(status)

    async def close(self) -> None:
        return None

    async def stream(
        self, model: str, messages: list[dict[str, Any]], *, tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 1400, on_token: TokenCallback | None = None,
    ) -> AssistantResult:
        return await self.complete(model, messages, tools=tools, max_tokens=max_tokens, on_token=on_token)


async def asyncio_gather_close(adapters: Any) -> None:
    import asyncio

    await asyncio.gather(*(adapter.close() for adapter in adapters), return_exceptions=True)
