"""Authenticated Telegram Mini App for private adaptive chat and theme settings."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from enan_ultra.config import Settings
from enan_ultra.security import is_authorized
from enan_ultra.services.conversation import bound_history
from enan_ultra.services.theme_engine import AdaptiveThemeEngine, ThemeDecision
from enan_ultra.storage.database import Database

logger = logging.getLogger(__name__)
_STATIC_DIR = Path(__file__).resolve().parent / "webapp" / "static"
_MAX_INIT_DATA_BYTES = 8192
_INIT_DATA_MAX_AGE = 12 * 60 * 60
_MAX_FUTURE_SKEW = 30
_MAX_REQUEST_BYTES = 64 * 1024


def validate_init_data(
    init_data: str, bot_token: str, *, now: int | None = None,
    max_age: int = _INIT_DATA_MAX_AGE,
) -> dict[str, Any]:
    """Verify Telegram's signed initData and return the authenticated user object."""
    if not init_data or len(init_data.encode("utf-8")) > _MAX_INIT_DATA_BYTES:
        raise ValueError("Missing or oversized Telegram initData")
    try:
        fields = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True, max_num_fields=32)
    except ValueError as exc:
        raise ValueError("Malformed Telegram initData") from exc
    values: dict[str, str] = {}
    for key, value in fields:
        if key in values:
            raise ValueError("Duplicate Telegram initData field")
        values[key] = value
    supplied_hash = values.pop("hash", "")
    if not supplied_hash or len(supplied_hash) != 64:
        raise ValueError("Missing Telegram initData signature")
    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(values.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    expected_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_hash, supplied_hash.casefold()):
        raise ValueError("Invalid Telegram initData signature")
    try:
        auth_date = int(values.get("auth_date", ""))
        user = json.loads(values.get("user", ""))
        user_id = int(user.get("id", 0)) if isinstance(user, dict) else 0
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid Telegram initData user or timestamp") from exc
    current_time = int(time.time()) if now is None else now
    if auth_date > current_time + _MAX_FUTURE_SKEW or current_time - auth_date > max_age:
        raise ValueError("Telegram initData has expired")
    if user_id <= 0 or user_id > 2**52:
        raise ValueError("Invalid Telegram user ID")
    user["id"] = user_id
    user["first_name"] = str(user.get("first_name", ""))[:128]
    user["last_name"] = str(user.get("last_name", ""))[:128]
    user["username"] = str(user.get("username", ""))[:64]
    return user


