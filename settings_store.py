"""Runtime settings overlay (data/settings.json) on top of env defaults.

Editable from the dashboard without rewriting .env (often mounted :ro).
Sensitive fields are never returned in plaintext after save — only whether set.
"""

from __future__ import annotations

import json
import os
import threading
from copy import deepcopy
from pathlib import Path

from env import (
    Authorization_code as env_auth_code,
    check_interval_minutes as env_interval,
    data_file as env_data_file,
    low_balance_threshold as env_threshold,
    session_alert_cooldown_hours as env_session_cooldown,
    smtp_from_addr as env_smtp_from,
    smtp_from_name as env_smtp_from_name,
    smtp_port as env_smtp_port,
    smtp_server as env_smtp_server,
    smtp_use_ssl as env_smtp_ssl,
)

_lock = threading.RLock()

_DEFAULT_PATH = "./data/settings.json"

# Keys allowed in settings.json
_KEYS = {
    "smtp_from",
    "smtp_from_name",
    "smtp_server",
    "smtp_port",
    "smtp_use_ssl",
    "smtp_auth_code",
    "low_balance_threshold",
    "check_interval_minutes",
    "session_alert_cooldown_hours",
    # recipients managed alongside data.json
    "recipients",
    "room_id",
}


def settings_path() -> Path:
    raw = os.getenv("SETTINGS_FILE", _DEFAULT_PATH)
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _data_json_path() -> Path:
    path = Path(env_data_file).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path


