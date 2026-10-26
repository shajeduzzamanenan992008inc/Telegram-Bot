"""Async OpenRouter client: capability discovery, fallbacks, tools and media."""

from __future__ import annotations

import base64
import json
import logging
import math
import time
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import quote

import httpx

from enan_ultra.config import Settings
from enan_ultra.models import AssistantResult
from enan_ultra.services.key_rotation import KeyRotation, provider_state_path
from enan_ultra.services.provider_errors import (
    ProviderError, consume_provider_attempt, error_from_response,
)

logger = logging.getLogger(__name__)
TokenCallback = Callable[[str], Awaitable[None]]


class OpenRouter:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.key_rotation = KeyRotation(
            "openrouter", settings.openrouter_keys,
            max_retries_per_key=getattr(settings, "ai_max_retries_per_key", 1),
            state_path=provider_state_path(settings, "openrouter"),
            quota_cooldown_seconds=getattr(settings, "ai_circuit_breaker_cooldown", 60),
        )
        self.client = httpx.AsyncClient(
            base_url=settings.openrouter_base_url,
            headers={
                "Content-Type": "application/json",
                "X-OpenRouter-Title": settings.display_name,
            },
            timeout=httpx.Timeout(
                connect=min(10, getattr(settings, "ai_request_timeout", 30)),
                read=getattr(settings, "ai_request_timeout", 30),
                write=getattr(settings, "ai_request_timeout", 30), pool=10,
            ),
            follow_redirects=False,
            limits=httpx.Limits(max_connections=40, max_keepalive_connections=10),
        )
        self._models_cache: tuple[float, list[dict[str, Any]]] = (0, [])

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        original_headers = dict(kwargs.pop("headers", {}) or {})

        async def request(key: str | None, _key_index: int) -> httpx.Response:
            if method.upper() == "POST" and not consume_provider_attempt():
                raise ProviderError("The per-request provider attempt limit was reached", category="attempt_limit")
            if not key:
                raise ProviderError(
                    "OpenRouter is not configured with API credentials",
                    category="invalid_credential",
                )
            headers = dict(original_headers)
            if key:
                headers["Authorization"] = f"Bearer {key}"
            response = await self.client.request(method, url, headers=headers, **kwargs)
            if response.status_code >= 400:
                raise error_from_response("OpenRouter", response)
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
            if not key:
                raise ProviderError(
                    "OpenRouter is not configured with API credentials",
                    category="invalid_credential",
                )
            headers = dict(original_headers)
            if key:
                headers["Authorization"] = f"Bearer {key}"
            async with self.client.stream(method, url, headers=headers, **kwargs) as response:
                if response.status_code >= 400:
                    raise error_from_response("OpenRouter", response)
                return await consume(response)

        return await self.key_rotation.run(request)

    async def close(self) -> None:
        await self.client.aclose()

    @staticmethod
    def _stop_provider_fallback(error: Exception) -> bool:
        return bool(
            getattr(error, "partial_output", False)
            or isinstance(error, ProviderError) and error.category in {
                "invalid_credential", "quota_or_billing", "rate_limited", "attempt_limit",
            }
        )

    async def models(self, *, input_modality: str | None = None, output_modality: str | None = None) -> list[dict[str, Any]]:
        now = time.monotonic()
        if self._models_cache[0] > now:
            data = self._models_cache[1]
        else:
            response = await self._request("GET", "/models")
            response.raise_for_status()
            payload = response.json()
            data = payload.get("data", [])
            self._models_cache = (now + 900, data)
        if input_modality:
            data = [m for m in data if input_modality in (m.get("architecture", {}).get("input_modalities") or [])]
        if output_modality:
            data = [m for m in data if output_modality in (m.get("architecture", {}).get("output_modalities") or [])]
        return data

    async def validate_model(self, model_id: str, modality: str = "text") -> bool:
        return any(model.get("id") == model_id for model in await self.models(input_modality=modality))

    async def suggested_models(self, modality: str = "text", count: int = 20) -> list[str]:
        models = await self.models(input_modality=modality)
        return [str(model["id"]) for model in models[:count] if model.get("id")]

    async def estimate_cost(
        self, input_chars: int, *, primary_model: str | None = None, max_tokens: int = 1400,
        modality: str = "text", image_count: int = 0,
    ) -> float:
        """Conservative estimate from the live catalog; unknown prices remain unestimated."""
        try:
            catalog = await self.models()
        except Exception:
            return 0.0
        chain = await self._model_chain(primary_model, modality)
        prices: list[float] = []
        for model_id in chain:
            record = next((item for item in catalog if item.get("id") == model_id), None)
            pricing = (record or {}).get("pricing") or {}
            try:
                prompt_price = float(pricing.get("prompt") or 0)
                completion_price = float(pricing.get("completion") or 0)
                image_price = float(pricing.get("image") or 0)
            except (TypeError, ValueError):
                continue
            if not all(math.isfinite(item) and item >= 0 for item in (prompt_price, completion_price, image_price)):
                continue
            # One token per character is intentionally conservative for Bengali/other scripts.
            input_tokens = max(1, input_chars) + image_count * 1200
            prices.append(input_tokens * prompt_price + max_tokens * completion_price + image_count * image_price)
        return max(prices, default=0.0)

    async def estimate_model_cost(
        self, model_id: str, *, input_chars: int, max_tokens: int = 1400,
        modality: str = "text", image_count: int = 0,
    ) -> float | None:
        """Estimate one catalogued model, returning None when price is unknown."""
        try:
            records = await self.models(input_modality=modality)
        except Exception:
            return None
        record = next((item for item in records if item.get("id") == model_id), None)
        if not record:
            return None
        pricing = record.get("pricing")
        if not isinstance(pricing, dict) or not {"prompt", "completion"}.issubset(pricing):
            return None
        try:
            prompt_price = float(pricing["prompt"])
            completion_price = float(pricing["completion"])
            image_price = float(pricing.get("image", 0) or 0)
        except (TypeError, ValueError):
            return None
        if not all(
            math.isfinite(value) and value >= 0
            for value in (prompt_price, completion_price, image_price)
        ):
            return None
        input_tokens = max(1, input_chars) + image_count * 1200
        return input_tokens * prompt_price + max_tokens * completion_price + image_count * image_price

    async def estimate_transcription_cost(self, audio_bytes: int, max_tokens: int = 1000) -> float:
        try:
            records = await self.models(input_modality="audio")
            candidates = set(self._modality_candidates(records, self.settings.stt_model))
            costs = []
            for record in records:
                if record.get("id") not in candidates:
                    continue
                pricing = record.get("pricing") or {}
                prompt_price = float(pricing.get("prompt") or 0)
                completion_price = float(pricing.get("completion") or 0)
                if not all(math.isfinite(item) and item >= 0 for item in (prompt_price, completion_price)):
                    continue
                estimated_tokens = max(1000, audio_bytes // 4)
                costs.append(estimated_tokens * prompt_price + max_tokens * completion_price)
            return max(costs, default=0.0)
        except (TypeError, ValueError):
            return 0.0

    async def _model_chain(
        self, primary: str | None, modality: str, *, routing_preapproved: bool = False,
    ) -> list[str]:
        candidates = [primary or self.settings.default_model, *self.settings.fallback_models]
        # Preserve order and avoid retrying the same model twice.
        chain = list(dict.fromkeys(model for model in candidates if model))
        if getattr(self.settings, "ai_free_only", False):
            allowlist = {
                item.split("::", 1)[1]
                for item in getattr(self.settings, "ai_free_model_allowlist", ())
                if item.casefold().startswith("openrouter::")
            }
            if routing_preapproved and primary:
                # ProviderRouter has already checked this exact model and its cost cap.
                return [primary]
            if primary == "openrouter/free" or not primary:
                chain.extend(sorted(allowlist))
            chain = [model for model in chain if model in allowlist]
            return chain[:max(1, int(getattr(self.settings, "ai_max_total_attempts", 5)))]
        if len(chain) <= 1 or modality != "text":
            try:
                records = await self.models(input_modality=modality)
                eligible: list[tuple[float, str]] = []
                for record in records:
                    pricing = record.get("pricing") or {}
                    try:
                        price = float(pricing.get("prompt") or 0) + float(pricing.get("completion") or 0)
                        if not math.isfinite(price) or price < 0:
                            price = float("inf")
                    except (TypeError, ValueError):
                        price = float("inf")
                    model_id = str(record.get("id") or "")
                    if model_id and model_id not in chain:
                        if modality != "text" or price == 0:
                            eligible.append((price, model_id))
                eligible.sort(key=lambda item: (item[0], item[1]))
                chain.extend(model_id for _price, model_id in eligible[:2])
            except Exception:
                pass
        return chain

    async def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        primary_model: str | None = None,
        modality: str = "text",
        tools: list[dict[str, Any]] | None = None,
        web_search: bool = False,
        on_token: TokenCallback | None = None,
        max_tokens: int = 1400,
        routing_preapproved: bool = False,
    ) -> AssistantResult:
        chain = await self._model_chain(
            primary_model, modality, routing_preapproved=routing_preapproved,
        )
        if not chain:
            raise ProviderError("OpenRouter returned no compatible models")
        last_error: Exception | None = None
        for index, model in enumerate(chain):
            try:
                model_record = next((m for m in await self.models() if m.get("id") == model), {})
                request_tools = list(tools or [])
                if web_search and self.settings.web_search_enabled:
                    request_tools.append({"type": "openrouter:web_search", "parameters": {"max_results": 5}})
                if request_tools and "tools" not in model_record.get("supported_parameters", []):
                    request_tools = []
                try:
                    result = await self._completion_for_model(
                        model, messages, request_tools, max_tokens, on_token if not request_tools else None,
                    )
                except ProviderError as exc:
                    if not web_search or not request_tools:
                        raise
                    if exc.category in {
                        "invalid_credential", "quota_or_billing", "rate_limited", "attempt_limit",
                    }:
                        raise
                    # Server-side web search is beta; retry with the ordinary function tools only.
                    without_web = [tool for tool in request_tools if tool.get("type") != "openrouter:web_search"]
                    logger.info("OpenRouter web search tool failed; retrying without it")
                    result = await self._completion_for_model(
                        model, messages, without_web, max_tokens, on_token if not without_web else None,
                    )
                return result
            except (httpx.TimeoutException, httpx.NetworkError, ProviderError) as exc:
                last_error = exc
                logger.warning("OpenRouter model attempt %d failed (%s)", index + 1, type(exc).__name__)
                if getattr(exc, "partial_output", False):
                    raise
                if isinstance(exc, ProviderError) and exc.category in {
                    "invalid_credential", "quota_or_billing", "rate_limited", "attempt_limit",
                }:
                    raise
                continue
            except Exception as exc:
                last_error = exc
                logger.warning("OpenRouter model attempt %d failed (%s)", index + 1, type(exc).__name__)
                continue
        if isinstance(last_error, ProviderError):
            raise last_error
        raise ProviderError(f"All configured models failed: {type(last_error).__name__ if last_error else 'unknown'}")

    async def _completion_for_model(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int,
        on_token: TokenCallback | None,
    ) -> AssistantResult:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.75,
            "stream": bool(on_token),
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
            payload["parallel_tool_calls"] = False
        if not on_token:
            response = await self._request("POST", "/chat/completions", json=payload)
            if response.status_code >= 400:
                # Auth and balance errors are global; model fallback will not fix them.
                if response.status_code in {401, 402, 403}:
                    raise ProviderError(f"OpenRouter request rejected ({response.status_code})", response.status_code)
                raise ProviderError(f"OpenRouter request failed ({response.status_code})", response.status_code)
            body = response.json()
            choices = body.get("choices") or []
            if not choices:
                raise ProviderError("OpenRouter returned no choices")
            message = choices[0].get("message") or {}
            content = message.get("content")
            if isinstance(content, list):
                content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
            citations: list[str] = []
            for annotation in message.get("annotations") or []:
                candidate = annotation.get("url_citation") if isinstance(annotation, dict) else None
                if isinstance(candidate, dict) and isinstance(candidate.get("url"), str):
                    citations.append(candidate["url"])
            citations.extend(item for item in (message.get("citations") or []) if isinstance(item, str))
            return AssistantResult(
                text=str(content or "").strip(),
                model=str(body.get("model") or model),
                usage=body.get("usage") or {},
                tool_calls=tuple(message.get("tool_calls") or ()),
                citations=tuple(dict.fromkeys(citations)),
            )

        answer_parts: list[str] = []
        usage: dict[str, Any] = {}

        async def consume(response: httpx.Response) -> AssistantResult:
            nonlocal usage
            if response.status_code >= 400:
                if response.status_code in {401, 402, 403}:
                    raise ProviderError(f"OpenRouter request rejected ({response.status_code})", response.status_code)
                raise ProviderError(f"OpenRouter request failed ({response.status_code})", response.status_code)
            try:
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if not raw or raw == "[DONE]":
                        continue
                    try:
                        event = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    usage = event.get("usage") or usage
                    choices = event.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    token = delta.get("content")
                    if isinstance(token, str) and token:
                        answer_parts.append(token)
                        await on_token(token)
            except Exception as exc:
                if answer_parts:
                    try:
                        setattr(exc, "partial_output", True)
                    except Exception:
                        pass
                raise
            streamed_text = "".join(answer_parts).strip()
            if not streamed_text:
                raise ProviderError("OpenRouter stream returned no text")
            return AssistantResult(text=streamed_text, model=model, usage=usage)

        return await self._stream_request("POST", "/chat/completions", consume, json=payload)

    async def transcribe(self, data: bytes, filename: str, mime_type: str) -> str:
        models = await self.models(input_modality="audio")
        preferred = self.settings.stt_model
        if preferred and not any(item.get("id") == preferred for item in models):
            raise ProviderError("Configured STT_MODEL does not accept audio")
        candidates = self._modality_candidates(models, preferred)
        if not candidates:
            raise ProviderError("No audio input model is currently available")
        last_error: Exception | None = None
        for index, model in enumerate(candidates):
            try:
                response = await self._request(
                    "POST", "/audio/transcriptions", data={"model": model},
                    files={"file": (filename, data, mime_type or "application/octet-stream")},
                    headers={"Content-Type": None},
                    timeout=httpx.Timeout(connect=10, read=180, write=60, pool=10),
                )
                if response.status_code >= 400:
                    raise ProviderError(f"Speech transcription failed ({response.status_code})", response.status_code)
                return str(response.json().get("text", "")).strip()
            except Exception as exc:
                last_error = exc
                logger.warning("STT model attempt %d failed (%s)", index + 1, type(exc).__name__)
                if self._stop_provider_fallback(exc):
                    raise
        if isinstance(last_error, ProviderError):
            raise last_error
        raise ProviderError(f"All audio transcription models failed ({type(last_error).__name__ if last_error else 'unknown'})")

    async def synthesize(self, text: str, voice: str | None = None) -> bytes:
        models = await self.models(output_modality="audio")
        preferred = self.settings.tts_model
        available = {str(item.get("id")) for item in models if item.get("id")}
        if preferred and preferred not in available:
            logger.warning("Configured TTS_MODEL is not in the audio catalog; using an available model")
        candidates = self._modality_candidates(models, preferred if preferred in available else None)
        if not candidates:
            raise ProviderError("No OpenRouter speech output model is configured")
        last_error: Exception | None = None
        for index, model in enumerate(candidates):
            try:
                async def consume(response: httpx.Response) -> bytes:
                    if response.status_code >= 400:
                        raise ProviderError(
                            f"Speech generation failed ({response.status_code})", response.status_code,
                        )
                    output = bytearray()
                    declared = response.headers.get("content-length")
                    if declared and int(declared) > self.settings.max_upload_bytes:
                        raise ProviderError("Speech output exceeds MAX_UPLOAD_BYTES")
                    async for part in response.aiter_bytes():
                        output.extend(part)
                        if len(output) > self.settings.max_upload_bytes:
                            raise ProviderError("Speech output exceeds MAX_UPLOAD_BYTES")
                    if not output:
                        raise ProviderError("Speech generation returned an empty audio stream")
                    return bytes(output)

                request: dict[str, Any] = {
                    "model": model, "input": text[:4000], "response_format": "mp3",
                }
                selected_voice = voice or self.settings.tts_voice
                if selected_voice:
                    request["voice"] = selected_voice
                try:
                    return await self._stream_request(
                        "POST", "/audio/speech", consume, json=request,
                        timeout=httpx.Timeout(connect=10, read=120, write=45, pool=10),
                    )
                except ProviderError as exc:
                    if "voice" not in request or exc.status_code not in {400, 422}:
                        raise
                    request.pop("voice")
                    logger.info("Retrying TTS model without its provider-specific voice option")
                    return await self._stream_request(
                        "POST", "/audio/speech", consume, json=request,
                        timeout=httpx.Timeout(connect=10, read=120, write=45, pool=10),
                    )
            except Exception as exc:
                last_error = exc
                logger.warning("TTS model attempt %d failed (%s)", index + 1, type(exc).__name__)
                if self._stop_provider_fallback(exc):
                    raise
        if isinstance(last_error, ProviderError):
            raise last_error
        raise ProviderError(f"All speech output models failed ({type(last_error).__name__ if last_error else 'unknown'})")

    def _modality_candidates(self, records: list[dict[str, Any]], preferred: str | None) -> list[str]:
        available = [str(item.get("id")) for item in records if item.get("id")]
        candidates = [preferred, *(model for model in self.settings.fallback_models if model in available), *available]
        return list(dict.fromkeys(model for model in candidates if model and model in available))[:4]

    async def generate_image(self, prompt: str) -> tuple[bytes | None, str | None]:
        items = await self.image_models()
        preferred = self.settings.image_model
        available = [str(item.get("id")) for item in items if item.get("id")]
        if preferred and preferred not in available:
            logger.warning("Configured IMAGE_MODEL is not in the current image catalog; using an available model")
        candidates = list(dict.fromkeys(
            model for model in [preferred, *available] if model and model in available
        ))[:4]
        if not candidates:
            raise ProviderError("No image generation model is currently available")
        last_error: Exception | None = None
        for index, model in enumerate(candidates):
            try:
                response = await self._request(
                    "POST", "/images", json={"model": model, "prompt": prompt[:2000]},
                )
                if response.status_code >= 400:
                    raise ProviderError(f"Image generation failed ({response.status_code})", response.status_code)
                item = (response.json().get("data") or [{}])[0]
                if item.get("b64_json"):
                    encoded = item["b64_json"]
                    if not isinstance(encoded, str) or len(encoded) > ((self.settings.max_image_bytes + 2) // 3) * 4:
                        raise ProviderError("Generated image exceeds MAX_IMAGE_BYTES")
                    image_bytes = base64.b64decode(encoded, validate=True)
                    if len(image_bytes) > self.settings.max_image_bytes:
                        raise ProviderError("Generated image exceeds MAX_IMAGE_BYTES")
                    media_type = str(item.get("media_type") or "image/png").lower()
                    if media_type not in {"image/jpeg", "image/png", "image/webp"}:
                        raise ProviderError("Image model returned an unsupported image format")
                    return image_bytes, media_type
                url = item.get("url")
                if not url:
                    raise ProviderError("Image model returned no image")
                return await self._download_external_image(str(url))
            except Exception as exc:
                last_error = exc
                logger.warning("Image model attempt %d failed (%s)", index + 1, type(exc).__name__)
                if self._stop_provider_fallback(exc):
                    raise
        if isinstance(last_error, ProviderError):
            raise last_error
        raise ProviderError(f"All image generation models failed ({type(last_error).__name__ if last_error else 'unknown'})")

    async def _download_external_image(self, url: str) -> tuple[bytes, str]:
        # This one-shot transport resolves once, validates every address, and connects
        # to those numeric addresses while retaining the original hostname for TLS.
        from enan_ultra.security import download_public_https_image

        try:
            return await download_public_https_image(
                url, max_bytes=self.settings.max_image_bytes,
            )
        except (OSError, TimeoutError, ValueError) as exc:
            raise ProviderError(f"Image URL could not be fetched safely ({type(exc).__name__})") from exc

    async def image_models(self) -> list[dict[str, Any]]:
        response = await self._request("GET", "/images/models")
        if response.status_code >= 400:
            raise ProviderError(
                f"Image model catalog failed ({response.status_code})", response.status_code,
            )
        return response.json().get("data", [])

    async def estimate_image_cost(self) -> float:
        try:
            items = await self.image_models()
            if self.settings.image_model and not any(item.get("id") == self.settings.image_model for item in items):
                raise ProviderError("Configured image model is unavailable")
            prices = [float((item.get("pricing") or {}).get("image") or 0) for item in items]
            return max([self.settings.image_cost_reserve_usd, *prices])
        except Exception:
            return self.settings.image_cost_reserve_usd

    async def upload_file(self, data: bytes, filename: str, mime_type: str) -> str:
        response = await self._request(
            "POST", "/files", files={"file": (filename, data, mime_type or "application/octet-stream")},
            headers={"Content-Type": None}, timeout=httpx.Timeout(connect=10, read=90, write=90, pool=10),
        )
        if response.status_code >= 400:
            raise ProviderError(f"OpenRouter file upload failed ({response.status_code})", response.status_code)
        return str(response.json().get("id", ""))

    async def complete_with_file(self, prompt: str, filename: str, mime_type: str, file_id: str, primary_model: str | None = None) -> AssistantResult:
        content = [
            {"type": "text", "text": prompt},
            {"type": "file", "file": {"filename": filename, "file_id": file_id}},
        ]
        return await self.complete([{"role": "user", "content": content}], primary_model=primary_model, modality="file")

    async def delete_file(self, file_id: str) -> None:
        response = await self._request("DELETE", f"/files/{quote(file_id, safe='')}")
        if response.status_code not in {200, 204, 404}:
            response.raise_for_status()


def tool_schemas(*, admin_actions: bool = False) -> list[dict[str, Any]]:
    """Only narrowly validated actions are exposed; writes create pending approvals."""
    schemas = [
        {
            "type": "function",
            "function": {
                "name": "send_poll",
                "description": "Create a Telegram poll only when the user asks or choosing between options would help.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string", "maxLength": 300},
                        "options": {"type": "array", "items": {"type": "string", "maxLength": 100}, "minItems": 2, "maxItems": 10},
                    },
                    "required": ["question", "options"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "send_sticker",
                "description": "Send one contextually appropriate sticker selected only from the configured sticker IDs.",
                "parameters": {
                    "type": "object",
                    "properties": {"file_id": {"type": "string"}},
                    "required": ["file_id"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "generate_image",
                "description": "Generate one image only when the user explicitly asks for an image or illustration.",
                "parameters": {
                    "type": "object",
                    "properties": {"prompt": {"type": "string", "maxLength": 2000}},
                    "required": ["prompt"],
                    "additionalProperties": False,
                },
            },
        },
    ]
    if admin_actions:
        schemas.extend([
            {
                "type": "function",
                "function": {
                    "name": "home_assistant_action",
                    "description": "Request an allowlisted Home Assistant service call. It will only be queued for admin confirmation, never executed by the model.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "service": {"type": "string", "maxLength": 80},
                            "entity_ids": {"type": "array", "items": {"type": "string", "maxLength": 100}, "minItems": 1, "maxItems": 10},
                            "service_data": {"type": "object", "properties": {
                                "brightness": {"type": "integer", "minimum": 0, "maximum": 255},
                                "temperature": {"type": "number", "minimum": 5, "maximum": 40},
                                "color_temp": {"type": "string", "maxLength": 40},
                                "hvac_mode": {"type": "string", "maxLength": 40},
                            }, "additionalProperties": False},
                        },
                        "required": ["service", "entity_ids"], "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "restart_bot_service",
                    "description": "Create an approval request to restart this bot service. Never restart without an admin explicitly approving it.",
                    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
                },
            },
        ])
    return schemas
