#!/bin/sh
# Host-side / sidecar lifecycle helper for roominfo-auth.
# POSIX sh — runs on NAS host bash OR alpine docker:cli.
#
# Why: roominfo has no docker.sock. Dashboard (AUTH_MODE=manual) writes
# trigger files under ./data/; this loop starts/stops the auth container so
# Chromium only runs while refreshing credentials — 省资源.
#
# Triggers (shared volume):
#   data/.auth_start_request  → docker compose --profile auth up -d roominfo-auth
#   data/.auth_stop_request   → docker compose --profile auth stop/rm roominfo-auth
#
# Also force-stops if auth_status.json is terminal while container is still up
# past AUTH_HOLD_SECONDS, and on overall AUTH_TIMEOUT.
set -eu

ROOT="${AUTH_PROJECT_ROOT:-}"
if [ -z "$ROOT" ]; then
  # Resolve ../ from this script when run from host.
  SCRIPT="$0"
  # $0 may be relative
  case "$SCRIPT" in
    /*) ;;
    *) SCRIPT="$(pwd)/$SCRIPT" ;;
  esac
  ROOT="$(cd "$(dirname "$SCRIPT")/.." && pwd)"
fi
cd "$ROOT"

DATA="${AUTH_DATA_DIR:-$ROOT/data}"
mkdir -p "$DATA"

SERVICE="${AUTH_SERVICE_NAME:-roominfo-auth}"
POLL="${AUTH_LIFECYCLE_POLL_SECONDS:-2}"
HOLD="${AUTH_HOLD_SECONDS:-20}"
TIMEOUT="${AUTH_TIMEOUT_SECONDS:-900}"
COMPOSE_FILE="${COMPOSE_FILE:-$ROOT/docker-compose.yml}"

START_REQ="$DATA/.auth_start_request"
STOP_REQ="$DATA/.auth_stop_request"
STATUS_FILE="$DATA/auth_status.json"

log() {
  printf '[auth-lifecycle] %s %s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z' 2>/dev/null || date)" "$*"
}

compose() {
  if [ -n "${AUTH_COMPOSE_PROJECT:-}" ]; then
    docker compose -p "$AUTH_COMPOSE_PROJECT" -f "$COMPOSE_FILE" "$@"
  else
    docker compose -f "$COMPOSE_FILE" "$@"
  fi
}

container_running() {
  st="$(docker inspect -f '{{.State.Running}}' "$SERVICE" 2>/dev/null || echo false)"
  [ "$st" = "true" ]
}

start_auth() {
  log "start requested → compose --profile auth up -d $SERVICE"
  if ! compose --profile auth up -d "$SERVICE"; then
    log "up failed; retry once with --build"
    compose --profile auth up -d --build "$SERVICE" || log "up --build also failed"
  fi
  rm -f "$START_REQ"
}

stop_auth() {
  log "stop requested → stop/rm $SERVICE"
  compose --profile auth stop "$SERVICE" >/dev/null 2>&1 || true
  docker rm -f "$SERVICE" >/dev/null 2>&1 || true
  rm -f "$STOP_REQ"
}

# Extract "state":"..." without python (docker:cli has none).
read_state() {
  if [ ! -f "$STATUS_FILE" ]; then
    echo "idle"
    return
  fi
  # Prefer a single-line JSON file; tolerate pretty-printed.
  tr '\n' ' ' <"$STATUS_FILE" 2>/dev/null \
    | sed -n 's/.*"state"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' \
    | head -n 1 \
    | grep -E '^[a-z_]+$' \
    || echo "idle"
}

# Best-effort started_at → epoch via date -d / date -j (may return 0).
read_started_epoch() {
  if [ ! -f "$STATUS_FILE" ]; then
    echo 0
    return
  fi
  ts="$(tr '\n' ' ' <"$STATUS_FILE" 2>/dev/null \
    | sed -n 's/.*"started_at"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' \
    | head -n 1)"
  if [ -z "$ts" ] || [ "$ts" = "null" ]; then
    echo 0
    return
  fi
  # Normalize Z → +0000 for busybox/GNU date.
  ts_norm="$(printf '%s' "$ts" | sed 's/Z$/+00:00/')"
  epoch="$(date -d "$ts_norm" +%s 2>/dev/null || true)"
  if [ -n "$epoch" ]; then
    echo "$epoch"
    return
  fi
  echo 0
}

now_epoch() {
  date +%s 2>/dev/null || echo 0
}

TERMINAL_SINCE=""
log "watching $DATA (poll=${POLL}s service=$SERVICE hold=${HOLD}s timeout=${TIMEOUT}s)"
log "project root=$ROOT"

while true; do
  if [ -f "$STOP_REQ" ]; then
    stop_auth
    TERMINAL_SINCE=""
  fi

  if [ -f "$START_REQ" ]; then
    start_auth
    TERMINAL_SINCE=""
  fi

  if container_running; then
    state="$(read_state)"
    case "$state" in
      success|failed|idle)
        now="$(now_epoch)"
        if [ -z "$TERMINAL_SINCE" ]; then
          TERMINAL_SINCE="$now"
          log "terminal state=$state while container still up; will force-stop in ${HOLD}s"
        else
          elapsed=$((now - TERMINAL_SINCE))
          if [ "$elapsed" -ge "$HOLD" ]; then
            log "force-stop after terminal state=$state"
            stop_auth
            TERMINAL_SINCE=""
          fi
        fi
        ;;
      starting|running|waiting_mfa|waiting_host)
        TERMINAL_SINCE=""
        started="$(read_started_epoch)"
        now="$(now_epoch)"
        if [ "$started" -gt 0 ] 2>/dev/null; then
          age=$((now - started))
          limit=$((TIMEOUT + HOLD + 60))
          if [ "$age" -gt "$limit" ]; then
            log "auth session exceeded ${limit}s (age=${age}); force-stop"
            stop_auth
          fi
        fi
        ;;
      *)
        TERMINAL_SINCE=""
        ;;
    esac
  else
    TERMINAL_SINCE=""
  fi

  sleep "$POLL"
done
