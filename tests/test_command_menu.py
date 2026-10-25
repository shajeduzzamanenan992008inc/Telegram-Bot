from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from telegram import BotCommandScopeChat, BotCommandScopeChatMember, BotCommandScopeDefault
from telegram.error import TelegramError
from telegram.ext import CommandHandler

from enan_ultra.command_menu import (
    ADMIN_COMMANDS,
    INTERNAL_COMMANDS,
    PUBLIC_COMMANDS,
    register_command_menus,
)
from enan_ultra.handlers import BotHandlers


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        admin_chat_id=-2000,
        admin_id=100,
        effective_admin_user_ids=frozenset({100, 101}),
    )


def test_command_scopes_show_public_commands_to_users_and_admin_commands_only_to_admins() -> None:
    async def scenario() -> None:
        bot = SimpleNamespace(set_my_commands=AsyncMock())
        await register_command_menus(bot, _settings())

        calls = bot.set_my_commands.await_args_list
        default_call = next(
            call for call in calls if isinstance(call.kwargs["scope"], BotCommandScopeDefault)
        )
        public_names = {command.command for command in default_call.args[0]}
        admin_names = {command.command for command in ADMIN_COMMANDS}
        hidden_names = {command.command for command in INTERNAL_COMMANDS}

        assert len(default_call.args[0]) < 100
        assert not public_names & (admin_names | hidden_names)
        assert {
            "update", "block", "unblock", "providerdiag", "learning_pause", "evolve_mode",
        } <= admin_names
        assert {"resetuser", "end_all", "evolve_export"} <= hidden_names

        group_scope_call = next(
            call for call in calls
            if isinstance(call.kwargs["scope"], BotCommandScopeChat)
            and call.kwargs["scope"].chat_id == -2000
        )
        assert {command.command for command in group_scope_call.args[0]} == public_names

        member_calls = [
            call for call in calls
            if isinstance(call.kwargs["scope"], BotCommandScopeChatMember)
        ]
        assert {call.kwargs["scope"].user_id for call in member_calls} == {100, 101}
        for call in member_calls:
            names = {command.command for command in call.args[0]}
            assert public_names <= names
            assert admin_names - {"update"} <= names
            assert "update" in names
            assert not names & hidden_names

        private_calls = [
            call for call in calls
            if isinstance(call.kwargs["scope"], BotCommandScopeChat)
            and call.kwargs["scope"].chat_id in {100, 101}
        ]
        assert len(private_calls) == 2
        assert all("update" not in {item.command for item in call.args[0]} for call in private_calls)
        assert all(len(call.args[0]) <= 100 for call in calls)

    asyncio.run(scenario())


def test_private_admin_chat_gets_admin_scope_without_group_scope() -> None:
    async def scenario() -> None:
        settings = SimpleNamespace(
            admin_chat_id=100,
            admin_id=100,
            effective_admin_user_ids=frozenset({100}),
        )
        bot = SimpleNamespace(set_my_commands=AsyncMock())
        await register_command_menus(bot, settings)

        calls = bot.set_my_commands.await_args_list
        private_scope = next(
            call for call in calls
            if isinstance(call.kwargs["scope"], BotCommandScopeChat)
        )
        assert "update" in {item.command for item in private_scope.args[0]}
        assert not any(
            isinstance(call.kwargs["scope"], BotCommandScopeChatMember)
            for call in calls
        )

    asyncio.run(scenario())


def test_primary_update_admin_gets_only_update_scope_when_not_general_admin() -> None:
    async def scenario() -> None:
        settings = SimpleNamespace(
            admin_chat_id=-2000,
            admin_id=100,
            effective_admin_user_ids=frozenset({101}),
        )
        bot = SimpleNamespace(set_my_commands=AsyncMock())
        await register_command_menus(bot, settings)

        member_calls = {
            call.kwargs["scope"].user_id: call
            for call in bot.set_my_commands.await_args_list
            if isinstance(call.kwargs["scope"], BotCommandScopeChatMember)
        }
        primary_names = {
            command.command for command in member_calls[100].args[0]
        }
        secondary_names = {
            command.command for command in member_calls[101].args[0]
        }
        assert "update" in primary_names
        assert not (admin_names := {command.command for command in ADMIN_COMMANDS} - {"update"}) & primary_names
        assert admin_names <= secondary_names
        assert "update" in secondary_names

    asyncio.run(scenario())


def test_removed_admin_scopes_are_deleted_and_registry_is_updated(tmp_path) -> None:
    async def scenario() -> None:
        state_path = tmp_path / "menu-scopes.json"
        state_path.write_text(json.dumps({
            "admin_chat_id": -2000,
            "admin_user_ids": [100, 101, 102],
        }), encoding="utf-8")
        settings = SimpleNamespace(
            admin_chat_id=-2000,
            admin_id=100,
            effective_admin_user_ids=frozenset({100, 101}),
        )
        bot = SimpleNamespace(
            set_my_commands=AsyncMock(return_value=True),
            delete_my_commands=AsyncMock(return_value=True),
        )

        await register_command_menus(bot, settings, state_path=state_path)

        removed_scopes = [call.kwargs["scope"] for call in bot.delete_my_commands.await_args_list]
        assert len(removed_scopes) == 2
        assert any(
            isinstance(scope, BotCommandScopeChat) and scope.chat_id == 102
            for scope in removed_scopes
        )
        assert any(
            isinstance(scope, BotCommandScopeChatMember)
            and scope.chat_id == -2000 and scope.user_id == 102
            for scope in removed_scopes
        )
        assert json.loads(state_path.read_text(encoding="utf-8")) == {
            "admin_chat_id": -2000,
            "admin_user_ids": [100, 101],
        }

    asyncio.run(scenario())


