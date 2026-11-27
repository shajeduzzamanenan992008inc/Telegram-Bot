"""Helpers for running uvicorn servers inside the Telegram bot's own event loop."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from typing import Any

import uvicorn


@contextlib.contextmanager
def _no_signal_capture() -> Iterator[None]:
    yield


def build_embedded_server(app: Any, *, host: str, port: int) -> uvicorn.Server:
    """Create a loopback uvicorn server that never touches process signal handlers.

    python-telegram-bot owns SIGINT/SIGTERM and shuts the servers down explicitly through
    ``server.should_exit``. uvicorn >= 0.29 installs its own handlers in ``capture_signals()``
    (older releases used ``install_signal_handlers()``); left enabled, each embedded server
    would replace the bot's handlers and a stop request would be delivered to the wrong place.
    Both hooks are disabled so the behaviour is identical on every supported uvicorn version.
    """
    server = uvicorn.Server(uvicorn.Config(
        app, host=host, port=port, log_level="warning", access_log=False, lifespan="off",
    ))
    server.install_signal_handlers = lambda: None  # uvicorn < 0.29
    server.capture_signals = _no_signal_capture  # uvicorn >= 0.29
    return server
