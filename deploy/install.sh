#!/usr/bin/env bash
#
# install.sh --- install StratumTap on this (Debian 12 or 13) host.
#
# Run as root ON THE TARGET:
#     sudo bash deploy/install.sh [SOURCE_DIR]
#
# SOURCE_DIR defaults to this script's parent directory (the repo root), which
# is what deploy/deploy.sh passes after rsyncing the repo to /tmp.
#
# The script is idempotent --- run it again to upgrade an existing install.
# It never overwrites /etc/default/stratumtap.
#
# Layout it creates:
#     /opt/stratumtap/app          the source tree
#     /opt/stratumtap/venv         its private virtualenv
#     /etc/default/stratumtap      configuration (created once)
#     /etc/systemd/system/stratumtap.service
#
set -euo pipefail

APP_NAME="stratumtap"
SERVICE="${APP_NAME}.service"
PREFIX="/opt/${APP_NAME}"
APP_DIR="${PREFIX}/app"
VENV_DIR="${PREFIX}/venv"
ENV_FILE="/etc/default/${APP_NAME}"
UNIT_DEST="/etc/systemd/system/${SERVICE}"
RUN_USER="stratumtap"
RUN_GROUP="stratumtap"

RSYNC_EXCLUDES=(
  --exclude '.git'
  --exclude 'node_modules'
  --exclude '.venv'
  --exclude 'venv'
  --exclude 'tests'
  --exclude '__pycache__'
  --exclude '.pytest_cache'
  --exclude '.ruff_cache'
  --exclude '*.egg-info'
  --exclude 'build'
  --exclude 'dist'
)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="${1:-$(cd "${SCRIPT_DIR}/.." && pwd)}"

say()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m warning:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m error:\033[0m %s\n' "$*" >&2; exit 1; }

# --- preflight ------------------------------------------------------------

[ "$(id -u)" -eq 0 ] || die "must be run as root (try: sudo bash $0)"

if [ ! -f "${SRC_DIR}/pyproject.toml" ] || [ ! -d "${SRC_DIR}/stratumtap" ]; then
  die "'${SRC_DIR}' does not look like the StratumTap source tree"
fi
say "Installing from ${SRC_DIR}"

if [ -r /etc/os-release ]; then
  # shellcheck disable=SC1091
  . /etc/os-release
  case "${ID:-}${ID_LIKE:-}" in
    *debian*|*ubuntu*) say "Detected ${PRETTY_NAME:-Debian-like}" ;;
    *) warn "this installer targets Debian/Ubuntu; '${ID:-unknown}' is untested" ;;
  esac
else
  warn "/etc/os-release not found --- assuming Debian-like"
fi

command -v systemctl >/dev/null 2>&1 || die "systemd is required"

# --- packages -------------------------------------------------------------

need_pkgs=()
command -v python3 >/dev/null 2>&1              || need_pkgs+=(python3)
python3 -c 'import venv' >/dev/null 2>&1        || need_pkgs+=(python3-venv)
command -v rsync >/dev/null 2>&1                || need_pkgs+=(rsync)
command -v curl >/dev/null 2>&1                 || need_pkgs+=(curl)

if [ "${#need_pkgs[@]}" -gt 0 ]; then
  say "Installing packages: ${need_pkgs[*]}"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y --no-install-recommends "${need_pkgs[@]}"
else
  say "All required packages already present"
fi

python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
  || die "Python 3.11 or newer is required (found $(python3 -V 2>&1))"

# We deliberately do NOT install or reconfigure chrony/gpsd --- this host is a
# time server and its time stack is not ours to touch.
if ! command -v chronyc >/dev/null 2>&1; then
  warn "chronyc not found --- install the 'chrony' package, or the NTP panels stay empty"
fi
if ! systemctl is-active --quiet gpsd.service 2>/dev/null; then
  warn "gpsd.service is not active --- the GPS panels stay empty (check: systemctl status gpsd)"
fi

# --- migrate a pre-rename install (gps-ntp-visual -> stratumtap) -----------

OLD_NAME="gps-ntp-visual"
if [ -e "/etc/systemd/system/${OLD_NAME}.service" ] || [ -d "/opt/${OLD_NAME}" ]; then
  say "Migrating previous '${OLD_NAME}' install"
  systemctl disable --now "${OLD_NAME}.service" 2>/dev/null || true
  rm -f "/etc/systemd/system/${OLD_NAME}.service"
  if [ -e "/etc/default/${OLD_NAME}" ] && [ ! -e "${ENV_FILE}" ]; then
    sed 's/GPSNTP_/STRATUMTAP_/g' "/etc/default/${OLD_NAME}" > "${ENV_FILE}"
    say "Converted /etc/default/${OLD_NAME} -> ${ENV_FILE}"
  fi
  rm -f "/etc/default/${OLD_NAME}"
  rm -rf "/opt/${OLD_NAME}"
  if id -u gpsntp >/dev/null 2>&1; then userdel gpsntp 2>/dev/null || true; fi
  systemctl daemon-reload
fi

# --- user -----------------------------------------------------------------

if id -u "${RUN_USER}" >/dev/null 2>&1; then
  say "System user '${RUN_USER}' already exists"
