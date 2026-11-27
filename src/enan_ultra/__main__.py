"""Command line startup entry point."""

from __future__ import annotations

import logging
import json
import sys
from datetime import UTC, datetime
from telegram import Update

from enan_ultra.config import Settings
from enan_ultra.runtime import build_application


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def main() -> None:
    try:
        settings = Settings.load()
    except (ValueError, OSError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    logging.basicConfig(level=getattr(logging, settings.log_level, logging.INFO), handlers=[logging.StreamHandler()], force=True)
    for handler in logging.getLogger().handlers:
        handler.setFormatter(JsonLogFormatter())
    for noisy_logger in ("httpx", "httpcore", "telegram"):
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)
    application = build_application(settings)
    application.run_polling(
        allowed_updates=[*Update.ALL_TYPES, "stopped_message_generation"],
        drop_pending_updates=False,
        close_loop=True,
    )


if __name__ == "__main__":
    main()