class MiniAppServer:
    """Own a separate loopback ASGI app; admin dashboard authorization stays isolated."""

    def __init__(
        self, settings: Settings, db: Database, router: Any, handlers: Any,
        theme_engine: AdaptiveThemeEngine | None = None,
    ) -> None:
        self.settings = settings
        self.db = db
        self.router = router
        self.handlers = handlers
        self.theme_engine = theme_engine or AdaptiveThemeEngine()
        self.public_url = urlsplit(settings.mini_app_url)
        self.public_host = self.public_url.netloc.lower()
        self.public_origin = f"{self.public_url.scheme}://{self.public_host}"
        self.rate_windows: dict[int, deque[float]] = defaultdict(deque)
        self.app = self._build_app()
        self.server = uvicorn.Server(uvicorn.Config(
            self.app, host=settings.mini_app_host, port=settings.mini_app_port,
            log_level="warning", access_log=False, lifespan="off",
        ))
        self.server.install_signal_handlers = lambda: None

    def _build_app(self) -> FastAPI:
        app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

        @app.middleware("http")
        async def harden(request: Request, call_next: Any):
            host = request.headers.get("host", "").lower()
            forwarded_proto = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip().lower()
            if host != self.public_host or forwarded_proto != "https":
                return JSONResponse({"detail": "Use the configured HTTPS Mini App URL"}, status_code=421)
            origin = request.headers.get("origin")
            if origin and origin.rstrip("/").lower() != self.public_origin:
                return JSONResponse({"detail": "Cross-origin request rejected"}, status_code=403)
            content_length = request.headers.get("content-length")
            if content_length:
                try:
                    if int(content_length) > _MAX_REQUEST_BYTES:
                        return JSONResponse({"detail": "Request body is too large"}, status_code=413)
                except ValueError:
                    return JSONResponse({"detail": "Invalid content length"}, status_code=400)
            response = await call_next(request)
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self' https://telegram.org; "
                "style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; "
                "font-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
            )
            return response

        app.mount("/assets", StaticFiles(directory=_STATIC_DIR, check_dir=True), name="mini-app-assets")

        @app.get("/healthz")
        async def healthz():
            return {"status": "ok"}

        @app.get("/")
        async def index():
            return FileResponse(_STATIC_DIR / "index.html", media_type="text/html")

        @app.get("/api/v1/bootstrap")
        async def bootstrap(request: Request):
            user = await self._authenticate(request)
            user_id = int(user["id"])
            state = await self._user_state(user_id)
            history = await self._chat_history(user_id)
            return {
                "user": {"first_name": user["first_name"], "last_name": user["last_name"]},
                "themes": self.theme_engine.registry.client_themes(),
                "state": state,
                "history": history,
                "telegram": {"color_scheme": "dark"},
            }

        @app.get("/api/v1/themes")
        async def themes(request: Request):
            await self._authenticate(request)
            return {"themes": self.theme_engine.registry.client_themes()}

        @app.post("/api/v1/theme")
        async def update_theme(request: Request):
            user = await self._authenticate(request)
            user_id = int(user["id"])
            payload = await self._read_json(request)
            action = payload.get("action")
            try:
                if action == "mode":
                    mode = str(payload.get("mode", ""))
                    theme_id = payload.get("theme_id")
                    decision = await self.theme_engine.set_mode(
                        self.db, user_id, mode, str(theme_id) if theme_id else None,
                    )
                elif action == "setting":
                    key, value = str(payload.get("key", "")), str(payload.get("value", ""))
                    await self.theme_engine.update_setting(self.db, user_id, key, value)
                    decision = await self.theme_engine.current(self.db, user_id)
                elif action == "favorite":
                    theme_id = str(payload.get("theme_id", ""))
                    favorited = await self.theme_engine.favorite(self.db, user_id, theme_id)
                    return {"state": await self._user_state(user_id), "favorited": favorited}
                elif action == "reset":
                    await self.theme_engine.reset(self.db, user_id)
                    decision = await self.theme_engine.current(self.db, user_id)
                else:
                    raise ValueError("Unknown theme operation")
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            logger.info("Mini App theme preference updated for user %s (%s)", user_id, action)
            return {"state": await self._user_state(user_id, decision)}

        @app.get("/api/v1/chat/history")
        async def chat_history(request: Request):
            user = await self._authenticate(request)
            return {"history": await self._chat_history(int(user["id"]))}

        @app.get("/api/v1/tasks")
        async def list_tasks(request: Request):
            user = await self._authenticate(request)
            return {"tasks": await self.db.list_tasks(int(user["id"]))}

        @app.post("/api/v1/tasks")
        async def update_tasks(request: Request):
            user = await self._authenticate(request)
            user_id = int(user["id"])
            payload = await self._read_json(request)
            action = payload.get("action")
            try:
                if action == "add":
                    text = payload.get("text")
                    priority = payload.get("priority", "normal")
                    due_at = payload.get("due_at")
                    dependencies = payload.get("depends_on", [])
                    if not isinstance(text, str):
                        raise ValueError("Task text is required")
                    if not isinstance(priority, str):
                        raise ValueError("Invalid task priority")
                    if due_at is not None and not isinstance(due_at, str):
                        raise ValueError("Invalid task deadline")
                    if (
                        not isinstance(dependencies, list)
                        or any(isinstance(item, bool) or not isinstance(item, int) for item in dependencies)
                    ):
                        raise ValueError("Invalid task prerequisites")
                    task_id = await self.db.add_task(
                        user_id, text, priority, due_at, depends_on=dependencies,
                    )
                    result = {"task_id": task_id}
                elif action in {"done", "delete"}:
                    task_id = payload.get("task_id")
                    if isinstance(task_id, bool) or not isinstance(task_id, int) or task_id <= 0:
                        raise ValueError("A valid task ID is required")
                    if action == "done":
                        changed = await self.db.complete_task(user_id, task_id)
                        if not changed:
                            tasks = await self.db.list_tasks(user_id)
                            task = next((item for item in tasks if item["id"] == task_id), None)
                            if task and task["blocked_by"]:
                                raise HTTPException(
                                    status_code=409,
                                    detail="Complete prerequisite tasks first: "
                                    + ", ".join(f"#{item}" for item in task["blocked_by"]),
                                )
                            raise HTTPException(status_code=404, detail="Open task not found")
                    else:
                        try:
                            changed = await self.db.delete_task(user_id, task_id)
                        except ValueError as exc:
                            raise HTTPException(status_code=409, detail=str(exc)) from exc
                        if not changed:
                            raise HTTPException(status_code=404, detail="Task not found")
                    result = {"task_id": task_id}
                else:
                    raise ValueError("Unknown task operation")
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return {**result, "tasks": await self.db.list_tasks(user_id)}

        @app.post("/api/v1/chat")
        async def chat(request: Request):
            user = await self._authenticate(request)
            user_id = int(user["id"])
            payload = await self._read_json(request)
            prompt = payload.get("text")
            if not isinstance(prompt, str) or not prompt.strip():
                raise HTTPException(status_code=400, detail="Enter a message")
            prompt = prompt.strip()
            if len(prompt) > self.settings.max_input_chars:
                raise HTTPException(status_code=413, detail="Message is too long")
            if not is_authorized(self.settings, user_id):
                raise HTTPException(status_code=403, detail="This account is not authorized")
            if await self.db.is_user_blocked(user_id):
                raise HTTPException(status_code=403, detail="This account is blocked")
            lock = self.handlers.locks.setdefault(user_id, asyncio.Lock())
            if lock.locked():
                raise HTTPException(status_code=429, detail="A response is already being generated for this account")
            await lock.acquire()
            now = time.monotonic()
            wait = self.settings.cooldown_seconds - (now - self.handlers.last_request.get(user_id, 0))
            if wait > 0:
                lock.release()
                raise HTTPException(status_code=429, detail="Please wait before sending another message")
            try:
                profile = await self.handlers._profile(user_id, self._display_name(user))
                temporary = await self.db.get_preference(user_id, "temporary_chat", "off") == "on"
                history = [] if temporary else await self.db.history(
                    user_id, max(0, self.settings.history_messages - 1),
                )
                history = bound_history(history, self.settings.max_input_chars)
                decision = await self.theme_engine.resolve(
                    self.db, user_id, prompt, history, persist=not temporary,
                )
                memories = await self.db.memories(user_id) if profile.memory_enabled and not temporary else []
                facts = await self.db.get_user_facts(user_id) if profile.memory_enabled and not temporary else {}
                learned = await self.db.get_learned_knowledge(user_id) if profile.memory_enabled and not temporary else []
                system = self.handlers._system_prompt(
                    profile, memories, "", await self.db.get_chat_mode(user_id), facts, learned,
                )
                messages = [
                    {"role": "system", "content": system},
                    *history,
                    {"role": "user", "content": prompt},
                ]
                model_id = await self.db.get_setting("current_model") or self.router.default_model()
                if not model_id:
                    raise HTTPException(status_code=503, detail="No AI provider is configured")
                estimate = await self.router.estimate_cost(
                    self.handlers._input_text_chars(messages), primary_model=model_id,
                    local_only=profile.local_only,
                )
                event_id, reason = await self.db.reserve_usage(
                    user_id, model_id, estimate, self.settings.user_daily_request_limit,
                    self.settings.max_daily_cost_usd, self.settings.user_monthly_request_limit,
                    self.settings.user_monthly_cost_limit_usd,
                )
                if event_id is None:
                    raise HTTPException(status_code=429, detail=self._usage_error(reason))
                self.handlers.last_request[user_id] = now
                if not temporary:
                    await self.db.save_message(user_id, "user", prompt, self.settings.history_messages)
            except BaseException:
                lock.release()
                raise

            return StreamingResponse(
                self._stream_chat(user_id, lock, event_id, model_id, messages, profile.local_only, decision, temporary),
                media_type="text/event-stream",
                headers={"X-Accel-Buffering": "no", "Connection": "keep-alive"},
            )

        return app

    async def _authenticate(self, request: Request) -> dict[str, Any]:
        authorization = request.headers.get("authorization", "")
        if not authorization.startswith("tma "):
            raise HTTPException(status_code=401, detail="Open this workspace from Telegram")
        raw = authorization[4:]
        try:
            user = validate_init_data(raw, self.settings.telegram_token)
        except ValueError as exc:
            raise HTTPException(status_code=401, detail="Telegram session is invalid or expired") from exc
        user_id = int(user["id"])
        now = time.monotonic()
        bucket = self.rate_windows[user_id]
        while bucket and now - bucket[0] > 60:
            bucket.popleft()
        if len(bucket) >= 120:
            raise HTTPException(status_code=429, detail="Too many requests; wait a moment")
        bucket.append(now)
        if not is_authorized(self.settings, user_id) or await self.db.is_user_blocked(user_id):
            raise HTTPException(status_code=403, detail="This account is not authorized")
        return user

    @staticmethod
    async def _read_json(request: Request) -> dict[str, Any]:
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > _MAX_REQUEST_BYTES:
                raise HTTPException(status_code=413, detail="Request body is too large")
        try:
            payload = json.loads(body or b"{}")
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise HTTPException(status_code=400, detail="Request body must be valid JSON") from exc
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Request body must be a JSON object")
        return payload

    async def _user_state(
        self, user_id: int, decision: ThemeDecision | None = None,
    ) -> dict[str, Any]:
        decision = decision or await self.theme_engine.current(self.db, user_id)
        theme = decision.theme
        favorites = {
            theme_id: await self.db.get_preference(user_id, f"theme_favorite_{theme_id}", "0") == "1"
            for theme_id in self.theme_engine.registry.themes
        }
        return {
            "mode": decision.mode,
            "topic_id": decision.topic_id,
            "theme_id": decision.theme_id,
            "confidence": decision.confidence,
            "reason_code": decision.reason_code,
            "label": theme.get("label", {}),
            "icon": theme.get("icon", "✨"),
            "theme_learning": await self.db.get_preference(user_id, "theme_learning", "on"),
            "theme_animation": await self.db.get_preference(user_id, "theme_animation", "auto"),
            "appearance_mode": await self.db.get_preference(user_id, "appearance_mode", "system"),
            "theme_palette": await self.db.get_preference(user_id, "theme_palette", "default"),
            "favorites": favorites,
            "favorite": await self.db.get_preference(
                user_id, f"theme_favorite_{decision.theme_id}", "0",
            ) == "1",
        }

    async def _chat_history(self, user_id: int) -> list[dict[str, str]]:
        if await self.db.get_preference(user_id, "temporary_chat", "off") == "on":
            return []
        history = await self.db.history(user_id, min(self.settings.history_messages, 40))
        return bound_history(history, self.settings.max_input_chars)

    async def _stream_chat(
        self, user_id: int, lock: asyncio.Lock, event_id: int, model_id: str,
        messages: list[dict[str, Any]], local_only: bool, decision: ThemeDecision,
        temporary: bool,
    ):
        queue: asyncio.Queue[str] = asyncio.Queue()

        async def on_token(token: str) -> None:
            if token:
                await queue.put(token)

        task = asyncio.create_task(self.router.complete(
            messages, primary_model=model_id, on_token=on_token, local_only=local_only,
            max_tokens=min(4000, max(256, self.settings.max_output_chars // 2)),
        ))
        try:
            yield self._event("theme", self._theme_event_data(decision))
            while not task.done() or not queue.empty():
                try:
                    token = await asyncio.wait_for(queue.get(), timeout=0.2)
                except TimeoutError:
                    continue
                yield self._event("token", {"text": token})
            result = await task
            final_text = result.text.strip()[:self.settings.max_output_chars]
            await self.db.finish_usage(event_id, result.model, result.usage)
            if final_text and not temporary:
                await self.db.save_message(
                    user_id, "assistant", final_text, self.settings.history_messages,
                )
            yield self._event("done", {
                "text": final_text, "model": result.model,
                "theme_id": decision.theme_id, "topic_id": decision.topic_id,
            })
        except asyncio.CancelledError:
            task.cancel()
            raise
        except Exception as exc:
            await self.db.finish_usage(event_id, model_id, {})
            logger.warning("Mini App generation failed for user %s (%s)", user_id, type(exc).__name__)
            yield self._event("error", {"message": "AI response could not be completed. Please try again."})
        finally:
            if not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
            lock.release()

    @staticmethod
    def _event(name: str, data: dict[str, Any]) -> str:
        return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"

    @staticmethod
    def _theme_event_data(decision: ThemeDecision) -> dict[str, Any]:
        return {
            "topic_id": decision.topic_id,
            "theme_id": decision.theme_id,
            "confidence": decision.confidence,
            "reason_code": decision.reason_code,
            "theme": decision.theme,
        }

    @staticmethod
    def _usage_error(reason: str | None) -> str:
        messages = {
            "request_limit": "Your daily request limit has been reached.",
            "monthly_request_limit": "Your monthly request limit has been reached.",
            "monthly_cost_limit": "Your monthly cost budget has been reached.",
            "cost_limit": "The bot-wide daily cost budget has been reached.",
        }
        return messages.get(reason or "", "The request is not available right now.")

    @staticmethod
    def _display_name(user: dict[str, Any]) -> str:
        return " ".join(part for part in (user.get("first_name", ""), user.get("last_name", "")) if part)[:180]
