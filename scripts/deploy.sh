#!/usr/bin/env bash
set -euo pipefail

REMOTE_USER="${REMOTE_USER:-ubuntu}"
REMOTE_HOST="${REMOTE_HOST:-129.154.251.149}"
REMOTE_DIR="${REMOTE_DIR:-/home/ubuntu/sb-backend}"
BRANCH="${BRANCH:-main}"
SERVICE_NAME="${SERVICE_NAME:-storybook-backend}"
STARTUP_TEXT="${STARTUP_TEXT:-INFO:     Application startup complete.}"
LOG_TIMEOUT_SECONDS="${LOG_TIMEOUT_SECONDS:-120}"

KEY_PATH="${KEY_PATH:-}"
KEY_CANDIDATES=(
  "$KEY_PATH"
  "/mnt/d/oracle_server_arm_vc/ssh-key-2026-02-19.key"
  "/d/oracle_server_arm_vc/ssh-key-2026-02-19.key"
  "D:/oracle_server_arm_vc/ssh-key-2026-02-19.key"
)

find_key_file() {
  local candidate

  for candidate in "${KEY_CANDIDATES[@]}"; do
    if [[ -n "$candidate" && -f "$candidate" ]]; then
      printf "%s" "$candidate"
      return 0
    fi
  done

  printf "Could not find SSH key. Set KEY_PATH or place it at D:\\oracle_server_arm_vc\\ssh-key-2026-02-19.key\n" >&2
  return 1
}

shell_quote() {
  printf "'%s'" "$(printf "%s" "$1" | sed "s/'/'\\\\''/g")"
}

main() {
  local key_file remote remote_env

  key_file="$(find_key_file)"
  remote="$REMOTE_USER@$REMOTE_HOST"
  remote_env="REMOTE_DIR=$(shell_quote "$REMOTE_DIR") BRANCH=$(shell_quote "$BRANCH") SERVICE_NAME=$(shell_quote "$SERVICE_NAME") STARTUP_TEXT=$(shell_quote "$STARTUP_TEXT") LOG_TIMEOUT_SECONDS=$(shell_quote "$LOG_TIMEOUT_SECONDS")"

  printf "Deploying backend on %s...\n" "$remote"
  ssh -i "$key_file" "$remote" "$remote_env bash -s" <<'REMOTE_SCRIPT'
set -euo pipefail

printf "Changing to %s...\n" "$REMOTE_DIR"
cd "$REMOTE_DIR"

printf "Updating %s from origin/%s...\n" "$REMOTE_DIR" "$BRANCH"
git fetch origin "$BRANCH"
git checkout "$BRANCH"
git pull --ff-only origin "$BRANCH"

since="$(date --iso-8601=seconds)"

printf "Restarting %s...\n" "$SERVICE_NAME"
sudo systemctl restart "$SERVICE_NAME"

printf "Following %s logs until startup completes...\n" "$SERVICE_NAME"
set +e
timeout "$LOG_TIMEOUT_SECONDS" bash -c '
service_name="$1"
startup_text="$2"
sudo journalctl -u "$service_name" --since "$3" -f --no-pager | while IFS= read -r line; do
  printf "%s\n" "$line"
  if [[ "$line" == *"$startup_text"* ]]; then
    exit 0
  fi
done
' _ "$SERVICE_NAME" "$STARTUP_TEXT" "$since"
log_status=$?
set -e

if [[ "$log_status" -eq 124 ]]; then
  printf "Timed out waiting for startup log: %s\n" "$STARTUP_TEXT" >&2
  printf "Recent logs:\n" >&2
  sudo journalctl -u "$SERVICE_NAME" -n 80 --no-pager >&2
  exit 1
fi

if [[ "$log_status" -ne 0 ]]; then
  printf "Log follow failed with status %s\n" "$log_status" >&2
  sudo systemctl status "$SERVICE_NAME" --no-pager >&2 || true
  exit "$log_status"
fi

printf "Backend deploy complete. Startup confirmed for %s.\n" "$SERVICE_NAME"
REMOTE_SCRIPT
}

main "$@"
