"""Telegram command menus, separated by visibility scope.

Command visibility is a presentation feature only. Handler and callback
authorization remains the security boundary.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from telegram import (
    BotCommand,
    BotCommandScopeChat,
    BotCommandScopeChatMember,
    BotCommandScopeDefault,
)

logger = logging.getLogger(__name__)


PUBLIC_COMMANDS = (
    BotCommand("start", "Start the bot"),
    BotCommand("help", "Show commands and help"),
    BotCommand("reset", "Clear conversation history"),
    BotCommand("clear", "Clear conversation memory"),
    BotCommand("live", "Contact an administrator"),
    BotCommand("exit", "End the live session"),
    BotCommand("id", "Show your IDs"),
    BotCommand("voice", "Toggle voice replies"),
    BotCommand("make_sticker", "Turn a photo into a sticker"),
    BotCommand("summary", "Summarize recent group chat"),
    BotCommand("setmode", "Set chat mode or global private/public (admins)"),
    BotCommand("read", "Summarize a public webpage"),
    BotCommand("media", "Toggle media processing"),
    BotCommand("style", "Set reply style"),
    BotCommand("language", "Set reply language"),
    BotCommand("format", "Set the reply format"),
    BotCommand("temporary", "Toggle temporary chat"),
    BotCommand("persona", "Set the assistant persona"),
    BotCommand("timezone", "Set your time zone"),
    BotCommand("memory", "Manage long-term memory"),
    BotCommand("remember", "Save a memory"),
    BotCommand("forget", "Delete memories"),
    BotCommand("files", "List saved files"),
    BotCommand("forget_files", "Delete saved file text"),
    BotCommand("privacy", "Manage privacy and data"),
    BotCommand("remind", "Create a reminder"),
    BotCommand("reminders", "List or manage reminders"),
    BotCommand("cancelreminder", "Cancel a reminder"),
    BotCommand("snooze", "Snooze a pending reminder"),
    BotCommand("tasks", "Manage your task list"),
    BotCommand("search", "Search the live web"),
    BotCommand("research", "Search the live web"),
    BotCommand("historysearch", "Search recent chat history"),
    BotCommand("document", "Document help and saved files"),
    BotCommand("translate", "Translate text"),
    BotCommand("web", "Toggle web search"),
    BotCommand("image", "Generate an image"),
    BotCommand("imagine", "Generate an image"),
    BotCommand("usage", "Show your account usage"),
    BotCommand("cancel", "Stop the current AI response"),
    BotCommand("export", "Export your private conversation"),
    BotCommand("theme", "Choose an adaptive visual theme"),
    BotCommand("app", "Open the OMEGA AI Mini App"),
    BotCommand("agents", "Toggle specialist reviews"),
    BotCommand("study", "Manage study mode"),
    BotCommand("kb", "Manage your personal knowledge base"),
    BotCommand("feedback", "Rate a response"),
    BotCommand("knowledge", "Manage your personal knowledge base"),
    # These commands are public entry points; ActionRegistry checks whether the
    # requester or an administrator is allowed to consume each specific request.
    BotCommand("approve", "Approve a request you are authorized to approve"),
    BotCommand("deny", "Deny a request you are authorized to deny"),
    BotCommand("test", "Request a sandbox project test"),
    BotCommand("patch", "Request a project patch"),
    BotCommand("emoji", "Set emoji frequency"),
    BotCommand("stickers", "Set sticker mode"),
    BotCommand("preferences", "Show your saved preferences"),
    BotCommand("resetpreferences", "Reset presentation preferences"),
    BotCommand("mission", "Start or manage an analysis mission"),
    BotCommand("mission_new", "Start an informational mission"),
    BotCommand("mission_status", "Check a mission status"),
    BotCommand("mission_list", "List your missions"),
    BotCommand("mission_pause", "Pause a mission"),
    BotCommand("mission_resume", "Resume a mission"),
    BotCommand("mission_cancel", "Cancel a mission"),
    BotCommand("mission_report", "Show a mission report"),
    BotCommand("mission_delete", "Delete a mission and its stored details"),
)


ADMIN_COMMANDS = (
    BotCommand("clearuser", "Clear a user's conversation memory"),
    BotCommand("model", "Change the current AI model"),
    BotCommand("models", "List models from configured providers"),
    BotCommand("status", "Bot status"),
    BotCommand("providers", "Configured provider health"),
    BotCommand("providerdiag", "Provider circuit and credential diagnostics"),
    BotCommand("admin", "Open the administrator menu"),
    BotCommand("update", "Control the temporary ZIP upload window"),
    BotCommand("end", "End a user's live session"),
    BotCommand("endall", "End all live sessions"),
    BotCommand("device", "Allowlisted Home Assistant control"),
    BotCommand("vm", "Bot service status/restart"),
    BotCommand("block", "Block a user's bot access"),
    BotCommand("unblock", "Unblock a user's bot access"),
    BotCommand("collect_on", "Enable admin-only sticker collection"),
    BotCommand("collect_off", "Disable sticker collection"),
    BotCommand("import_sticker_set", "Import a sticker pack for review"),
    BotCommand("cancel_sticker_import", "Cancel an active sticker import"),
    BotCommand("sticker_stats", "Show sticker catalog health"),
    BotCommand("sticker_search", "Search sticker metadata"),
    BotCommand("sticker_review", "List stickers pending approval"),
    BotCommand("sticker_categorize", "Categorize and approve a sticker"),
    BotCommand("sticker_export", "Export sticker catalog metadata"),
    BotCommand("evolve_status", "Evolution proposal overview"),
    BotCommand("evolve_pending", "List pending code proposals"),
    BotCommand("evolve_review", "Review an exact proposal diff"),
    BotCommand("evolve_test", "Run an isolated patch test"),
    BotCommand("evolve_approve", "Approve a patch for sandbox staging"),
    BotCommand("evolve_stage", "Verify a staged candidate in isolation"),
    BotCommand("evolve_deploy_approve", "Record separate production approval"),
    BotCommand("evolve_cancel", "Cancel an evolution proposal"),
    BotCommand("evolve_mode", "Pause or resume proposal collection"),
    BotCommand("learning_status", "Show learning signal status"),
    BotCommand("learning_pause", "Pause improvement signal aggregation"),
    BotCommand("learning_resume", "Resume improvement signal aggregation"),
)


# These registered aliases and admin commands remain callable, but stay out of
# menus. Hiding them also keeps each Telegram command scope below its 100-command
# limit. Internal callback actions such as zipupd:approve are not slash commands.
INTERNAL_COMMANDS = (
    BotCommand("resetuser", "Compatibility alias for /clearuser"),
    BotCommand("end_all", "Compatibility alias for /endall"),
    BotCommand("evolve_export", "Download a verified candidate or source archive"),
)


def _menu_for_admin(*, include_update: bool) -> list[BotCommand]:
    combined = (*PUBLIC_COMMANDS, *ADMIN_COMMANDS)
    return [
        command for command in combined
        if include_update or command.command != "update"
    ]


def _menu_for_primary_update_admin(*, include_update: bool) -> list[BotCommand]:
    if not include_update:
        return list(PUBLIC_COMMANDS)
    update_command = next(command for command in ADMIN_COMMANDS if command.command == "update")
    return [*PUBLIC_COMMANDS, update_command]


def _admin_scope_entries(settings: Any) -> Iterable[tuple[Any, int, bool]]:
    group_chat = settings.admin_chat_id < 0
    general_admin_ids = set(settings.effective_admin_user_ids)
    configured_admin_ids = set(general_admin_ids)
    if settings.admin_id > 0:
        configured_admin_ids.add(settings.admin_id)

    for admin_id in sorted(configured_admin_ids):
        # Private admin chats always get a user-specific Chat scope.
        private_update_access = (
            (admin_id in general_admin_ids or admin_id == settings.admin_id)
            and settings.admin_chat_id == admin_id
        )
        # ADMIN_ID may be dedicated to ZIP updates without being in the
        # general administrator allowlist.
        yield BotCommandScopeChat(admin_id), admin_id, private_update_access
        # In a group admin chat, a Chat scope would reveal admin commands to
        # every member. A ChatMember scope overrides the public group scope.
        if group_chat:
            yield (
                BotCommandScopeChatMember(settings.admin_chat_id, admin_id),
                admin_id,
                admin_id in general_admin_ids or admin_id == settings.admin_id,
            )


def _scope_state_path(settings: Any) -> Path | None:
    database_path = getattr(settings, "database_path", None)
    if not database_path:
        return None
    return Path(database_path).parent / ".telegram-command-menu-scopes.json"


def _read_scope_state(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            raise ValueError("invalid scope state")
        admin_chat_id = state.get("admin_chat_id")
        admin_ids = state.get("admin_user_ids")
        if (
            not isinstance(admin_chat_id, int)
            or not isinstance(admin_ids, list)
            or any(not isinstance(admin_id, int) or admin_id <= 0 for admin_id in admin_ids)
        ):
            raise ValueError("invalid scope state")
        return {"admin_chat_id": admin_chat_id, "admin_user_ids": set(admin_ids)}
    except FileNotFoundError:
        return None
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        logger.warning("Telegram command-scope history could not be read (%s)", type(exc).__name__)
        return {"invalid": True}


def _write_scope_state(path: Path | None, *, admin_chat_id: int, admin_ids: set[int]) -> bool:
    if path is None:
        return True
    temporary_path: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent,
        )
        temporary_path = Path(temporary_name)
        if os.name != "nt":
            os.chmod(temporary_path, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump({
                "admin_chat_id": admin_chat_id,
                "admin_user_ids": sorted(admin_ids),
            }, stream, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
        return True
    except OSError as exc:
        logger.warning("Telegram command-scope history could not be saved (%s)", type(exc).__name__)
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
        return False


async def register_command_menus(
    bot: Any, settings: Any, *, state_path: Path | None = None,
) -> None:
    """Apply public and per-admin scopes; invoke this after startup and updates."""
    if state_path is None:
        state_path = _scope_state_path(settings)
    previous = _read_scope_state(state_path)
    general_admin_ids = set(settings.effective_admin_user_ids)
    admin_ids = set(general_admin_ids)
    if settings.admin_id > 0:
        admin_ids.add(settings.admin_id)

    scopes_ok = True
    try:
        result = await bot.set_my_commands(
            list(PUBLIC_COMMANDS), scope=BotCommandScopeDefault(),
        )
        if result is False:
            scopes_ok = False
            logger.warning("Telegram public command menu update returned false")
    except Exception as exc:
        scopes_ok = False
        logger.warning("Telegram public command menu update failed (%s)", type(exc).__name__)

    scopes: list[tuple[Any, list[BotCommand], str]] = []
    if settings.admin_chat_id < 0:
        # Replace any older broad group menu with public commands only.
        scopes.append((BotCommandScopeChat(settings.admin_chat_id), list(PUBLIC_COMMANDS), "group"))
    for scope, admin_id, include_update in _admin_scope_entries(settings):
        if admin_id in general_admin_ids:
            commands = _menu_for_admin(include_update=include_update)
        else:
            commands = _menu_for_primary_update_admin(include_update=include_update)
        scopes.append((scope, commands, "admin"))

    for scope, commands, label in scopes:
        try:
            result = await bot.set_my_commands(commands, scope=scope)
            if result is False:
                scopes_ok = False
                logger.warning("Telegram %s command menu update returned false", label)
        except Exception as exc:
            scopes_ok = False
            logger.warning(
                "Telegram %s command menu update failed (%s)", label, type(exc).__name__,
            )

    if previous and not previous.get("invalid"):
        previous_ids = set(previous["admin_user_ids"])
        previous_chat_id = previous["admin_chat_id"]
        stale_ids = previous_ids - admin_ids
        stale_scopes: list[Any] = [BotCommandScopeChat(admin_id) for admin_id in sorted(stale_ids)]
        if previous_chat_id < 0:
            old_member_ids = (
                previous_ids if previous_chat_id != settings.admin_chat_id else stale_ids
            )
            stale_scopes.extend(
                BotCommandScopeChatMember(previous_chat_id, admin_id)
                for admin_id in sorted(old_member_ids)
            )
        if previous_chat_id != settings.admin_chat_id:
            if previous_chat_id < 0 or previous_chat_id not in admin_ids:
                stale_scopes.append(BotCommandScopeChat(previous_chat_id))
        for scope in stale_scopes:
            try:
                result = await bot.delete_my_commands(scope=scope)
                if result is False:
                    scopes_ok = False
                    logger.warning("Stale Telegram command scope cleanup returned false")
            except Exception as exc:
                scopes_ok = False
                logger.warning(
                    "Stale Telegram command scope cleanup failed (%s)", type(exc).__name__,
                )

    if scopes_ok and not (previous and previous.get("invalid")):
        _write_scope_state(
            state_path, admin_chat_id=settings.admin_chat_id, admin_ids=admin_ids,
        )
