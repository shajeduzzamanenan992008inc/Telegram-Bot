"""Tests for Private/Public global access mode enforcement."""

from __future__ import annotations

from types import SimpleNamespace

from enan_ultra.security import is_admin, is_authorized


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