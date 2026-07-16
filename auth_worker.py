"""Auth container worker: headful browser via DISPLAY/noVNC → Plan A session file.

Never prints cookie values, passwords, tokens, or OAuth codes.
"""

from __future__ import annotations

import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

from auth_control import write_status
from browser_session import (
    BrowserMFARequired,
    BrowserSessionError,
    bootstrap_headful_playwright,
    clear_chromium_profile_locks,
    resolve_project_path,
)
from env import (
    auth_novnc_url,
    auth_timeout_seconds,
    online_browser_executable,
    online_browser_profile_dir,
    online_browser_state_file,
    online_session_file,
    password,
    username,
)
from getInfo import UESTCLogin


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _progress(event: str, payload: dict | None = None):
    payload = payload or {}
    if event == "mfa_required":
        write_status(
            state="waiting_mfa",
            message="IDAS 要求多因素复核：请在 noVNC 中完成微信/短信验证",
            novnc_url=auth_novnc_url,
            event=event,
        )
        print(f"[auth_worker] MFA required at {_now()}", flush=True)
    elif event == "password_submitted":
        write_status(
            state="running",
            message="已提交账号密码，等待门户跳转 / MFA",
            event=event,
        )
        print(f"[auth_worker] password form submitted at {_now()}", flush=True)
    else:
        write_status(state="running", message=f"progress: {event}", event=event)
        print(f"[auth_worker] progress={event} at {_now()}", flush=True)


def run() -> int:
    timeout = max(60, int(os.getenv("AUTH_TIMEOUT_SECONDS", auth_timeout_seconds or 900)))
    state_file = resolve_project_path(
        os.getenv("ONLINE_BROWSER_STATE_FILE", online_browser_state_file),
        ".uestc_browser_state.json",
    )
    # Keep browser profile inside shared data volume when possible.
    profile_raw = os.getenv(
        "ONLINE_BROWSER_PROFILE_DIR",
        online_browser_profile_dir or "./data/.uestc_chrome_profile",
    )
    if profile_raw in {".uestc_chrome_profile", "./.uestc_chrome_profile"}:
        profile_raw = "./data/.uestc_chrome_profile"
    profile_dir = resolve_project_path(profile_raw, "./data/.uestc_chrome_profile")
    # Belt-and-suspenders: entrypoint also clears Singleton*; do it here too so
    # non-entrypoint launches (debug) and racey restarts stay safe.
    profile_dir.mkdir(parents=True, exist_ok=True)
    clear_chromium_profile_locks(profile_dir)
    session_file = os.getenv("ONLINE_SESSION_FILE", online_session_file)

    write_status(
        state="running",
        message="opening headful browser; complete login/MFA in noVNC",
        started_at=_now(),
        novnc_url=auth_novnc_url,
        error=None,
    )
    print(
        f"[auth_worker] start timeout={timeout}s display={os.getenv('DISPLAY')} "
        f"session_file_set={bool(session_file)}",
        flush=True,
    )

    if not username or not password:
        write_status(
            state="failed",
            message="API_USERNAME / API_PASSWORD missing in env",
            error="missing_credentials",
        )
        print("[auth_worker] missing API_USERNAME/API_PASSWORD", flush=True)
        return 2

    try:
        result = bootstrap_headful_playwright(
            username=username,
            password=password,
            state_file=state_file,
            profile_dir=profile_dir,
            browser_executable=online_browser_executable or None,
            timeout=timeout,
            progress=_progress,
        )
    except BrowserMFARequired as exc:
        write_status(
            state="failed",
            message="MFA incomplete before timeout",
            error=str(exc),
        )
        print(f"[auth_worker] MFA incomplete: {exc}", flush=True)
        return 3
    except BrowserSessionError as exc:
        write_status(state="failed", message=f"browser session failed: {exc}", error=str(exc))
        print(f"[auth_worker] browser error: {exc}", flush=True)
        return 4
    except Exception as exc:
        write_status(
            state="failed",
            message=f"unexpected error: {type(exc).__name__}",
            error=str(exc),
        )
        print(f"[auth_worker] unexpected: {exc}", flush=True)
        traceback.print_exc()
        return 5

    # Export Plan A requests session (cookies only; no secret dump to logs).
    try:
        client = UESTCLogin(
            username,
            password,
            session_file=session_file,
            browser_refresh=False,
            load_session=False,
        )
        client.install_browser_auth(result)
        # Verify bedroom once without printing balances to avoid noise in shared logs.
        ok = client.is_session_valid()
        bedroom = result.get("bedroom") or {}
        room_name = bedroom.get("fjh") or bedroom.get("roomName") or ""
        write_status(
            state="success" if ok else "failed",
            message=(
                f"session exported; room={room_name or 'unknown'}; valid={ok}"
                if ok
                else "session written but validation failed"
            ),
            session_valid=ok,
            room_name=room_name or None,
            finished_at=_now(),
            error=None if ok else "session_invalid",
        )
        print(
            f"[auth_worker] done ok={ok} room_set={bool(room_name)} at {_now()}",
            flush=True,
        )
        # Entrypoint holds briefly (AUTH_HOLD_SECONDS) then exits → container stops.
        return 0 if ok else 6
    except Exception as exc:
        write_status(
            state="failed",
            message=f"export session failed: {type(exc).__name__}",
            error=str(exc),
        )
        print(f"[auth_worker] export failed: {exc}", flush=True)
        traceback.print_exc()
        return 7


if __name__ == "__main__":
    sys.exit(run())
