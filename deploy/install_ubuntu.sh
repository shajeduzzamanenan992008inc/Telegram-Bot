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
apt-get install -y clamav clamav-daemon clamav-freshclam ffmpeg poppler-utils tesseract-ocr tesseract-ocr-ben fonts-noto-core rsync pipx python3 python3-venv
systemctl enable --now clamav-freshclam
systemctl enable --now clamav-daemon
if ! id enan-ultra >/dev/null 2>&1; then
  useradd --system --home-dir /var/lib/telegram-ai --shell /usr/sbin/nologin enan-ultra
fi
install -d -m 0755 "${APP_DIR}" "${CONFIG_DIR}"
install -d -m 0755 /usr/local/libexec
rsync -a \
  --exclude='.env' --exclude='.venv' --exclude='data' --exclude='__pycache__' \
  "${SOURCE_DIR}/" "${APP_DIR}/"
chown -R root:root "${APP_DIR}"
chmod -R go-w "${APP_DIR}"

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
install -o root -g root -m 0750 deploy/telegram_ai_deploy.py /usr/local/libexec/telegram-ai-deploy
install -m 0644 deploy/telegram-ai-deploy.service /etc/systemd/system/telegram-ai-deploy.service
install -m 0644 deploy/telegram-ai-deploy.socket /etc/systemd/system/telegram-ai-deploy.socket
install -d -m 0700 -o enan-ultra -g enan-ultra /var/lib/telegram-ai
install -d -m 0700 -o root -g root \
  /var/lib/telegram-ai-deploy \
  /var/lib/telegram-ai-deploy/release-backups \
  /var/lib/telegram-ai-deploy/deployment-history
if [[ ! -f "${CONFIG_FILE}" ]]; then
  install -m 0600 .env.example "${CONFIG_FILE}"
  sed -i 's/^MALWARE_SCAN_ENABLED=false$/MALWARE_SCAN_ENABLED=true/' "${CONFIG_FILE}"
elif ! grep -q '^MALWARE_SCAN_ENABLED=' "${CONFIG_FILE}"; then
  printf '\nMALWARE_SCAN_ENABLED=true\nCLAMAV_SOCKET_PATH=/var/run/clamav/clamd.ctl\n' >> "${CONFIG_FILE}"
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
