"""Provider-neutral model routing for OpenRouter and direct API providers."""

from __future__ import annotations

import base64
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

import httpx

from enan_ultra.config import Settings
from enan_ultra.models import AssistantResult
from enan_ultra.services.openrouter import OpenRouter, ProviderError

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

    def __init__(self, settings: Settings, base_url: str, headers: dict[str, str]) -> None:
        self.settings = settings
        self.client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Content-Type": "application/json", **headers},
            timeout=httpx.Timeout(connect=8, read=90, write=45, pool=10),
            follow_redirects=False,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=5),
        )
        self.last_health: tuple[float, dict[str, Any]] = (0.0, {"status": "unknown"})

    async def close(self) -> None:
        await self.client.aclose()

    def capabilities(self) -> dict[str, Any]:
        return {"input": ["text", "image"], "output": ["text"], "tools": True,
                "streaming": True, "local": self.name == "ollama"}

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
            state = {"status": "healthy", "models": len(models)}
        except Exception as exc:
            state = {"status": "unavailable", "error": type(exc).__name__}
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
    """OpenAI API and compatible Ollama endpoint adapter."""

    def __init__(self, settings: Settings, name: str, base_url: str, key: str, model: str) -> None:
        headers = {"Authorization": f"Bearer {key or 'ollama'}"}
        super().__init__(settings, base_url, headers)
        self.name = name
        self._model = model

    def configured_model(self) -> str:
        return self._model

    async def list_models(self) -> list[str]:
        response = await self.client.get("/models")
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
            pieces: list[str] = []
            usage: dict[str, Any] = {}
            async with self.client.stream("POST", "/chat/completions", json=payload) as response:
                self._check(response)
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
            text = "".join(pieces).strip()
            if not text:
                raise ProviderError(f"{self.name} returned an empty stream")
            return AssistantResult(text=text, model=f"{self.name}::{model}", usage=usage)
        response = await self.client.post("/chat/completions", json=payload)
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
        if response.status_code in {401, 402, 403}:
            raise ProviderError(f"{self.name} request rejected ({response.status_code})")
        if response.status_code >= 400:
            raise ProviderError(f"{self.name} request failed ({response.status_code})")


class AnthropicAdapter(_HttpAdapter):
    name = "anthropic"
    _api_version = "2023-06-01"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings, settings.anthropic_base_url, {
            "x-api-key": settings.anthropic_key, "anthropic-version": self._api_version,
        })

    def configured_model(self) -> str:
        return self.settings.anthropic_model

    async def list_models(self) -> list[str]:
        response = await self.client.get("/v1/models", params={"limit": 100})
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
        response = await self.client.post("/v1/messages", json=request)
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
        if response.status_code in {401, 402, 403}:
            raise ProviderError(f"Anthropic request rejected ({response.status_code})")
        if response.status_code >= 400:
            raise ProviderError(f"Anthropic request failed ({response.status_code})")


class GeminiAdapter(_HttpAdapter):
    name = "gemini"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings, settings.gemini_base_url, {"x-goog-api-key": settings.gemini_key})

    def configured_model(self) -> str:
        return self.settings.gemini_model

    async def list_models(self) -> list[str]:
        response = await self.client.get("/models")
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
        response = await self.client.post(f"/models/{model}:generateContent", json=request)
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
        if response.status_code in {401, 402, 403}:
            raise ProviderError(f"Gemini request rejected ({response.status_code})")
        if response.status_code >= 400:
            raise ProviderError(f"Gemini request failed ({response.status_code})")


class ProviderRouter:
    """Provider facade; OpenRouter keeps media/file operations, adapters route chat/tools."""

    def __init__(self, settings: Settings, openrouter: OpenRouter | None) -> None:
        self.settings = settings
        self.openrouter = openrouter
        self.adapters: dict[str, ProviderAdapter] = {}
        if settings.openrouter_key and openrouter:
            self.adapters["openrouter"] = _OpenRouterAdapter(openrouter, settings)
        if settings.openai_key:
            self.adapters["openai"] = OpenAICompatibleAdapter(
                settings, "openai", settings.openai_base_url, settings.openai_key, settings.openai_model,
            )
        if settings.anthropic_key:
            self.adapters["anthropic"] = AnthropicAdapter(settings)
        if settings.gemini_key:
            self.adapters["gemini"] = GeminiAdapter(settings)
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
        candidates: list[str] = [primary, *self.settings.fallback_models]
        if primary == "auto" or not primary:
            candidates = []
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

    async def complete(
        self, messages: list[dict[str, Any]], *, primary_model: str | None = None,
        modality: str = "text", tools: list[dict[str, Any]] | None = None,
        web_search: bool = False, on_token: TokenCallback | None = None,
        max_tokens: int = 1400, local_only: bool = False,
    ) -> AssistantResult:
        if web_search:
            if local_only:
                raise ProviderError("Web search is disabled in local-only mode")
            if not self.openrouter:
                raise ProviderError("Web search currently requires a configured OpenRouter provider")
            return await self.openrouter.complete(
                messages, primary_model=self.adapters["openrouter"].configured_model(), modality=modality, tools=tools,
                web_search=True, on_token=on_token, max_tokens=max_tokens,
            )
        chain = self._chain(primary_model, local_only)
        if not chain:
            raise ProviderError("No configured provider is available for this privacy mode")
        errors: list[str] = []
        for provider, model in chain:
            adapter = self.adapters[provider]
            capabilities = adapter.capabilities()
            if modality not in capabilities["input"]:
                continue
            if tools and not capabilities["tools"]:
                continue
            try:
                result = await adapter.complete(
                    model, messages, tools=tools, max_tokens=max_tokens,
                    on_token=on_token if not tools else None,
                )
                return result
            except (httpx.HTTPError, ProviderError, ValueError, KeyError) as exc:
                errors.append(f"{provider}:{type(exc).__name__}")
                logger.warning("Provider %s attempt failed (%s)", provider, type(exc).__name__)
        raise ProviderError("Configured providers failed: " + ", ".join(errors[:5]))

    async def models(self, *, input_modality: str | None = None, output_modality: str | None = None) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for name, adapter in self.adapters.items():
            try:
                for model in await adapter.list_models():
                    capabilities = adapter.capabilities()
                    result.append({"id": self._label(name, model), "provider": name,
                                   "architecture": {"input_modalities": capabilities["input"],
                                                    "output_modalities": capabilities["output"]},
                                   "supported_parameters": ["tools"] if capabilities["tools"] else []})
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
        if model == adapter.configured_model():
            return True
        return model in await adapter.list_models()

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
        for provider, model in chain:
            if provider == "ollama":
                continue
            if provider == "openrouter" and self.openrouter:
                estimate += await self.openrouter.estimate_cost(
                    input_chars, primary_model=model, max_tokens=max_tokens,
                    modality=modality, image_count=image_count,
                )
            else:
                estimate += self.settings.provider_request_reserve_usd
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
            result.append({"provider": name, "model": adapter.configured_model(), **status})
        return result


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

    async def complete(
        self, model: str, messages: list[dict[str, Any]], *, tools: list[dict[str, Any]] | None,
        max_tokens: int, on_token: TokenCallback | None = None,
    ) -> AssistantResult:
        result = await self.router.complete(
            messages, primary_model=model, tools=tools, on_token=on_token, max_tokens=max_tokens,
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
            status = {"status": "healthy", "models": len(models)}
        except Exception as exc:
            status = {"status": "unavailable", "error": type(exc).__name__}
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