def _load_file() -> dict:
    path = settings_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _atomic_write(path: Path, text: str, *, mode: int | None = 0o600) -> None:
    """Write text to path. Prefer temp+replace; fall back to in-place for bind mounts."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.tmp"
    tmp.write_text(text, encoding="utf-8")
    try:
        tmp.replace(path)
    except OSError:
        # Docker often bind-mounts a single file (e.g. ./data.json); rename is EBUSY.
        path.write_text(text, encoding="utf-8")
        try:
            tmp.unlink()
        except OSError:
            pass
    if mode is not None:
        try:
            os.chmod(path, mode)
        except OSError:
            pass


def _save_file(data: dict) -> None:
    path = settings_path()
    _atomic_write(
        path,
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        mode=0o600,
    )


def _load_subscriptions() -> list[dict]:
    path = _data_json_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_subscriptions(subs: list[dict]) -> None:
    path = _data_json_path()
    _atomic_write(
        path,
        json.dumps(subs, ensure_ascii=False, indent=2) + "\n",
        mode=None,
    )


def get_raw() -> dict:
    """Merged settings for internal use (includes secrets)."""
    with _lock:
        file_data = _load_file()
        subs = _load_subscriptions()
        emails = []
        room_id = None
        for sub in subs:
            if not isinstance(sub, dict):
                continue
            if room_id is None and sub.get("room_id") not in (None, ""):
                room_id = str(sub.get("room_id"))
            email = (sub.get("email") or "").strip()
            if email and email not in emails:
                emails.append(email)
        # recipients may also be listed in settings.json
        for e in file_data.get("recipients") or []:
            e = str(e).strip()
            if e and e not in emails:
                emails.append(e)
        if file_data.get("room_id") not in (None, ""):
            room_id = str(file_data.get("room_id"))

        def pick(key, default):
            if key in file_data and file_data[key] not in (None, ""):
                return file_data[key]
            return default

        auth = pick("smtp_auth_code", env_auth_code or "")
        return {
            "smtp_from": pick("smtp_from", env_smtp_from or ""),
            "smtp_from_name": pick("smtp_from_name", env_smtp_from_name or "roominfo"),
            "smtp_server": pick("smtp_server", env_smtp_server or "smtp.163.com"),
            "smtp_port": int(pick("smtp_port", env_smtp_port or 465) or 465),
            "smtp_use_ssl": bool(pick("smtp_use_ssl", env_smtp_ssl if env_smtp_ssl is not None else True)),
            "smtp_auth_code": auth or "",
            "smtp_auth_configured": bool(auth),
            "low_balance_threshold": float(
                pick("low_balance_threshold", env_threshold if env_threshold is not None else 20.0)
            ),
            "check_interval_minutes": int(
                pick("check_interval_minutes", env_interval if env_interval is not None else 60)
            ),
            "session_alert_cooldown_hours": float(
                pick(
                    "session_alert_cooldown_hours",
                    env_session_cooldown if env_session_cooldown is not None else 12.0,
                )
            ),
            "recipients": emails,
            "room_id": room_id or "",
            "settings_file": str(settings_path()),
            "data_file": str(_data_json_path()),
        }


def get_public() -> dict:
    """Safe for API responses — no plaintext auth code."""
    raw = get_raw()
    out = deepcopy(raw)
    out.pop("smtp_auth_code", None)
    out["smtp_auth_configured"] = bool(raw.get("smtp_auth_configured"))
    out["smtp_auth_hint"] = "••••已配置" if raw.get("smtp_auth_configured") else "未配置"
    return out


def update(payload: dict) -> dict:
    """Merge payload into settings.json and sync recipients into data.json."""
    if not isinstance(payload, dict):
        raise ValueError("payload must be object")

    with _lock:
        current = _load_file()
        next_data = dict(current)

        # SMTP / threshold fields
        if "smtp_from" in payload:
            next_data["smtp_from"] = str(payload.get("smtp_from") or "").strip()
        if "smtp_from_name" in payload:
            next_data["smtp_from_name"] = str(payload.get("smtp_from_name") or "roominfo").strip()
        if "smtp_server" in payload:
            next_data["smtp_server"] = str(payload.get("smtp_server") or "").strip()
        if "smtp_port" in payload and payload.get("smtp_port") not in (None, ""):
            next_data["smtp_port"] = int(payload["smtp_port"])
        if "smtp_use_ssl" in payload:
            v = payload["smtp_use_ssl"]
            if isinstance(v, str):
                next_data["smtp_use_ssl"] = v.lower() in {"1", "true", "yes", "on"}
            else:
                next_data["smtp_use_ssl"] = bool(v)
        # Auth code: empty / placeholder means keep existing
        if "smtp_auth_code" in payload:
            code = str(payload.get("smtp_auth_code") or "")
            if code and not set(code) <= {"•", "*"} and code not in {"unchanged", "__keep__"}:
                next_data["smtp_auth_code"] = code
        if "low_balance_threshold" in payload and payload.get("low_balance_threshold") not in (
            None,
            "",
        ):
            next_data["low_balance_threshold"] = float(payload["low_balance_threshold"])
        if "check_interval_minutes" in payload and payload.get("check_interval_minutes") not in (
            None,
            "",
        ):
            next_data["check_interval_minutes"] = max(1, int(payload["check_interval_minutes"]))
        if "session_alert_cooldown_hours" in payload and payload.get(
            "session_alert_cooldown_hours"
        ) not in (None, ""):
            next_data["session_alert_cooldown_hours"] = float(
                payload["session_alert_cooldown_hours"]
            )

        room_id = None
        if "room_id" in payload and payload.get("room_id") not in (None, ""):
            room_id = str(payload.get("room_id")).strip()
            next_data["room_id"] = room_id
        elif next_data.get("room_id"):
            room_id = str(next_data.get("room_id"))

        recipients: list[str] = []
        if "recipients" in payload:
            raw_rec = payload.get("recipients")
            if isinstance(raw_rec, str):
                # comma / newline separated
                parts = raw_rec.replace(";", ",").replace("\n", ",").split(",")
                recipients = [p.strip() for p in parts if p.strip()]
            elif isinstance(raw_rec, list):
                recipients = [str(x).strip() for x in raw_rec if str(x).strip()]
            next_data["recipients"] = recipients
        elif "recipient_email" in payload:
            email = str(payload.get("recipient_email") or "").strip()
            recipients = [email] if email else []
            next_data["recipients"] = recipients
        else:
            recipients = list(next_data.get("recipients") or [])

        # Keep only known keys
        next_data = {k: v for k, v in next_data.items() if k in _KEYS}
        _save_file(next_data)

        # Sync data.json subscriptions (preserve extra fields if present)
        if "recipients" in payload or "recipient_email" in payload or "room_id" in payload:
            old = _load_subscriptions()
            rid = room_id
            if rid is None and old:
                rid = str(old[0].get("room_id") or "")
            if not recipients and old:
                # keep old emails if client only changed room
                recipients = [
                    str(s.get("email")).strip()
                    for s in old
                    if isinstance(s, dict) and s.get("email")
                ]
            if not recipients:
                subs = [{"room_id": rid or "000000", "email": ""}]
            else:
                subs = [{"room_id": rid or "000000", "email": e} for e in recipients]
            _save_subscriptions(subs)

        return get_public()


def smtp_kwargs() -> dict:
    raw = get_raw()
    return {
        "smtp_server": raw["smtp_server"],
        "smtp_port": raw["smtp_port"],
        "from_addr": raw["smtp_from"],
        "password": raw["smtp_auth_code"],
        "use_ssl": raw["smtp_use_ssl"],
        "from_name": raw["smtp_from_name"],
    }


def threshold() -> float:
    return float(get_raw()["low_balance_threshold"])


def interval_minutes() -> int:
    return max(1, int(get_raw()["check_interval_minutes"]))


def session_cooldown_hours() -> float:
    return float(get_raw()["session_alert_cooldown_hours"])


def recipients() -> list[str]:
    return list(get_raw()["recipients"])


def auth_code_configured() -> bool:
    return bool(get_raw()["smtp_auth_configured"])
