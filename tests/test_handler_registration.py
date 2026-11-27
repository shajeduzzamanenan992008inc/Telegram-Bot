"""Static consistency between registered commands, the Telegram command menu and handler methods."""

from __future__ import annotations

import ast
import re

from conftest import ROOT

SOURCE = ROOT / "src" / "enan_ultra"


def _registered_commands() -> dict[str, str]:
    text = (SOURCE / "handlers.py").read_text(encoding="utf-8")
    start = text.index("    def install(")
    end = text.index("for name, callback in commands.items()", start)
    return dict(re.findall(r'"([a-z_]+)":\s*self\.([A-Za-z_]+)', text[start:end]))


def _menu_commands() -> set[str]:
    text = (SOURCE / "command_menu.py").read_text(encoding="utf-8")
    return set(re.findall(r'\(\s*"([a-z_]+)"\s*,\s*"', text))


def test_every_registered_command_is_in_the_menu_and_vice_versa():
    registered = set(_registered_commands())
    menu = _menu_commands()
    assert registered, "no commands found; the parser needs updating"
    assert sorted(registered - menu) == []
    assert sorted(menu - registered) == []


def test_every_registered_command_points_at_an_existing_handler_method():
    tree = ast.parse((SOURCE / "handlers.py").read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "BotHandlers")
    methods = {node.name for node in cls.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    missing = sorted(set(_registered_commands().values()) - methods)
    assert missing == []


def test_callback_data_prefixes_stay_within_telegram_limits():
    from enan_ultra.services.telegram_zip_updates import UPDATE_CALLBACK_HASH_PREFIX_LENGTH

    longest = len("zipupd:approve:") + 8 + 1 + UPDATE_CALLBACK_HASH_PREFIX_LENGTH
    assert longest <= 64, "Telegram rejects callback_data longer than 64 bytes"
