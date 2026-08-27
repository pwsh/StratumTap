#!/usr/bin/env bash
#
# deploy.sh --- push this working copy to the NTP server and install it.
#
# Run from the DEV MACHINE (not on the target):
#     bash deploy/deploy.sh [--dry-run] [--host HOST] [--user USER] [--port PORT]
#     make deploy
#
# It rsyncs the repo to ${REMOTE_TMP} on the target over ssh, then runs
# deploy/install.sh there under sudo. You need ssh access and sudo rights on
# the target; sudo may prompt for your password (that is why ssh -t is used).
#
# Overridable with environment variables:
#     TARGET_HOST=ntp.example.org TARGET_USER=root bash deploy/deploy.sh
#
set -euo pipefail

TARGET_HOST="${TARGET_HOST:-stratum1.local}"
TARGET_USER="${TARGET_USER:-pi}"
REMOTE_TMP="${REMOTE_TMP:-/tmp/stratumtap-src}"
APP_PORT="${APP_PORT:-8080}"
DRY_RUN=0

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

say() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m error:\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
  sed -n '3,15p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  echo "Defaults: host=${TARGET_HOST} user=${TARGET_USER} port=${APP_PORT} remote-tmp=${REMOTE_TMP}"
  exit "${1:-0}"
}

while [ $# -gt 0 ]; do
  case "$1" in
    -n|--dry-run) DRY_RUN=1; shift ;;
    --host)       TARGET_HOST="${2:?--host needs a value}"; shift 2 ;;
    --user)       TARGET_USER="${2:?--user needs a value}"; shift 2 ;;
    --port)       APP_PORT="${2:?--port needs a value}"; shift 2 ;;
    -h|--help)    usage 0 ;;
    *)            printf 'unknown option: %s\n\n' "$1" >&2; usage 2 ;;
  esac
done

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[ -f "${REPO_ROOT}/pyproject.toml" ] || die "cannot find the repo root from ${BASH_SOURCE[0]}"

command -v rsync >/dev/null 2>&1 || die "rsync is not installed on this machine"
command -v ssh   >/dev/null 2>&1 || die "ssh is not installed on this machine"

REMOTE="${TARGET_USER}@${TARGET_HOST}"

if [ "${DRY_RUN}" -eq 1 ]; then
  say "DRY RUN --- nothing will be changed on ${TARGET_HOST}"
  say "Would sync ${REPO_ROOT}/ -> ${REMOTE}:${REMOTE_TMP}/"
  rsync -az --delete --dry-run --itemize-changes "${RSYNC_EXCLUDES[@]}" \
    "${REPO_ROOT}/" "${REMOTE}:${REMOTE_TMP}/"
  say "Would then run: sudo bash ${REMOTE_TMP}/deploy/install.sh ${REMOTE_TMP}"
  exit 0
fi

say "Syncing ${REPO_ROOT}/ -> ${REMOTE}:${REMOTE_TMP}/"
ssh "${REMOTE}" "mkdir -p '${REMOTE_TMP}'"
rsync -az --delete "${RSYNC_EXCLUDES[@]}" "${REPO_ROOT}/" "${REMOTE}:${REMOTE_TMP}/"

say "Running the installer on ${TARGET_HOST} (sudo may ask for your password)"
ssh -t "${REMOTE}" "sudo bash '${REMOTE_TMP}/deploy/install.sh' '${REMOTE_TMP}'"

echo
printf '\033[1;32m==>\033[0m Deployed. Open http://%s:%s/\n' "${TARGET_HOST}" "${APP_PORT}"
