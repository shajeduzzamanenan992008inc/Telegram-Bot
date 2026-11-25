"""Centralized access control service for Private/Public mode enforcement."""

from __future__ import annotations

from typing import Any

from enan_ultra.config import Settings
from enan_ultra.security import is_admin
from enan_ultra.storage.database import Database


class AccessControlService:
    """Centralized service for enforcing global Private/Public access mode."""

    def __init__(self, settings: Settings, db: Database) -> None:
        self.settings = settings
        self.db = db

    async def is_private_mode(self) -> bool:
        """Check if the global access mode is set to private.

        Raises when the authoritative mode cannot be read so callers can fail
        closed rather than silently assume public access.
        """
        mode = await self.db.get_global_access_mode()
        if mode not in {"private", "public"}:
            raise ValueError(f"Unrecognized global access mode: {mode!r}")
        return mode == "private"

    async def is_access_allowed(self, user_id: int) -> tuple[bool, str]:
        """
        Check if a user is allowed to access protected bot functionality.

        Trusted administrators always pass. Everyone else is checked against
        the database-persisted global mode, which is authoritative: local
        ``ACCESS_MODE`` configuration can never bypass a database PRIVATE mode.
        When that mode cannot be read, access is denied (fail closed).

        Returns:
            tuple: (allowed, reason) where reason explains the denial if not allowed.
        """
        # Always allow administrators (they must retain recovery access).
        if is_admin(self.settings, user_id):
            return True, "admin"

        # The database-persisted global mode is authoritative. Read it before
        # any ordinary-user authorization and fail closed on error.
        try:
            mode = await self.db.get_global_access_mode()
        except Exception:
            return False, "mode_unavailable"
        if mode not in {"private", "public"}:
            return False, "mode_unavailable"

        # Blocked users are denied in both modes.
        try:
            if await self.db.is_user_blocked(user_id):
                return False, "blocked"
        except Exception:
            return False, "mode_unavailable"

        if mode == "private":
            return False, "private_mode"

        # PUBLIC mode restores access only according to the existing
        # authorization policy (ACCESS_MODE=allowlist still restricts users).
        from enan_ultra.security import is_authorized
        if not is_authorized(self.settings, user_id):
            return False, "unauthorized"

        return True, "allowed"

    async def require_access(self, user_id: int) -> None:
        """
        Raise an exception if access is not allowed.
        
        This is a convenience method for handler integration.
        """
        allowed, reason = await self.is_access_allowed(user_id)
        if not allowed:
            if reason == "private_mode":
                raise AccessDeniedError(
                    "The bot is currently in Private Mode. Only administrators can use this feature.",
                    reason="private_mode"
                )
            elif reason == "blocked":
                raise AccessDeniedError(
                    "Your access to this bot has been restricted.",
                    reason="blocked"
                )
            elif reason == "unauthorized":
                raise AccessDeniedError(
                    "You are not authorized to use this bot.",
                    reason="unauthorized"
                )
            elif reason == "mode_unavailable":
                raise AccessDeniedError(
                    "Access cannot be verified right now. Please try again later.",
                    reason="mode_unavailable"
                )
            else:
                raise AccessDeniedError(
                    "Access denied.",
                    reason=reason
                )

    async def enforce_private_mode_for_update(self, update: Any) -> bool:
        """
        Enforce private mode for a Telegram update.
        
        Returns True if access is allowed, False if denied (and sends a message).
        """
        user = update.effective_user
        msg = update.effective_message
        
        if not user or not msg:
            return False
        
        allowed, reason = await self.is_access_allowed(user.id)
        if not allowed:
            if reason == "private_mode":
                await msg.reply_text(
                    "🔒 The bot is currently in **Private Mode**.\n"
                    "Only administrators can use this feature at this time."
                )
            elif reason == "blocked":
                await msg.reply_text("Your access to this bot has been restricted.")
            elif reason == "unauthorized":
                await msg.reply_text("You are not authorized to use this bot.")
            elif reason == "mode_unavailable":
                await msg.reply_text(
                    "Access cannot be verified right now. Please try again later."
                )
            return False
        
        return True


class AccessDeniedError(Exception):
    """Exception raised when access is denied by the access control service."""
    
    def __init__(self, message: str, reason: str) -> None:
        super().__init__(message)
        self.reason = reason