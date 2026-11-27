#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run with sudo: sudo bash deploy/install_ubuntu.sh" >&2
  exit 1
fi

SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR="/opt/enan-ultra"
RELEASES_DIR="${APP_DIR}/releases"
CURRENT_LINK="${APP_DIR}/current"
CONFIG_DIR="/etc/telegram-ai"
CONFIG_FILE="${CONFIG_DIR}/bot.env"

if [[ -e "${CURRENT_LINK}" && ! -L "${CURRENT_LINK}" ]] || {
  [[ ! -L "${CURRENT_LINK}" ]] && {
    [[ -d "${APP_DIR}/src" ]] || [[ -d "${APP_DIR}/.venv" ]]
  }
}; then
  echo "An existing flat application tree was found at ${APP_DIR}. Migrate it to a reviewed versioned layout before rerunning this installer." >&2
  exit 3
fi

# This script is a first-install bootstrap only. It is deliberately not an
# upgrade path: never replace an existing versioned release or a running service.
if [[ -e "${CURRENT_LINK}" || -L "${CURRENT_LINK}" ]]; then
  echo "A versioned release already exists at ${CURRENT_LINK}. Use the reviewed broker release flow; this installer is bootstrap-only." >&2
  exit 4
fi
if systemctl is-active --quiet telegram-ai.service; then
  echo "telegram-ai.service is active. This bootstrap installer will not replace or restart it." >&2
  exit 4
fi

apt-get update
apt-get install -y clamav clamav-daemon clamav-freshclam ffmpeg poppler-utils tesseract-ocr tesseract-ocr-ben fonts-noto-core rsync pipx python3 python3-venv
systemctl enable --now clamav-freshclam
systemctl enable --now clamav-daemon
if ! id enan-ultra >/dev/null 2>&1; then
  useradd --system --home-dir /var/lib/telegram-ai --shell /usr/sbin/nologin enan-ultra
fi
install -d -m 0755 "${APP_DIR}" "${RELEASES_DIR}" "${CONFIG_DIR}"
chown root:root "${APP_DIR}" "${RELEASES_DIR}"
chmod 0755 "${APP_DIR}" "${RELEASES_DIR}"
install -d -m 0755 /usr/local/libexec
RELEASE_ID="installer-$(date -u +%Y%m%dT%H%M%SZ)-$$"
RELEASE_DIR="${RELEASES_DIR}/${RELEASE_ID}"
install -d -m 0755 "${RELEASE_DIR}"
rsync -a \
  --exclude='.env' --exclude='.venv' --exclude='.git' --exclude='data' \
  --exclude='uploads' --exclude='user_uploads' --exclude='backups' --exclude='instance' \
  --exclude='storage' --exclude='logs' --exclude='.telegram-zip-updates' \
  --exclude='release-artifacts' --exclude='dist' --exclude='node_modules' \
  --exclude='__pycache__' --exclude='.pytest_cache' --exclude='.ruff_cache' \
  "${SOURCE_DIR}/" "${RELEASE_DIR}/"
chown -R root:root "${RELEASE_DIR}"
chmod -R go-w "${RELEASE_DIR}"

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
UV_PROJECT_ENVIRONMENT="${APP_DIR}/.venv" "${UV_BIN}" sync \
  --project "${RELEASE_DIR}" --locked --python "${PYTHON_VERSION}" --no-dev --group build
install -d -m 0700 -o enan-ultra -g enan-ultra /var/lib/telegram-ai
install -d -m 0700 -o root -g root \
  /var/lib/telegram-ai-deploy \
  /var/lib/telegram-ai-deploy/release-backups \
  /var/lib/telegram-ai-deploy/deployment-history
if [[ ! -f "${CONFIG_FILE}" ]]; then
  install -m 0600 "${RELEASE_DIR}/.env.example" "${CONFIG_FILE}"
  sed -i 's/^MALWARE_SCAN_ENABLED=false$/MALWARE_SCAN_ENABLED=true/' "${CONFIG_FILE}"
elif ! grep -q '^MALWARE_SCAN_ENABLED=' "${CONFIG_FILE}"; then
  printf '\nMALWARE_SCAN_ENABLED=true\nCLAMAV_SOCKET_PATH=/var/run/clamav/clamd.ctl\n' >> "${CONFIG_FILE}"
fi
# The shipped developer default is relative to the read-only application tree. Remap only
# that known default; leave an operator-supplied absolute state path untouched.
if grep -q '^DATABASE_PATH=data/telegram_ai.sqlite3$' "${CONFIG_FILE}"; then
  sed -i 's#^DATABASE_PATH=data/telegram_ai.sqlite3$#DATABASE_PATH=/var/lib/telegram-ai/telegram_ai.sqlite3#' "${CONFIG_FILE}"
elif ! grep -q '^DATABASE_PATH=' "${CONFIG_FILE}"; then
  printf '\nDATABASE_PATH=/var/lib/telegram-ai/telegram_ai.sqlite3\n' >> "${CONFIG_FILE}"
fi
DATABASE_PATH_VALUE="$(sed -n 's/^DATABASE_PATH=//p' "${CONFIG_FILE}" | tail -n 1)"
if [[ "${DATABASE_PATH_VALUE}" != /* ]]; then
  echo "DATABASE_PATH must be absolute and placed in an allowed writable state directory." >&2
  exit 1
fi
case "${DATABASE_PATH_VALUE}" in
  /var/lib/telegram-ai/*) ;;
  *)
    echo "DATABASE_PATH must be inside /var/lib/telegram-ai for the systemd write boundary." >&2
    exit 1
    ;;
esac
chown root:root "${CONFIG_FILE}"
chmod 0600 "${CONFIG_FILE}"

if ! python3 "${RELEASE_DIR}/scripts/prepare_deploy_env.py" "${CONFIG_FILE}"; then
  echo "Edit ${CONFIG_FILE} with the required Telegram/admin values and one supported AI provider."
  echo "Then rerun this installer to enable and start the service."
  exit 2
fi
chown root:root "${CONFIG_FILE}"
chmod 0600 "${CONFIG_FILE}"

NEXT_LINK="${APP_DIR}/.current-${RELEASE_ID}"
ln -s "releases/${RELEASE_ID}" "${NEXT_LINK}"
mv -Tf "${NEXT_LINK}" "${CURRENT_LINK}"

install -m 0644 "${RELEASE_DIR}/deploy/telegram-ai.service" /etc/systemd/system/telegram-ai.service
install -o root -g root -m 0750 "${RELEASE_DIR}/deploy/telegram_ai_deploy.py" /usr/local/libexec/telegram-ai-deploy
install -m 0644 "${RELEASE_DIR}/deploy/telegram-ai-deploy.service" /etc/systemd/system/telegram-ai-deploy.service
install -m 0644 "${RELEASE_DIR}/deploy/telegram-ai-deploy.socket" /etc/systemd/system/telegram-ai-deploy.socket

systemctl daemon-reload
systemctl enable telegram-ai.service
systemctl restart telegram-ai.service
systemctl --no-pager --full status telegram-ai.service
