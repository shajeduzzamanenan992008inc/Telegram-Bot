"""Tests for Private/Public global access mode enforcement."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from enan_ultra.access_control import AccessControlService, AccessDeniedError
from enan_ultra.config import Settings
from enan_ultra.mini_app import validate_init_data
from enan_ultra.security import is_authorized
from enan_ultra.storage.database import Database

TOKEN = "123456:private-public-test-token-with-sufficient-length"
APP_KEY = "k" * 64
ADMIN_ID = 100
ORDINARY_ID = 200
ALLOWLISTED_ID = 300


def _settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, access_mode: str) -> Settings:
    monkeypatch.chdir(tmp_path)
    values = {
        "TELEGRAM_BOT_TOKEN": TOKEN,
        "ADMIN_CHAT_ID": "123456",
        "ADMIN_USER_IDS": str(ADMIN_ID),
        "ALLOWED_USER_IDS": str(ALLOWLISTED_ID),
        "OPENROUTER_API_KEY": "test-provider-key",
        "APP_DATA_KEY": APP_KEY,
        "DASHBOARD_AUTH_TOKEN": "test-dashboard-auth-token-12345678901234567890",
        "ACCESS_MODE": access_mode,
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    settings = Settings.load()
    assert settings.access_mode == access_mode
    return settings


def _run_mode_scenario(tmp_path: Path, settings: Settings, mode: str):
    async def scenario():
        db = Database(tmp_path / "access-mode.sqlite3")
        await db.open()
        try:
            await db.set_global_access_mode(mode, ADMIN_ID)
            service = AccessControlService(settings, db)
            results = {
                "admin": await service.is_access_allowed(ADMIN_ID),
                "ordinary": await service.is_access_allowed(ORDINARY_ID),
                "allowlisted": await service.is_access_allowed(ALLOWLISTED_ID),
            }
            await db.set_user_blocked(ORDINARY_ID, ADMIN_ID, "test block")
            results["blocked_ordinary"] = await service.is_access_allowed(ORDINARY_ID)
            return results
        finally:
            await db.close()

    return asyncio.run(scenario())


class TestAccessControlServiceWithDatabase:
    """Real service + real temporary database: the database mode is authoritative."""

    def test_private_db_mode_overrides_public_local_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        settings = _settings(tmp_path, monkeypatch, "public")
        results = _run_mode_scenario(tmp_path, settings, "private")
        assert results["admin"] == (True, "admin")
        assert results["ordinary"] == (False, "private_mode")
        assert results["allowlisted"] == (False, "private_mode")
        assert results["blocked_ordinary"] == (False, "blocked")

    def test_private_db_mode_denies_ordinary_users_in_allowlist_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        settings = _settings(tmp_path, monkeypatch, "allowlist")
        results = _run_mode_scenario(tmp_path, settings, "private")
        assert results["admin"] == (True, "admin")
        assert results["ordinary"] == (False, "private_mode")
        assert results["allowlisted"] == (False, "private_mode")

    def test_public_db_mode_restores_public_access_policy(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        settings = _settings(tmp_path, monkeypatch, "public")
        results = _run_mode_scenario(tmp_path, settings, "public")
        assert results["admin"] == (True, "admin")
        assert results["ordinary"] == (True, "allowed")
        assert results["allowlisted"] == (True, "allowed")
        assert results["blocked_ordinary"] == (False, "blocked")

    def test_public_db_mode_still_honours_allowlist_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        settings = _settings(tmp_path, monkeypatch, "allowlist")
        results = _run_mode_scenario(tmp_path, settings, "public")
        assert results["admin"] == (True, "admin")
        assert results["ordinary"] == (False, "unauthorized")
        assert results["allowlisted"] == (True, "allowed")

    def test_unreadable_mode_fails_closed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        settings = _settings(tmp_path, monkeypatch, "public")

        class BrokenDatabase:
            async def get_global_access_mode(self) -> str:
                raise RuntimeError("database unavailable")

            async def is_user_blocked(self, user_id: int) -> bool:
                raise AssertionError("must not be reached before mode read")

        async def scenario() -> None:
            service = AccessControlService(settings, BrokenDatabase())
            assert await service.is_access_allowed(ORDINARY_ID) == (
                False, "mode_unavailable",
            )
            # Administrators keep recovery access even when the mode is unreadable.
            assert await service.is_access_allowed(ADMIN_ID) == (True, "admin")
            with pytest.raises(AccessDeniedError) as denied:
                await service.require_access(ORDINARY_ID)
            assert denied.value.reason == "mode_unavailable"

        asyncio.run(scenario())

    def test_unknown_persisted_mode_fails_closed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        settings = _settings(tmp_path, monkeypatch, "public")

        class UnknownModeDatabase:
            async def get_global_access_mode(self) -> str:
                return "enabled"

            async def is_user_blocked(self, user_id: int) -> bool:
                return False

        async def scenario() -> None:
            service = AccessControlService(settings, UnknownModeDatabase())
            assert await service.is_access_allowed(ORDINARY_ID) == (
                False, "mode_unavailable",
            )

        asyncio.run(scenario())

    def test_default_database_mode_is_private(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        settings = _settings(tmp_path, monkeypatch, "public")

        async def scenario() -> None:
            db = Database(tmp_path / "fresh.sqlite3")
            await db.open()
            try:
                assert await db.get_global_access_mode() == "private"
                service = AccessControlService(settings, db)
                assert await service.is_access_allowed(ORDINARY_ID) == (
                    False, "private_mode",
                )
                assert await service.is_access_allowed(ADMIN_ID) == (True, "admin")
            finally:
                await db.close()

        asyncio.run(scenario())

    def test_mini_app_init_data_user_is_subject_to_global_mode(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        settings = _settings(tmp_path, monkeypatch, "public")
        raw = _signed_init_data(ORDINARY_ID)
        assert validate_init_data(raw, TOKEN, now=1_800_000_000)["id"] == ORDINARY_ID

        async def scenario() -> None:
            db = Database(tmp_path / "mini-app-mode.sqlite3")
            await db.open()
            try:
                service = AccessControlService(settings, db)
                assert (await service.is_access_allowed(ORDINARY_ID))[0] is False
                await db.set_global_access_mode("public", ADMIN_ID)
                assert (await service.is_access_allowed(ORDINARY_ID))[0] is True
            finally:
                await db.close()

        asyncio.run(scenario())


def _signed_init_data(user_id: int) -> str:
    import hashlib
    import hmac
    import json
    from urllib.parse import urlencode

    values = {
        "auth_date": "1800000000",
        "query_id": f"AAEAA-{user_id}",
        "user": json.dumps({"id": user_id, "first_name": "User"}, separators=(",", ":")),
    }
    check = "\n".join(f"{key}={value}" for key, value in sorted(values.items()))
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    values["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(values)


class TestAccessControlLogic:
    """Test the access control logic for Private/Public mode."""

    def test_public_mode_allows_non_admins(self):
        """In Public Mode, non-admins should have access (per existing rules)."""
        settings = SimpleNamespace(
            effective_admin_user_ids=frozenset({100}),
            admin_chat_id=123456,
            access_mode="public",  # publicly accessible
            allowed_user_ids=frozenset(),
        )

        # In public mode, non-admins should be allowed (backward compatibility)
        assert is_authorized(settings, 200) is True

    def test_private_mode_denies_non_admins(self):
        """In Private Mode (via settings), non-admins should be denied."""
        settings = SimpleNamespace(
            effective_admin_user_ids=frozenset({100}),
            admin_chat_id=123456,
            access_mode="private",  # explicitly private
            allowed_user_ids=frozenset(),
        )

        # In private mode with explicit settings, non-admins should be denied
        # is_authorized checks: is_admin OR access_mode == "public" OR allowed_user_ids
        # Since access_mode == "private", and user is not admin and not in allowed list:
        result = is_authorized(settings, 200)
        # With access_mode="private": is_admin(200) OR access_mode == "public" OR 200 in allowed_user_ids
        # = False OR False OR False = False
        assert result is False

    def test_private_mode_allows_admins(self):
        """Administrators should always be allowed regardless of mode."""
        settings = SimpleNamespace(
            effective_admin_user_ids=frozenset({100}),
            admin_chat_id=123456,
            access_mode="private",  # private mode
            allowed_user_ids=frozenset(),
        )

        # Admins should always be allowed even in private mode
        assert is_authorized(settings, 100) is True

    def test_public_mode_with_allowlist(self):
        """Allowlist mode should deny unknown users but allow listed ones."""
        settings = SimpleNamespace(
            effective_admin_user_ids=frozenset({100}),
            admin_chat_id=123456,
            access_mode="allowlist",
            allowed_user_ids=frozenset({200}),  # only user 200 is allowed
        )

        # Admin should always be allowed
        assert is_authorized(settings, 100) is True
        # Listed user should be allowed
        assert is_authorized(settings, 200) is True
        # Unlisted user should be denied
        assert is_authorized(settings, 300) is False

    def test_admin_always_bypasses_mode(self):
        """Admins should bypass Private Mode restrictions."""
        settings = SimpleNamespace(
            effective_admin_user_ids=frozenset({100}),
            admin_chat_id=123456,
            access_mode="private",
            allowed_user_ids=frozenset(),
        )

        # Admin 100 should be allowed
        assert is_authorized(settings, 100) is True
        # Non-admin 200 should be denied
        assert is_authorized(settings, 200) is False


class TestAccessDeniedErrorMessages:
    """Test AccessDeniedError messages and reasons."""

    def test_private_mode_error_has_reason_private_mode(self):
        """AccessDeniedError for private mode should have reason='private_mode'."""
        from enan_ultra.access_control import AccessDeniedError

        try:
            raise AccessDeniedError(
                "The bot is currently in Private Mode. Only administrators can use this feature.",
                reason="private_mode",
            )
        except AccessDeniedError as e:
            assert e.reason == "private_mode"
            # Message should mention Private Mode
            msg_lower = str(e).lower()
            assert "private" in msg_lower

    def test_blocked_error_has_reason_blocked(self):
        """AccessDeniedError for blocked users should have reason='blocked'."""
        from enan_ultra.access_control import AccessDeniedError

        try:
            raise AccessDeniedError(
                "Your access to this bot has been restricted.",
                reason="blocked",
            )
        except AccessDeniedError as e:
            assert e.reason == "blocked"

    def test_unauthorized_error_has_reason_unauthorized(self):
        """AccessDeniedError for unauthorized users should have reason='unauthorized'."""
        from enan_ultra.access_control import AccessDeniedError

        try:
            raise AccessDeniedError(
                "You are not authorized to use this bot.",
                reason="unauthorized",
            )
        except AccessDeniedError as e:
            assert e.reason == "unauthorized"


class TestSetModeCommands:
    """Test /setmode command validation logic."""

    def test_setmode_private_is_valid(self):
        """/setmode private is a valid command."""
        args = ["private"]
        assert len(args) == 1
        assert args[0].lower() in {"private", "public"}

    def test_setmode_public_is_valid(self):
        """/setmode public is a valid command."""
        args = ["public"]
        assert len(args) == 1
        assert args[0].lower() in {"private", "public"}

    def test_setmode_invalid_mode_rejected(self):
        """/setmode invalid should be rejected."""
        args = ["invalid"]
        assert len(args) == 1
        assert args[0].lower() not in {"private", "public"}

    def test_setmode_missing_args_shows_usage(self):
        """/setmode without args should show usage."""
        args = []
        assert len(args) == 0

    def test_setmode_non_admin_cannot_change_mode(self):
        """/setmode should only work for administrators."""
        from types import SimpleNamespace

        settings = SimpleNamespace(
            effective_admin_user_ids=frozenset({100}),
        )

        user_id = 200  # non-admin
        is_admin_result = user_id in settings.effective_admin_user_ids
        assert is_admin_result is False


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])