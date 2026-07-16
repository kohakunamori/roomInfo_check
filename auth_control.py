"""Manage on-demand browser auth container / local auth worker status.

Lifecycle model (省资源):
  - roominfo-auth is started only when the user clicks「刷新登录」
  - After success / fail / timeout the auth entrypoint exits → container stops
    (compose `restart: "no"`)
  - 「结束刷新」writes a stop request so the host helper can force-stop

Without docker.sock in the roominfo container, AUTH_MODE=manual uses trigger
files under ./data/ that a host-side helper (`docker/auth-lifecycle.sh` or the
`roominfo-auth-ctl` sidecar) polls.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from env import (
    auth_compose_project,
    auth_mode,
    auth_novnc_url,
    auth_service_name,
    auth_status_file,
    auth_timeout_seconds,
)

_lock = threading.RLock()


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _status_path() -> Path:
    path = Path(auth_status_file).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _data_dir() -> Path:
    # Shared volume: roominfo + roominfo-auth + host helper all see this.
    raw = os.getenv("AUTH_DATA_DIR", "./data")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    path.mkdir(parents=True, exist_ok=True)
    return path


def _trigger_path(name: str) -> Path:
    path = _data_dir() / name
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def read_status() -> dict:
    path = _status_path()
    if not path.exists():
        return {
            "state": "idle",
            "message": "no auth session",
            # Always use live env URL — never trust stale loopback from old deploys.
            "novnc_url": auth_novnc_url,
            "updated_at": None,
        }
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("invalid status")
        # Force public/configured URL so browsers off-NAS never get 127.0.0.1:6080
        data["novnc_url"] = auth_novnc_url
        return data
    except Exception:
        return {
            "state": "idle",
            "message": "status unreadable",
            "novnc_url": auth_novnc_url,
            "updated_at": None,
        }


def write_status(**fields) -> dict:
    path = _status_path()
    with _lock:
        current = {}
        if path.exists():
            try:
                current = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                current = {}
        current.update(fields)
        current["updated_at"] = _utc_now()
        current.setdefault("novnc_url", auth_novnc_url)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(current, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        tmp.replace(path)
        return current


def _docker_cmd() -> list[str] | None:
    docker = shutil.which("docker")
    if not docker:
        return None
    return [docker]


def request_auth_start() -> Path:
    """Signal host helper to `docker compose --profile auth up -d roominfo-auth`."""
    path = _trigger_path(".auth_start_request")
    # Clear a pending stop so a rapid stop→start still starts.
    stop = _trigger_path(".auth_stop_request")
    if stop.exists():
        try:
            stop.unlink()
        except OSError:
            pass
    path.write_text(_utc_now() + "\n", encoding="utf-8")
    return path


def request_auth_stop() -> Path:
    """Signal host helper to force-stop the auth container."""
    path = _trigger_path(".auth_stop_request")
    start = _trigger_path(".auth_start_request")
    if start.exists():
        try:
            start.unlink()
        except OSError:
            pass
    path.write_text(_utc_now() + "\n", encoding="utf-8")
    return path


# Back-compat alias (old idle-keepalive reauth). Prefer request_auth_start.
def request_reauth() -> Path:
    return request_auth_start()


def start_auth() -> dict:
    """Start auth flow. Idempotent if already running."""
    with _lock:
        status = read_status()
        if status.get("state") in {"running", "starting", "waiting_mfa"}:
            return status

        write_status(
            state="starting",
            message="starting auth worker",
            started_at=_utc_now(),
            deadline_at=None,
            error=None,
        )

        mode = (auth_mode or "manual").lower()
        if mode == "docker":
            return _start_docker_auth()
        if mode == "manual":
            # No docker.sock in roominfo: write a trigger for the host lifecycle helper.
            try:
                trigger = request_auth_start()
                return write_status(
                    state="starting",
                    message=(
                        "已请求启动 auth 容器（按需启停）。"
                        " 数秒内 noVNC 应可用；请在页面内完成登录/MFA。"
                        " 若长时间无响应，确认 roominfo-auth-ctl 在运行，"
                        " 或手动执行："
                        " docker compose --profile auth up -d roominfo-auth"
                    ),
                    started_at=_utc_now(),
                    novnc_url=auth_novnc_url,
                    start_trigger=str(trigger),
                    error=None,
                )
            except Exception as exc:
                return write_status(
                    state="waiting_host",
                    message=(
                        "manual mode: 无法写入启动触发文件；请在部署目录执行 "
                        "`docker compose --profile auth up -d roominfo-auth` "
                        f"后打开 noVNC。error={type(exc).__name__}"
                    ),
                    started_at=_utc_now(),
                    novnc_url=auth_novnc_url,
                    error=str(exc),
                )
        return write_status(
            state="failed",
            message=f"unknown AUTH_MODE={auth_mode}",
            error="bad_mode",
        )


def _start_docker_auth() -> dict:
    docker = _docker_cmd()
    if not docker:
        return write_status(
            state="failed",
            message="docker CLI not found in container; use AUTH_MODE=manual",
            error="no_docker",
        )

    compose = docker + ["compose"]
    if auth_compose_project:
        compose += ["-p", auth_compose_project]

    env = os.environ.copy()
    try:
        subprocess.run(
            compose + ["--profile", "auth", "up", "-d", auth_service_name],
            check=True,
            capture_output=True,
            text=True,
            timeout=180,
            env=env,
        )
    except subprocess.CalledProcessError as exc:
        err = (exc.stderr or exc.stdout or str(exc))[-500:]
        return write_status(
            state="failed",
            message="docker compose failed to start auth service",
            error=err,
        )
    except Exception as exc:
        return write_status(
            state="failed",
            message=f"failed to start auth: {exc}",
            error=str(exc),
        )

    deadline = time.time() + max(60, int(auth_timeout_seconds))
    return write_status(
        state="running",
        message="auth container started; complete MFA in noVNC",
        started_at=_utc_now(),
        deadline_ts=deadline,
        novnc_url=auth_novnc_url,
    )


def stop_auth() -> dict:
    with _lock:
        mode = (auth_mode or "manual").lower()
        if mode == "docker":
            docker = _docker_cmd()
            if docker:
                compose = docker + ["compose"]
                if auth_compose_project:
                    compose += ["-p", auth_compose_project]
                try:
                    subprocess.run(
                        compose + ["--profile", "auth", "stop", auth_service_name],
                        check=False,
                        capture_output=True,
                        text=True,
                        timeout=60,
                    )
                except Exception:
                    pass
        else:
            # manual: ask host helper to stop; container also self-exits after hold.
            try:
                request_auth_stop()
            except Exception:
                pass
        return write_status(
            state="idle",
            message="auth stopped",
            error=None,
        )
