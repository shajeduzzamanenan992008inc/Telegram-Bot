"""Create a private local .env template without replacing existing secrets."""

from __future__ import annotations

import os
import secrets
import shutil
import stat
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    source = root / ".env.example"
    target = root / ".env"
    if target.exists():
        print(".env already exists; left it unchanged.")
        return
    content = source.read_text(encoding="utf-8")
    content = content.replace("APP_DATA_KEY=replace-with-generated-secret", f"APP_DATA_KEY={secrets.token_urlsafe(48)}")
    content = content.replace("DASHBOARD_AUTH_TOKEN=replace-with-generated-secret", f"DASHBOARD_AUTH_TOKEN={secrets.token_urlsafe(48)}")
    target.write_text(content, encoding="utf-8")
    if os.name != "nt":
        target.chmod(stat.S_IRUSR | stat.S_IWUSR)
    print(f"Created {target}. Add real credentials before starting the bot.")
    if os.name == "nt":
        print("Windows uses the current folder ACL; restrict access to your Windows account.")


if __name__ == "__main__":
    main()
