"""Flask web dashboard + monitor scheduler (single process)."""

from __future__ import annotations

import functools
import os
import secrets
import threading
from datetime import datetime
from pathlib import Path

import schedule
from flask import (
    Flask,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from auth_control import read_status as read_auth_status
from auth_control import start_auth, stop_auth
from env import (
    auth_novnc_url,
    history_retain_days,
    online_browser_refresh,
    online_session_file,
    web_auth_token,
    web_host,
    web_port,
)
from monitor import monitor
import settings_store

APP_VERSION = os.getenv("APP_VERSION", "v1.0.0")


def create_app() -> Flask:
    app = Flask(__name__, static_folder="static", template_folder="templates")
    app.secret_key = os.getenv("FLASK_SECRET_KEY") or secrets.token_hex(16)

    def _token_ok(provided: str | None) -> bool:
        expected = web_auth_token or ""
        if not expected:
            # Dev-friendly: empty token disables auth (documented as insecure).
            return True
        return bool(provided) and secrets.compare_digest(str(provided), expected)

    def _extract_token() -> str | None:
        auth = request.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        return (
            request.headers.get("X-Auth-Token")
            or request.args.get("token")
            or session.get("token")
        )

    def require_auth(view):
        @functools.wraps(view)
        def wrapper(*args, **kwargs):
            if _token_ok(_extract_token()):
                return view(*args, **kwargs)
            if request.path.startswith("/api/"):
                return jsonify({"success": False, "message": "unauthorized"}), 401
            return redirect(url_for("login", next=request.path))

        return wrapper

    @app.get("/login")
    def login():
        return render_template(
            "login.html",
            need_token=bool(web_auth_token),
            error=None,
        )

    @app.post("/login")
    def login_post():
        token = (request.form.get("token") or "").strip()
        if _token_ok(token):
            session["token"] = token
            dest = request.args.get("next") or url_for("dashboard")
            return redirect(dest)
        return (
            render_template(
                "login.html",
                need_token=bool(web_auth_token),
                error="Token 无效",
            ),
            401,
        )

    @app.get("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.get("/")
    @require_auth
    def dashboard():
        pub = settings_store.get_public()
        # Host port for direct noVNC access (compose VNC_BIND right side is 6080;
        # left side is the host port browsers hit when not using a reverse proxy).
        vnc_port = os.getenv("VNC_PUBLIC_PORT") or os.getenv("VNC_HOST_PORT") or "3033"
        try:
            vnc_port_int = int(str(vnc_port).split(":")[-1])
        except ValueError:
            vnc_port_int = 3033
        return render_template(
            "dashboard.html",
            threshold=pub["low_balance_threshold"],
            interval=pub["check_interval_minutes"],
            novnc_url=auth_novnc_url,
            auth_required=bool(web_auth_token),
            version=APP_VERSION,
            vnc_port=vnc_port_int,
        )

    @app.get("/api/status")
    @require_auth
    def api_status():
        session_path = Path(online_session_file)
        if not session_path.is_absolute():
            session_path = Path.cwd() / session_path
        latest = monitor.history.latest()
        pub = settings_store.get_public()
        return jsonify(
            {
                "success": True,
                "data": {
                    "session_file_exists": session_path.exists(),
                    "session_valid": monitor.session_valid(),
                    "browser_refresh_enabled": online_browser_refresh,
                    "threshold": pub["low_balance_threshold"],
                    "interval_minutes": pub["check_interval_minutes"],
                    "retain_days": history_retain_days,
                    "novnc_url": auth_novnc_url,
                    "last_run": monitor.last_status,
                    "latest": latest,
                    "history_stats": monitor.history.stats(),
                    "auth": read_auth_status(),
                    "settings": {
                        "smtp_from": pub.get("smtp_from"),
                        "smtp_auth_configured": pub.get("smtp_auth_configured"),
                        "recipients": pub.get("recipients"),
                        "room_id": pub.get("room_id"),
                    },
                    "server_time": datetime.now().isoformat(timespec="seconds"),
                },
            }
        )

    @app.get("/api/latest")
    @require_auth
    def api_latest():
        return jsonify({"success": True, "data": monitor.history.latest()})

    @app.get("/api/history")
    @require_auth
    def api_history():
        hours = request.args.get("hours", default=24 * 7, type=int)
        limit = request.args.get("limit", default=2000, type=int)
        rows = monitor.history.history(hours=hours, limit=limit)
        return jsonify({"success": True, "data": rows, "count": len(rows)})

    @app.get("/api/events")
    @require_auth
    def api_events():
        limit = request.args.get("limit", default=200, type=int)
        return jsonify({"success": True, "data": monitor.history.events(limit=limit)})

    @app.post("/api/query")
    @require_auth
    def api_query():
        result = monitor.query_once(source="web", notify=True)
        return jsonify({"success": bool(result.get("ok")), "data": result})

    @app.get("/api/settings")
    @require_auth
    def api_settings_get():
        return jsonify({"success": True, "data": settings_store.get_public()})

    @app.put("/api/settings")
    @app.post("/api/settings")
    @require_auth
    def api_settings_put():
        payload = request.get_json(silent=True) or {}
        try:
            data = settings_store.update(payload)
            monitor.history.add_event(
                "settings_updated",
                note=(
                    f"threshold={data.get('low_balance_threshold')}; "
                    f"recipients={len(data.get('recipients') or [])}; "
                    f"smtp_from={data.get('smtp_from') or '-'}; "
                    f"smtp_auth={data.get('smtp_auth_configured')}"
                ),
            )
            # Hot-reload scheduler interval if changed
            try:
                _reschedule(settings_store.interval_minutes())
            except Exception:
                pass
            return jsonify({"success": True, "data": data})
        except Exception as exc:
            monitor.history.add_event(
                "settings_failed", note=f"{type(exc).__name__}: {exc}"
            )
            return jsonify({"success": False, "message": str(exc)}), 400

    @app.post("/api/settings/test-email")
    @require_auth
    def api_settings_test_email():
        payload = request.get_json(silent=True) or {}
        to_addr = (payload.get("to") or payload.get("email") or "").strip() or None
        result = monitor.send_test_email(to_addr)
        return jsonify({"success": bool(result.get("ok")), "data": result})

    @app.get("/api/auth/status")
    @require_auth
    def api_auth_status():
        return jsonify({"success": True, "data": read_auth_status()})

    @app.post("/api/auth/start")
    @require_auth
    def api_auth_start():
        data = start_auth()
        data = dict(data or {})
        data["novnc_url"] = auth_novnc_url
        ok_states = {"running", "starting", "waiting_mfa", "waiting_host"}
        monitor.history.add_event(
            "auth_start",
            note=f"state={data.get('state')}; {data.get('message') or ''}",
        )
        return jsonify({"success": data.get("state") in ok_states, "data": data})

    @app.post("/api/auth/stop")
    @require_auth
    def api_auth_stop():
        data = stop_auth()
        monitor.history.add_event(
            "auth_stop",
            note=f"state={data.get('state')}; {data.get('message') or ''}",
        )
        return jsonify({"success": True, "data": data})

    return app


_scheduler_lock = threading.Lock()
_scheduler_minutes: int | None = None


def _reschedule(minutes: int) -> None:
    global _scheduler_minutes
    minutes = max(1, int(minutes))
    with _scheduler_lock:
        if _scheduler_minutes == minutes:
            return
        schedule.clear()
        schedule.every(minutes).minutes.do(
            lambda: monitor.query_once(source="monitor", notify=True)
        )
        _scheduler_minutes = minutes
        print(f"[{datetime.now()}] scheduler interval → {minutes}m")


def _start_scheduler(stop_event: threading.Event):
    _reschedule(settings_store.interval_minutes())
    # initial run
    try:
        monitor.query_once(source="startup", notify=True)
    except Exception as exc:
        print(f"[{datetime.now()}] startup query failed: {exc}")
        try:
            monitor.history.add_event("startup_failed", note=str(exc))
        except Exception:
            pass

    while not stop_event.is_set():
        # pick up interval changes written by settings API
        try:
            _reschedule(settings_store.interval_minutes())
        except Exception:
            pass
        schedule.run_pending()
        stop_event.wait(1)


def main():
    app = create_app()
    stop_event = threading.Event()
    worker = threading.Thread(
        target=_start_scheduler, args=(stop_event,), name="monitor-scheduler", daemon=True
    )
    worker.start()
    pub = settings_store.get_public()
    print(
        f"[{datetime.now()}] web+monitor on {web_host}:{web_port} "
        f"(interval={pub['check_interval_minutes']}m, "
        f"threshold={pub['low_balance_threshold']})"
    )
    try:
        app.run(
            host=web_host,
            port=web_port,
            debug=False,
            use_reloader=False,
            threaded=True,
        )
    finally:
        stop_event.set()
        worker.join(timeout=3)


if __name__ == "__main__":
    main()
