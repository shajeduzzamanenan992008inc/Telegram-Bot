#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run with sudo: sudo bash deploy/install_ubuntu.sh" >&2
  exit 1
fi

SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR="/opt/enan-ultra"
CONFIG_DIR="/etc/telegram-ai"
CONFIG_FILE="${CONFIG_DIR}/bot.env"

apt-get update
apt-get install -y ffmpeg poppler-utils tesseract-ocr tesseract-ocr-ben rsync pipx python3 python3-venv
if ! id enan-ultra >/dev/null 2>&1; then
  useradd --system --home-dir /var/lib/telegram-ai --shell /usr/sbin/nologin enan-ultra
fi
install -d -m 0755 "${APP_DIR}" "${CONFIG_DIR}"
rsync -a \
  --exclude='.env' --exclude='.venv' --exclude='data' --exclude='__pycache__' \
  "${SOURCE_DIR}/" "${APP_DIR}/"

if ! command -v uv >/dev/null 2>&1; then
  pipx install uv
  export PATH="/root/.local/bin:${PATH}"
fi
UV_BIN="$(command -v uv || true)"
if [[ -z "${UV_BIN}" ]]; then
  echo "uv was not found after installation." >&2
  exit 1
fi
PYTHON_VERSION="${PYTHON_VERSION:-3.11}"
case "${PYTHON_VERSION}" in
  3.11|3.12|3.13|3.14|3.15) ;;
  *)
    echo "PYTHON_VERSION must be one of 3.11, 3.12, 3.13, 3.14, or 3.15." >&2
    exit 1
    ;;
esac
"${UV_BIN}" python install "${PYTHON_VERSION}"
cd "${APP_DIR}"
"${UV_BIN}" sync --locked --python "${PYTHON_VERSION}" --no-dev --group build
install -m 0644 deploy/telegram-ai.service /etc/systemd/system/telegram-ai.service
install -d -m 0700 -o enan-ultra -g enan-ultra /var/lib/telegram-ai
if [[ ! -f "${CONFIG_FILE}" ]]; then
  install -m 0600 .env.example "${CONFIG_FILE}"
fi
chown root:root "${CONFIG_FILE}"
chmod 0600 "${CONFIG_FILE}"

if ! python3 scripts/prepare_deploy_env.py "${CONFIG_FILE}"; then
  echo "Edit ${CONFIG_FILE} with the required Telegram/admin values and one supported AI provider."
  echo "Then rerun this installer to enable and start the service."
  exit 2
fi
chown root:root "${CONFIG_FILE}"
chmod 0600 "${CONFIG_FILE}"

systemctl daemon-reload
systemctl enable --now telegram-ai.service
systemctl --no-pager --full status telegram-ai.service