else
  say "Creating system user '${RUN_USER}'"
  useradd --system --no-create-home --shell /usr/sbin/nologin "${RUN_USER}"
fi

if getent group _chrony >/dev/null 2>&1; then
  if id -nG "${RUN_USER}" | tr ' ' '\n' | grep -qx '_chrony'; then
    say "'${RUN_USER}' is already in group '_chrony'"
  else
    say "Adding '${RUN_USER}' to group '_chrony'"
    usermod -aG _chrony "${RUN_USER}"
  fi
else
  warn "group '_chrony' does not exist --- chronyc will use UDP 323 on loopback"
fi

# --- files ----------------------------------------------------------------

say "Syncing source into ${APP_DIR}"
install -d -m 0755 "${PREFIX}"
install -d -m 0755 "${APP_DIR}"
rsync -a --delete "${RSYNC_EXCLUDES[@]}" "${SRC_DIR}/" "${APP_DIR}/"
chown -R root:root "${APP_DIR}"
chmod -R a+rX "${APP_DIR}"

# --- virtualenv -----------------------------------------------------------

# A venv is bound to one interpreter version. After an OS upgrade (e.g. Debian 12 ->
# 13 moves python3 from 3.11 to 3.13) the old venv silently breaks: bin/python follows
# /usr/bin/python3, but the packages still live under lib/python3.11. Detect that and
# rebuild rather than "reuse" a venv that can no longer import anything.
sys_pyver="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
venv_ok=0
if [ -x "${VENV_DIR}/bin/python" ]; then
  venv_pyver="$("${VENV_DIR}/bin/python" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || true)"
  if [ "${venv_pyver}" = "${sys_pyver}" ] && [ -d "${VENV_DIR}/lib/python${venv_pyver}/site-packages" ]; then
    venv_ok=1
  fi
fi
if [ "${venv_ok}" -eq 1 ]; then
  say "Reusing virtualenv ${VENV_DIR} (Python ${sys_pyver})"
else
  if [ -d "${VENV_DIR}" ]; then
    old_libs="$(ls -d "${VENV_DIR}"/lib/python* 2>/dev/null | sed 's#.*/python##' | tr '\n' ' ')"
    say "Rebuilding virtualenv ${VENV_DIR}: system Python is ${sys_pyver}, existing packages are for Python ${old_libs:-unknown}"
    rm -rf "${VENV_DIR}"
  else
    say "Creating virtualenv ${VENV_DIR} (Python ${sys_pyver})"
  fi
  python3 -m venv "${VENV_DIR}"
fi

say "Installing Python dependencies (this can take a minute)"
"${VENV_DIR}/bin/pip" install --quiet --upgrade pip wheel
"${VENV_DIR}/bin/pip" install --quiet --upgrade -r "${APP_DIR}/requirements.txt"
chmod -R a+rX "${VENV_DIR}"

# --- configuration --------------------------------------------------------

if [ -e "${ENV_FILE}" ]; then
  say "Keeping existing ${ENV_FILE}"
else
  say "Creating ${ENV_FILE} from the example"
  install -m 0644 "${APP_DIR}/deploy/${APP_NAME}.env.example" "${ENV_FILE}"
fi

# Read the configured port so the health check below hits the right one.
PORT="$( (grep -E '^[[:space:]]*STRATUMTAP_PORT=' "${ENV_FILE}" || true) \
         | tail -n1 | cut -d= -f2- | tr -d '"'"'"' \t\r' )"
PORT="${PORT:-8080}"

# --- systemd --------------------------------------------------------------

say "Installing ${UNIT_DEST}"
install -m 0644 "${APP_DIR}/deploy/${SERVICE}" "${UNIT_DEST}"
if ! getent group _chrony >/dev/null 2>&1; then
  # systemd refuses to start a unit whose SupplementaryGroups= does not exist.
  sed -i '/^SupplementaryGroups=_chrony/d' "${UNIT_DEST}"
fi
systemctl daemon-reload
systemctl enable --quiet --now "${SERVICE}"
systemctl restart "${SERVICE}"

# --- health check ---------------------------------------------------------

HEALTH_URL="http://127.0.0.1:${PORT}/api/v1/health"
say "Waiting for ${HEALTH_URL}"

body=""
for _ in $(seq 1 20); do
  if body="$(curl -fsS --max-time 2 "${HEALTH_URL}" 2>/dev/null)"; then
    break
  fi
  body=""
  sleep 0.5
done

if [ -z "${body}" ]; then
  printf '\033[1;31m error:\033[0m %s did not come up.\n' "${SERVICE}" >&2
  echo "--- systemctl status ---" >&2
  systemctl --no-pager --full status "${SERVICE}" 2>&1 | head -n 20 >&2 || true
  echo "--- journalctl -u ${APP_NAME} -n 50 ---" >&2
  journalctl -u "${APP_NAME}" -n 50 --no-pager >&2 || true
  exit 1
fi

echo
say "Health: ${body}"
printf '\033[1;32m==>\033[0m %s is running: http://%s:%s/\n' \
  "${SERVICE}" "$(hostname -f 2>/dev/null || hostname)" "${PORT}"
echo "    config: ${ENV_FILE}"
echo "    logs:   journalctl -u ${APP_NAME} -f"
