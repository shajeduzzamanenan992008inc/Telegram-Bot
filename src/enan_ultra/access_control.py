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
        """Check if the global access mode is set to private."""
        mode = await self.db.get_global_access_mode()
        return mode == "private"

    async def is_access_allowed(self, user_id: int) -> tuple[bool, str]:
        """
        Check if a user is allowed to access protected bot functionality.
        
        Returns:
            tuple: (allowed, reason) where reason explains the denial if not allowed.
        """
        # Always allow administrators
        if is_admin(self.settings, user_id):
            return True, "admin"
        
        # If access_mode is explicitly "public" in settings, allow access
        # (this maintains backward compatibility with existing configuration)
        if getattr(self.settings, "access_mode", "public") == "public":
            # Still check if user is blocked
            if await self.db.is_user_blocked(user_id):
                return False, "blocked"
            return True, "allowed"
        
        # Check if user is blocked
        if await self.db.is_user_blocked(user_id):
            return False, "blocked"
        
        # Check global access mode (database-persisted)
        private_mode = await self.is_private_mode()
        if private_mode:
            return False, "private_mode"
        
        # Check existing authorization
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
            return False
        
        return True


class AccessDeniedError(Exception):
    """Exception raised when access is denied by the access control service."""
    
    def __init__(self, message: str, reason: str) -> None:
        super().__init__(message)
        self.reason = reason