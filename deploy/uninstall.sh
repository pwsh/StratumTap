#!/usr/bin/env bash
#
# uninstall.sh --- remove StratumTap from this host.
#
# Run as root ON THE TARGET:
#     sudo bash deploy/uninstall.sh [--purge]
#
# Stops and disables the service, then removes /opt/stratumtap and the
# systemd unit. /etc/default/stratumtap and the 'stratumtap' system user are
# kept unless --purge is given.
#
set -euo pipefail

APP_NAME="stratumtap"
SERVICE="${APP_NAME}.service"
PREFIX="/opt/${APP_NAME}"
ENV_FILE="/etc/default/${APP_NAME}"
UNIT_DEST="/etc/systemd/system/${SERVICE}"
RUN_USER="stratumtap"
PURGE=0

say()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m error:\033[0m %s\n' "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --purge) PURGE=1; shift ;;
    -h|--help)
      sed -n '3,10p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done

[ "$(id -u)" -eq 0 ] || die "must be run as root (try: sudo bash $0)"

if systemctl list-unit-files --no-legend "${SERVICE}" 2>/dev/null | grep -q .; then
  say "Stopping and disabling ${SERVICE}"
  systemctl disable --quiet --now "${SERVICE}" || true
else
  say "${SERVICE} is not installed"
fi

if [ -e "${UNIT_DEST}" ]; then
  say "Removing ${UNIT_DEST}"
  rm -f "${UNIT_DEST}"
fi
systemctl daemon-reload
systemctl reset-failed "${SERVICE}" 2>/dev/null || true

if [ -d "${PREFIX}" ]; then
  say "Removing ${PREFIX}"
  rm -rf "${PREFIX}"
fi

if [ "${PURGE}" -eq 1 ]; then
  if [ -e "${ENV_FILE}" ]; then
    say "Removing ${ENV_FILE}"
    rm -f "${ENV_FILE}"
  fi
  if id -u "${RUN_USER}" >/dev/null 2>&1; then
    say "Removing system user '${RUN_USER}'"
    userdel "${RUN_USER}" || true
  fi
else
  if [ -e "${ENV_FILE}" ]; then
    say "Keeping ${ENV_FILE} (use --purge to remove it)"
  fi
  if id -u "${RUN_USER}" >/dev/null 2>&1; then
    say "Keeping system user '${RUN_USER}' (use --purge to remove it)"
  fi
fi

say "Done. chrony and gpsd were not touched."
