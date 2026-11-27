"""Configuration template, installer contract and deployment-environment validation."""

from __future__ import annotations

import re
import subprocess
import sys

import pytest

from conftest import BASE_ENV, ROOT, configuration_names

ENV_EXAMPLE = ROOT / ".env.example"


def _read_env_example() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key, value = stripped.split("=", 1)
            values[key] = value
    return values


def test_env_example_declares_each_setting_once():
    names = configuration_names()
    assert len(names) == len(set(names)), "a variable is declared twice in .env.example"


def test_env_example_covers_every_variable_the_loader_reads():
    source = (ROOT / "src" / "enan_ultra" / "config.py").read_text(encoding="utf-8")
    helpers = r"(?:_value|_bool|_int|_float|_csv|_ids|_model_allowlist|_provider_keys(?:_with_aliases)?)"
    read = set(re.findall(helpers + r'\(\s*"([A-Z][A-Z0-9_]+)"', source))
    read |= set(re.findall(r'_provider_keys(?:_with_aliases)?\(\s*"[A-Z0-9_]+",\s*"([A-Z0-9_]+)"', source))
    missing = sorted(read - set(configuration_names()))
    assert not missing, f".env.example is missing: {missing}"


def test_env_example_is_systemd_and_dotenv_compatible():
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, _, value = stripped.partition("=")
        assert key == key.strip() and re.fullmatch(r"[A-Z][A-Z0-9_]*", key), line
        assert " #" not in value, f"inline comment would end up in the value: {line}"
        assert not value.startswith(("'", '"')), f"quotes are not needed: {line}"


@pytest.mark.parametrize("line", [
    "MALWARE_SCAN_ENABLED=false",
    "DATABASE_PATH=data/telegram_ai.sqlite3",
    "APP_DATA_KEY=replace-with-generated-secret",
    "DASHBOARD_AUTH_TOKEN=replace-with-generated-secret",
    "CLAMAV_SOCKET_PATH=/var/run/clamav/clamd.ctl",
])
def test_installer_marker_lines_exist(line):
    """deploy/install_ubuntu.sh, init_env.py and prepare_deploy_env.py match these exact lines."""
    assert line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()


def test_default_database_name_is_consistent_everywhere():
    script = (ROOT / "scripts" / "prepare_deploy_env.py").read_text(encoding="utf-8")
    installer = (ROOT / "deploy" / "install_ubuntu.sh").read_text(encoding="utf-8")
    assert "/var/lib/telegram-ai/telegram_ai.sqlite3" in script
    assert "/var/lib/telegram-ai/telegram_ai.sqlite3" in installer
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "assistant.sqlite3" not in readme and "assistant.sqlite3" not in script


def test_validate_config_script_passes():
    result = subprocess.run(
        [sys.executable, "scripts/validate_config.py"], cwd=ROOT, capture_output=True, text=True,
        env={"PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin"},
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_example_template_alone_is_not_a_runnable_configuration(make_settings):
    values = _read_env_example()
    with pytest.raises(ValueError):
        make_settings(**{key: value for key, value in values.items() if value != ""} | {
            "ADMIN_CHAT_ID": None, "OPENROUTER_API_KEY": None,
        })


def test_settings_load_with_minimal_environment(make_settings):
    settings = make_settings()
    assert settings.admin_chat_id == 1
    assert settings.provider_order == ("openrouter", "groq", "gemini", "cloudflare", "huggingface")


def test_settings_require_a_provider(make_settings):
    with pytest.raises(ValueError):
        make_settings(OPENROUTER_API_KEY=None)


def test_settings_accept_groq_only_with_model(make_settings):
    settings = make_settings(OPENROUTER_API_KEY=None, GROQ_API_KEY="gsk_x", GROQ_MODEL="llama-3.3-70b-versatile")
    assert settings.groq_model == "llama-3.3-70b-versatile"


def test_settings_reject_bad_token(make_settings):
    with pytest.raises(ValueError):
        make_settings(TELEGRAM_BOT_TOKEN="replace-with-botfather-token")


def _prepare(tmp_path, extra: str) -> subprocess.CompletedProcess:
    template = ENV_EXAMPLE.read_text(encoding="utf-8").replace("DATABASE_PATH=data", "DATABASE_PATH=/var/lib/telegram-ai")
    common = (
        template
        .replace("TELEGRAM_BOT_TOKEN=replace-with-botfather-token", f"TELEGRAM_BOT_TOKEN={BASE_ENV['TELEGRAM_BOT_TOKEN']}")
        .replace("ADMIN_CHAT_ID=\n", "ADMIN_CHAT_ID=1\n")
        .replace("ADMIN_USER_IDS=\n", "ADMIN_USER_IDS=1\n")
    )
    target = tmp_path / "bot.env"
    target.write_text(common + "\n" + extra + "\n", encoding="utf-8")
    return subprocess.run(
        [sys.executable, "scripts/prepare_deploy_env.py", str(target)], cwd=ROOT, capture_output=True, text=True,
    )


@pytest.mark.parametrize("label,extra,expected", [
    ("openrouter", "OPENROUTER_API_KEY=sk-or-x", 0),
    ("groq without model", "GROQ_API_KEY=gsk_x", 2),
    ("groq with model", "GROQ_API_KEY=gsk_x\nGROQ_MODEL=llama-3.3-70b-versatile", 0),
    ("gemini with model", "GEMINI_API_KEY=g_x\nGEMINI_MODEL=gemini-2.5-flash", 0),
    ("cloudflare complete", "CLOUDFLARE_API_TOKENS=cf\nCLOUDFLARE_ACCOUNT_ID=0123456789abcdef0123456789abcdef\nCLOUDFLARE_MODEL=m", 0),
    ("cloudflare without account", "CLOUDFLARE_API_TOKENS=cf\nCLOUDFLARE_MODEL=m", 2),
    ("huggingface", "HUGGINGFACE_API_KEY=hf_x\nHUGGINGFACE_MODEL=org/model", 0),
    ("ollama", "OLLAMA_BASE_URL=http://127.0.0.1:11434\nOLLAMA_MODEL=llama3", 0),
    ("openai only is not routable", "OPENAI_API_KEY=sk-x\nOPENAI_MODEL=gpt", 2),
    ("nothing configured", "", 2),
])
def test_prepare_deploy_env_matches_the_providers_the_app_supports(tmp_path, label, extra, expected):
    result = _prepare(tmp_path, extra)
    assert result.returncode == expected, f"{label}: {result.stdout}{result.stderr}"


def test_init_env_generates_distinct_secrets(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "init_env.py").write_text((ROOT / "scripts" / "init_env.py").read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / ".env.example").write_text(ENV_EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    result = subprocess.run([sys.executable, str(tmp_path / "scripts" / "init_env.py")], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    values = {}
    for line in (tmp_path / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            values[key] = value
    assert len(values["APP_DATA_KEY"]) >= 32 and len(values["DASHBOARD_AUTH_TOKEN"]) >= 32
    assert values["APP_DATA_KEY"] != values["DASHBOARD_AUTH_TOKEN"]
