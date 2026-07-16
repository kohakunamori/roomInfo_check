#!/usr/bin/env bash
# Auth container: Xvfb + x11vnc + noVNC + interactive browser login worker.
# Lifecycle: start → browser login/MFA → export session → exit (container stops).
# Do NOT keep idle Chromium; saves RAM/CPU when not authenticating.
set -euo pipefail

cd /app
mkdir -p /app/data /tmp/.X11-unix
chmod 1777 /tmp/.X11-unix || true

export DISPLAY="${DISPLAY:-:99}"
export PYTHONUNBUFFERED=1

# Soft env check — never print secrets
python - <<'PY'
from pathlib import Path
import os
from dotenv import load_dotenv
load_dotenv("/app/.env")
print("[auth-entrypoint] DISPLAY=", os.environ.get("DISPLAY"))
print("[auth-entrypoint] ONLINE_SESSION_FILE=", os.getenv("ONLINE_SESSION_FILE", "./data/.uestc_session.json"))
print("[auth-entrypoint] session_exists=", Path(os.getenv("ONLINE_SESSION_FILE", "./data/.uestc_session.json")).exists())
print("[auth-entrypoint] username_set=", bool(os.getenv("API_USERNAME")))
print("[auth-entrypoint] password_set=", bool(os.getenv("API_PASSWORD")))
print("[auth-entrypoint] AUTH_NOVNC_URL=", os.getenv("AUTH_NOVNC_URL", "http://127.0.0.1:6080/vnc.html"))
PY

# Clean stale X lock
rm -f /tmp/.X99-lock /tmp/.X11-unix/X99 2>/dev/null || true

# Geometry must match Chromium --window-size / Playwright viewport in browser_session.py.
# Do NOT enable x11vnc -ncache: client-side pixel cache inflates the framebuffer to a
# tall strip (≈ height × ncache), so noVNC with resize=scale looks severely narrow.
XVFB_GEOMETRY="${AUTH_XVFB_GEOMETRY:-1600x900x24}"

echo "[auth-entrypoint] starting Xvfb on $DISPLAY geometry=$XVFB_GEOMETRY"
Xvfb "$DISPLAY" -screen 0 "$XVFB_GEOMETRY" -ac +extension RANDR >/tmp/xvfb.log 2>&1 &
XVFB_PID=$!
sleep 1
if ! kill -0 "$XVFB_PID" 2>/dev/null; then
  echo "[auth-entrypoint] Xvfb failed to start" >&2
  cat /tmp/xvfb.log >&2 || true
  exit 1
fi

# Lightweight window manager so Chromium gets decorations / focus
if command -v openbox >/dev/null 2>&1; then
  openbox >/tmp/openbox.log 2>&1 &
fi

echo "[auth-entrypoint] starting x11vnc (no -ncache)"
x11vnc -display "$DISPLAY" -forever -shared -rfbport 5900 -nopw -xkb \
  -geometry "${AUTH_VNC_GEOMETRY:-1600x900}" \
  >/tmp/x11vnc.log 2>&1 &
VNC_PID=$!
sleep 1

NOVNC_ROOT="${NOVNC_HOME:-/usr/share/novnc}"
if [ ! -d "$NOVNC_ROOT" ]; then
  NOVNC_ROOT="/opt/novnc"
fi
echo "[auth-entrypoint] starting websockify/noVNC from $NOVNC_ROOT"
websockify --web="$NOVNC_ROOT" 6080 localhost:5900 >/tmp/websockify.log 2>&1 &
WS_PID=$!
sleep 1

# Resolve Chromium user-data dir (shared volume). Stale Singleton* locks from a
# previous container (different hostname / docker rm -f) make Chromium exit
# immediately → noVNC black screen only. Never delete the whole profile.
resolve_profile_dir() {
  PROFILE_DIR="${ONLINE_BROWSER_PROFILE_DIR:-./data/.uestc_chrome_profile}"
  case "$PROFILE_DIR" in
    .uestc_chrome_profile|./.uestc_chrome_profile)
      PROFILE_DIR="./data/.uestc_chrome_profile"
      ;;
  esac
  printf '%s' "$PROFILE_DIR"
}

clear_chromium_singletons() {
  local dir="$1"
  [ -n "$dir" ] || return 0
  mkdir -p "$dir" 2>/dev/null || true
  # Named files + any leftover Singleton* (lock/cookie/socket variants).
  rm -f \
    "$dir/SingletonLock" \
    "$dir/SingletonCookie" \
    "$dir/SingletonSocket" \
    2>/dev/null || true
  # shellcheck disable=SC2086
  rm -f "$dir"/Singleton* 2>/dev/null || true
}

cleanup() {
  echo "[auth-entrypoint] shutting down"
  # Best-effort: kill any leftover chromium so stop is quick.
  pkill -f "/usr/lib/chromium/chromium" 2>/dev/null || true
  pkill -f "/usr/bin/chromium" 2>/dev/null || true
  # Brief pause so Chromium can release file locks before we strip Singleton*.
  sleep 0.3 2>/dev/null || true
  PROFILE_DIR="$(resolve_profile_dir)"
  clear_chromium_singletons "$PROFILE_DIR"
  kill "$WS_PID" "$VNC_PID" "$XVFB_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# Clear stale profile locks BEFORE launching the browser worker.
PROFILE_DIR="$(resolve_profile_dir)"
mkdir -p "$PROFILE_DIR"
echo "[auth-entrypoint] chromium profile dir=$PROFILE_DIR"
clear_chromium_singletons "$PROFILE_DIR"

echo "[auth-entrypoint] launching auth_worker.py"
set +e
python /app/auth_worker.py
RC=$?
set -e
echo "[auth-entrypoint] auth_worker exit=$RC"

# Brief hold so status file is flushed and operator can glance at the last frame.
# Default short — this container should not linger and burn RAM.
HOLD="${AUTH_HOLD_SECONDS:-20}"
if [ "$HOLD" -gt 0 ] 2>/dev/null; then
  echo "[auth-entrypoint] holding ${HOLD}s before exit (container will stop)"
  sleep "$HOLD"
fi

exit "$RC"
