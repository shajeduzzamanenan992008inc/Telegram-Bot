from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode

import httpx
import pytest

from enan_ultra.config import Settings
from enan_ultra.mini_app import MiniAppServer, validate_init_data
from enan_ultra.models import AssistantResult, UserProfile
from enan_ultra.storage.database import Database

TOKEN = "123456:mini-app-test-token-with-sufficient-length"
APP_KEY = "k" * 64


def signed_init_data(user_id: int, *, now: int, bot_token: str = TOKEN) -> str:
    values = {
        "auth_date": str(now),
        "query_id": f"AAEAA-{user_id}",
        "user": json.dumps({"id": user_id, "first_name": f"User {user_id}"}, separators=(",", ":")),
    }
    check = "\n".join(f"{key}={value}" for key, value in sorted(values.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    values["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(values)


def test_telegram_init_data_signature_timestamp_and_user_are_checked() -> None:
    now = 1_800_000_000
    raw = signed_init_data(77, now=now)
    assert validate_init_data(raw, TOKEN, now=now)["id"] == 77
    with pytest.raises(ValueError, match="signature"):
        validate_init_data(raw.replace("User+77", "Attacker"), TOKEN, now=now)
    with pytest.raises(ValueError, match="expired"):
        validate_init_data(signed_init_data(77, now=now - 50_000), TOKEN, now=now)
    with pytest.raises(ValueError, match="expired"):
        validate_init_data(signed_init_data(77, now=now + 60), TOKEN, now=now)


def test_mini_app_settings_require_https_root_and_loopback(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    values = {
        "TELEGRAM_BOT_TOKEN": TOKEN,
        "ADMIN_CHAT_ID": "123456",
        "ADMIN_USER_IDS": "123456",
        "OPENROUTER_API_KEY": "test-provider-key",
        "APP_DATA_KEY": APP_KEY,
        "DASHBOARD_AUTH_TOKEN": "test-dashboard-auth-token-12345678901234567890",
        "MINI_APP_URL": "https://omega.example",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    settings = Settings.load()
    assert settings.mini_app_url == "https://omega.example"
    assert settings.mini_app_host == "127.0.0.1"

    monkeypatch.setenv("MINI_APP_URL", "http://omega.example")
    with pytest.raises(ValueError, match="HTTPS origin"):
        Settings.load()

    monkeypatch.setenv("MINI_APP_URL", "https://omega.example/app")
    with pytest.raises(ValueError, match="HTTPS origin"):
        Settings.load()

    monkeypatch.setenv("MINI_APP_URL", "https://omega.example")
    monkeypatch.setenv("MINI_APP_HOST", "0.0.0.0")
    with pytest.raises(ValueError, match="127.0.0.1"):
        Settings.load()


def test_mini_app_chat_authenticates_and_checks_blocked_state_once(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        db = Database(tmp_path / "mini-app-blocked.sqlite3")
        await db.open()
        settings = SimpleNamespace(
            telegram_token=TOKEN,
            mini_app_url="https://omega.example",
            mini_app_host="127.0.0.1",
            mini_app_port=8788,
            max_input_chars=1000,
            max_output_chars=1200,
            history_messages=12,
            cooldown_seconds=0,
            user_daily_request_limit=30,
            user_monthly_request_limit=120,
            user_monthly_cost_limit_usd=20.0,
            max_daily_cost_usd=100.0,
            access_mode="public",
            allowed_user_ids=frozenset(),
            effective_admin_user_ids=frozenset(),
        )
        await db.set_global_access_mode("public", 0)
        await db.set_user_blocked(77, 0, "test block")

        calls = 0
        original = db.is_user_blocked

        async def counted(user_id: int) -> bool:
            nonlocal calls
            calls += 1
            return await original(user_id)

        db.is_user_blocked = counted  # type: ignore[method-assign]
        service = MiniAppServer(settings, db, SimpleNamespace(), SimpleNamespace())
        now = int(__import__("time").time())
        headers = {
            "Authorization": f"tma {signed_init_data(77, now=now)}",
            "Origin": "https://omega.example",
            "X-Forwarded-Proto": "https",
        }
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=service.app),
                base_url="http://omega.example",
            ) as client:
                response = await client.post(
                    "/api/v1/chat", headers=headers, json={"text": "hello"},
                )
            assert response.status_code == 403
            assert calls == 1
        finally:
            await db.close()

    asyncio.run(scenario())


def test_mini_app_auth_theme_preferences_streaming_and_user_isolation(tmp_path: Path) -> None:
    async def scenario() -> None:
        db = Database(tmp_path / "mini-app.sqlite3")
        await db.open()

        class Router:
            def default_model(self):
                return "openrouter::test-model"

            async def estimate_cost(self, *_args, **_kwargs):
                return 0.001

            async def complete(self, _messages, *, primary_model, on_token, **_kwargs):
                await on_token("বাংলা উত্তর")
                return AssistantResult("বাংলা উত্তর", primary_model, {"input_tokens": 12, "output_tokens": 4})

        class Handlers:
            def __init__(self):
                self.locks = {}
                self.last_request = {}
                self.learned_style_inputs = []
                owner = self

                class StyleProfiles:
                    async def learn_message(inner_self, user_id, text, *, private_chat, temporary):
                        owner.learned_style_inputs.append((user_id, text, private_chat, temporary))

                self.style_profiles = StyleProfiles()

            async def _profile(self, _user_id, name, *, include_style_profile=False):
                assert include_style_profile
                return UserProfile(name=name, memory_enabled=False)

            def _system_prompt(self, *_args):
                return "You are a test assistant."

            @staticmethod
            def _input_text_chars(messages):
                return sum(len(str(message.get("content", ""))) for message in messages)

        settings = SimpleNamespace(
            telegram_token=TOKEN,
            mini_app_url="https://omega.example",
            mini_app_host="127.0.0.1",
            mini_app_port=8788,
            max_input_chars=1000,
            max_output_chars=1200,
            history_messages=12,
            cooldown_seconds=0,
            user_daily_request_limit=30,
            user_monthly_request_limit=120,
            user_monthly_cost_limit_usd=20.0,
            max_daily_cost_usd=100.0,
            access_mode="public",
            allowed_user_ids=frozenset(),
            effective_admin_user_ids=frozenset(),
        )
        await db.set_global_access_mode("public", 0)
        service = MiniAppServer(settings, db, Router(), Handlers())
        transport = httpx.ASGITransport(app=service.app)
        now = int(__import__("time").time())
        first_headers = {
            "Authorization": f"tma {signed_init_data(77, now=now)}",
            "Origin": "https://omega.example",
            "X-Forwarded-Proto": "https",
        }
        other_headers = {
            "Authorization": f"tma {signed_init_data(88, now=now)}",
            "Origin": "https://omega.example",
            "X-Forwarded-Proto": "https",
        }
        try:
            async with httpx.AsyncClient(
                transport=transport, base_url="http://omega.example",
            ) as client:
                blocked_origin = await client.get(
                    "/api/v1/bootstrap", headers={**first_headers, "Origin": "https://evil.example"},
                )
                assert blocked_origin.status_code == 403
                blocked_host = await client.get(
                    "/api/v1/bootstrap", headers={**first_headers, "Host": "evil.example"},
                )
                assert blocked_host.status_code == 421
                unauthorized = await client.get(
                    "/api/v1/bootstrap", headers={**first_headers, "Authorization": "tma invalid"},
                )
                assert unauthorized.status_code == 401

                index = await client.get("/", headers=first_headers)
                assert index.status_code == 200 and "OMEGA NEXUS" in index.text
                initial = await client.get("/api/v1/bootstrap", headers=first_headers)
                assert initial.status_code == 200
                assert initial.json()["state"]["theme_id"] == "general"
                assert len(initial.json()["themes"]) >= 9

                manual = await client.post(
                    "/api/v1/theme", headers=first_headers,
                    json={"action": "mode", "mode": "manual", "theme_id": "chemistry"},
                )
                assert manual.status_code == 200
                assert manual.json()["state"]["theme_id"] == "chemistry"
                other = await client.get("/api/v1/bootstrap", headers=other_headers)
                assert other.json()["state"]["mode"] == "auto"
                assert other.json()["state"]["theme_id"] == "general"

                tasks_initial = await client.get("/api/v1/tasks", headers=first_headers)
                assert tasks_initial.status_code == 200
                assert tasks_initial.json()["tasks"] == []
                created = await client.post(
                    "/api/v1/tasks", headers=first_headers,
                    json={"action": "add", "text": "Collect requirements", "priority": "high"},
                )
                assert created.status_code == 200
                prerequisite_id = created.json()["task_id"]
                dependent = await client.post(
                    "/api/v1/tasks", headers=first_headers,
                    json={
                        "action": "add", "text": "Implement feature",
                        "depends_on": [prerequisite_id],
                    },
                )
                dependent_id = dependent.json()["task_id"]
                dependent_state = next(
                    task for task in dependent.json()["tasks"] if task["id"] == dependent_id
                )
                assert dependent_state["blocked_by"] == [prerequisite_id]
                blocked_completion = await client.post(
                    "/api/v1/tasks", headers=first_headers,
                    json={"action": "done", "task_id": dependent_id},
                )
                assert blocked_completion.status_code == 409
                assert (await client.get("/api/v1/tasks", headers=other_headers)).json()["tasks"] == []
                cross_user = await client.post(
                    "/api/v1/tasks", headers=other_headers,
                    json={
                        "action": "add", "text": "Cross-account task",
                        "depends_on": [prerequisite_id],
                    },
                )
                assert cross_user.status_code == 400
                assert (
                    await client.post(
                        "/api/v1/tasks", headers=first_headers,
                        json={"action": "done", "task_id": prerequisite_id},
                    )
                ).status_code == 200
                assert (
                    await client.post(
                        "/api/v1/tasks", headers=first_headers,
                        json={"action": "done", "task_id": dependent_id},
                    )
                ).status_code == 200
                invalid_task = await client.post(
                    "/api/v1/tasks", headers=first_headers,
                    json={"action": "add", "text": "Bad task", "depends_on": [True]},
                )
                assert invalid_task.status_code == 400

                streamed = await client.post(
                    "/api/v1/chat", headers=first_headers,
                    json={"text": "Explain a molecule", "user_id": 88},
                )
                assert streamed.status_code == 200
                assert "event: theme" in streamed.text
                assert '"theme_id":"chemistry"' in streamed.text
                assert "বাংলা উত্তর" in streamed.text and "event: done" in streamed.text
                assert [item["role"] for item in await db.history(77, 10)] == ["user", "assistant"]
                assert await db.history(88, 10) == []
                assert service.handlers.learned_style_inputs == [(77, "Explain a molecule", True, False)]

                learning_off = await client.post(
                    "/api/v1/theme", headers=first_headers,
                    json={"action": "setting", "key": "theme_learning", "value": "off"},
                )
                assert learning_off.status_code == 200
                tracking_off = await client.post(
                    "/api/v1/theme", headers=first_headers,
                    json={"action": "setting", "key": "theme_tracking", "value": "off"},
                )
                assert tracking_off.status_code == 200
                assert tracking_off.json()["state"]["theme_tracking"] == "off"
                reset = await client.post(
                    "/api/v1/theme", headers=first_headers, json={"action": "reset"},
                )
                assert reset.status_code == 200
                assert reset.json()["state"]["mode"] == "auto"
                assert reset.json()["state"]["theme_tracking"] == "on"
        finally:
            await db.close()

    asyncio.run(scenario())