def test_admin_chat_change_clears_old_group_scopes(tmp_path) -> None:
    async def scenario() -> None:
        state_path = tmp_path / "menu-scopes.json"
        state_path.write_text(json.dumps({
            "admin_chat_id": -2000,
            "admin_user_ids": [100, 101],
        }), encoding="utf-8")
        settings = SimpleNamespace(
            admin_chat_id=-3000,
            admin_id=100,
            effective_admin_user_ids=frozenset({100, 101}),
        )
        bot = SimpleNamespace(
            set_my_commands=AsyncMock(return_value=True),
            delete_my_commands=AsyncMock(return_value=True),
        )

        await register_command_menus(bot, settings, state_path=state_path)

        removed_scopes = [call.kwargs["scope"] for call in bot.delete_my_commands.await_args_list]
        assert any(
            isinstance(scope, BotCommandScopeChat) and scope.chat_id == -2000
            for scope in removed_scopes
        )
        assert {
            scope.user_id for scope in removed_scopes
            if isinstance(scope, BotCommandScopeChatMember) and scope.chat_id == -2000
        } == {100, 101}

    asyncio.run(scenario())


def test_every_registered_slash_command_has_exactly_one_visibility_class() -> None:
    settings = _settings()
    handlers = BotHandlers(settings, None, None, None, None, None)
    app = SimpleNamespace(bot_data={}, add_handler=Mock())
    handlers.install(app)

    registered_commands: list[str] = []
    for call in app.add_handler.call_args_list:
        handler = call.args[0]
        if isinstance(handler, CommandHandler):
            registered_commands.extend(handler.commands)
    registered = set(registered_commands)

    public = {command.command for command in PUBLIC_COMMANDS}
    admin = {command.command for command in ADMIN_COMMANDS}
    internal = {command.command for command in INTERNAL_COMMANDS}

    assert not public & admin
    assert not public & internal
    assert not admin & internal
    assert len(registered_commands) == len(registered)
    assert registered == public | admin | internal


def test_menu_api_errors_are_logged_without_exception_details(caplog) -> None:
    async def scenario() -> None:
        bot = SimpleNamespace(
            set_my_commands=AsyncMock(side_effect=TelegramError("sensitive response detail")),
        )
        await register_command_menus(bot, _settings())
        assert bot.set_my_commands.await_count == 6

    asyncio.run(scenario())
    assert "Telegram public command menu update failed (TelegramError)" in caplog.text
    assert "sensitive response detail" not in caplog.text


def test_manually_typed_admin_command_denies_unauthorized_user_without_side_effect() -> None:
    async def scenario() -> None:
        handlers = object.__new__(BotHandlers)
        handlers.settings = _settings()
        handlers.db = SimpleNamespace(
            is_user_blocked=AsyncMock(return_value=False),
            set_user_blocked=AsyncMock(),
        )
        reply_text = AsyncMock()
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=999),
            effective_chat=SimpleNamespace(id=999),
            effective_message=SimpleNamespace(reply_text=reply_text),
        )
        context = SimpleNamespace(args=["1234", "attempt"])

        await handlers.block_user(update, context)

        reply_text.assert_awaited_once_with(
            "This command is available to authorized administrators only."
        )
        handlers.db.set_user_blocked.assert_not_awaited()
        handlers.db.is_user_blocked.assert_not_awaited()

    asyncio.run(scenario())


def test_manually_typed_update_is_available_to_configured_admin_allowlist() -> None:
    async def scenario() -> None:
        handlers = object.__new__(BotHandlers)
        handlers.settings = _settings()
        handlers.update_manager = SimpleNamespace(admin_ids=frozenset({100, 101}))
        reply_text = AsyncMock()
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=101),
            effective_chat=SimpleNamespace(id=-2000),
            effective_message=SimpleNamespace(reply_text=reply_text),
        )

        await handlers.update(update, SimpleNamespace())

        assert "/update enable|disable|status|cancel" in reply_text.await_args.args[0]

    asyncio.run(scenario())


def test_evolution_mode_is_admin_controlled_and_persisted() -> None:
    async def scenario() -> None:
        handlers = object.__new__(BotHandlers)
        handlers._gate = AsyncMock(return_value=True)
        handlers.db = SimpleNamespace(
            get_setting=AsyncMock(return_value=None),
            set_setting=AsyncMock(),
            audit=AsyncMock(),
        )
        reply_text = AsyncMock()
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=100),
            effective_message=SimpleNamespace(reply_text=reply_text),
        )

        await handlers.evolve_mode(update, SimpleNamespace(args=["off"]))

        handlers.db.set_setting.assert_awaited_once_with("evolution_mode", "off")
        handlers.db.audit.assert_awaited_once_with(
            100, "evolution.mode_off", "proposal_collection", "off",
        )
        assert "proposal mode বন্ধ" in reply_text.await_args.args[0]

        handlers.db.get_setting.return_value = "off"
        reply_text.reset_mock()
        await handlers.evolve_mode(update, SimpleNamespace(args=["status"]))
        assert "paused" in reply_text.await_args.args[0]
        handlers.db.set_setting.assert_awaited_once()

    asyncio.run(scenario())
