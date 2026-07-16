"""Shared monitor loop: query portal, record history, optional email."""

from __future__ import annotations

import threading
import time
from datetime import datetime

import getInfo
from emailSend import send_email
from env import (
    auth_novnc_url,
    history_db,
    history_recharge_delta,
    history_retain_days,
    password,
    username,
    web_public_url,
)
from history import HistoryStore
import settings_store


class MonitorService:
    def __init__(self):
        self.login = getInfo.UESTCLogin(username, password)
        self.history = HistoryStore(history_db)
        self._lock = threading.Lock()
        self._last_session_alert_ts = 0.0
        self.last_status = {
            "ok": None,
            "message": "not_run",
            "at": None,
            "latest": None,
        }

    def session_valid(self) -> bool:
        try:
            return bool(self.login.is_session_valid())
        except Exception:
            return False

    def load_subscriptions(self) -> list:
        # Prefer settings_store which merges data.json + settings.json
        emails = settings_store.recipients()
        room_id = settings_store.get_raw().get("room_id") or None
        if not emails:
            # still allow query-only with empty email
            return [{"room_id": room_id, "email": None}]
        return [{"room_id": room_id, "email": e} for e in emails]

    def _recipient_emails(self) -> list[str]:
        return settings_store.recipients()

    def _send_and_log(
        self,
        *,
        to_addr: str,
        subject: str,
        content: str,
        event_type: str,
        extra_note: str = "",
    ) -> bool:
        """Send mail with runtime SMTP settings and write a history event."""
        if not settings_store.auth_code_configured():
            note = f"SMTP未配置 → {to_addr}"
            if extra_note:
                note = f"{note}; {extra_note}"
            self.history.add_event(event_type, note=note)
            print(f"[{datetime.now()}] {note}")
            return False
        kwargs = settings_store.smtp_kwargs()
        try:
            ok = send_email(
                to_addr=to_addr,
                subject=subject,
                content=content,
                **kwargs,
            )
        except Exception as exc:
            ok = False
            self.history.add_event(
                "email_failed",
                note=f"→ {to_addr}; {type(exc).__name__}: {exc}"
                + (f"; {extra_note}" if extra_note else ""),
            )
            print(f"[{datetime.now()}] 邮件异常 -> {to_addr}: {exc}")
            return False

        if ok:
            self.history.add_event(
                "email_sent",
                note=f"→ {to_addr}; subject={subject}"
                + (f"; {extra_note}" if extra_note else ""),
            )
            print(f"[{datetime.now()}] 邮件成功 -> {to_addr}: {subject}")
        else:
            self.history.add_event(
                "email_failed",
                note=f"→ {to_addr}; subject={subject}"
                + (f"; {extra_note}" if extra_note else ""),
            )
            print(f"[{datetime.now()}] 邮件失败 -> {to_addr}: {subject}")
        return bool(ok)

    def _notify_session_invalid(self, reason: str) -> None:
        """Email subscribers when portal session is invalid (rate-limited)."""
        cooldown_h = settings_store.session_cooldown_hours()
        cooldown_s = max(0.0, float(cooldown_h)) * 3600.0
        now = time.time()
        if cooldown_s > 0 and (now - self._last_session_alert_ts) < cooldown_s:
            msg = f"会话失效，邮件冷却中（{cooldown_h}h）: {reason}"
            print(f"[{datetime.now()}] {msg}")
            self.history.add_event("session_invalid", note=msg)
            return

        recipients = self._recipient_emails()
        if not recipients:
            print(f"[{datetime.now()}] 会话失效但无收件人: {reason}")
            self.history.add_event(
                "session_invalid",
                note=f"无收件人: {reason}",
            )
            return

        dash = (web_public_url or "http://127.0.0.1:3032/").rstrip("/") + "/"
        novnc = auth_novnc_url or (
            dash + "vnc/vnc.html?autoconnect=1&resize=scale&path=vnc/websockify"
        )
        subject = "roominfo 会话失效 — 请刷新登录"
        content = (
            "UESTC 宿舍电费监控的门户会话已失效，无法继续查询余额。\n\n"
            f"原因: {reason}\n\n"
            "请尽快修复：\n"
            f"1) 打开仪表盘: {dash}\n"
            f"2) 菜单 → 刷新登录（按需启动 auth / noVNC）: {novnc}\n"
            "3) 完成统一身份认证 / MFA 后容器会自动关闭。\n"
        )

        any_ok = False
        for email in recipients:
            ok = self._send_and_log(
                to_addr=email,
                subject=subject,
                content=content,
                event_type="session_invalid",
                extra_note=reason,
            )
            any_ok = any_ok or bool(ok)

        self._last_session_alert_ts = now
        self.history.add_event(
            "session_invalid",
            note=(
                f"已通知 {len(recipients)} 人; ok={any_ok}; {reason}"
                if any_ok
                else f"通知失败; {reason}"
            ),
        )

    def query_once(self, *, source="monitor", notify=True) -> dict:
        """Single full cycle. Thread-safe."""
        with self._lock:
            return self._query_once_unlocked(source=source, notify=notify)

    def _query_once_unlocked(self, *, source="monitor", notify=True) -> dict:
        at = datetime.now().isoformat(timespec="seconds")
        thr = settings_store.threshold()
        self.history.add_event("query_start", note=f"source={source}")

        try:
            if not self.login.is_session_valid():
                try:
                    self.login.login()
                except Exception as exc:
                    if notify:
                        self._notify_session_invalid(str(exc))
                    self.history.add_sample(ok=False, error=str(exc), source=source)
                    self.history.add_event(
                        "query_failed", note=f"login_failed: {exc}; source={source}"
                    )
                    self.last_status = {
                        "ok": False,
                        "message": f"login_failed: {exc}",
                        "at": at,
                        "latest": self.history.latest(),
                        "session_valid": False,
                    }
                    return self.last_status
        except Exception as exc:
            if notify:
                self._notify_session_invalid(str(exc))
            self.history.add_sample(ok=False, error=str(exc), source=source)
            self.history.add_event(
                "query_failed", note=f"session_check_failed: {exc}; source={source}"
            )
            self.last_status = {
                "ok": False,
                "message": f"session_check_failed: {exc}",
                "at": at,
                "latest": self.history.latest(),
                "session_valid": False,
            }
            return self.last_status

        subs = self.load_subscriptions()
        if not subs:
            subs = [{"room_id": None, "email": None}]

        results = []
        errors = []
        for sub in subs:
            room_id = sub.get("room_id")
            email = sub.get("email")
            try:
                info = self.login.query_room_info(room_id)
                amount = info.get("remaining_amount")
                sample = self.history.add_sample(
                    room_name=info.get("room_name"),
                    room_id_label=str(room_id) if room_id is not None else None,
                    amount=amount,
                    electricity=info.get("remaining_electricity"),
                    source=info.get("source") or source,
                    ok=True,
                    recharge_delta=history_recharge_delta,
                )
                results.append(sample)

                if notify and email and amount is not None:
                    try:
                        remaining = float(amount)
                    except (TypeError, ValueError):
                        remaining = None
                    if remaining is not None and remaining <= thr:
                        msg = (
                            f"寝室 {info.get('room_name')} 剩余电费: "
                            f"{info.get('remaining_amount', 'N/A')}元"
                            f"（阈值 ≤ {thr} 元）"
                        )
                        self._send_and_log(
                            to_addr=email,
                            subject=f"电量提醒 {info.get('room_name')}",
                            content=msg,
                            event_type="alert",
                            extra_note=f"balance={remaining}",
                        )
                        self.history.add_event(
                            "alert",
                            amount_to=remaining,
                            note=f"低余额触发 → {email}; ≤{thr}",
                        )
                    else:
                        self.history.add_event(
                            "check_ok",
                            amount_to=remaining if remaining is not None else None,
                            note=(
                                f"{email or '-'} 寝室 {info.get('room_name')} "
                                f"余额 {info.get('remaining_amount')} 未达阈值 {thr}"
                            ),
                        )
                        print(
                            f"[{datetime.now()}] 未达警戒线 {email}: "
                            f"寝室 {info.get('room_name')} 剩余电费: "
                            f"{info.get('remaining_amount')}元"
                        )
            except Exception as exc:
                err = str(exc)
                errors.append(err)
                self.history.add_sample(
                    room_id_label=str(room_id) if room_id is not None else None,
                    ok=False,
                    error=err,
                    source=source,
                )
                self.history.add_event(
                    "query_failed",
                    note=f"room={room_id}; {err}; source={source}",
                )
                print(f"[{datetime.now()}] 处理房间 {room_id} 失败: {exc}")
                lowered = err.lower()
                if notify and any(
                    k in lowered
                    for k in (
                        "auth",
                        "session",
                        "login",
                        "未登录",
                        "凭据",
                        "cookie",
                        "401",
                        "unauthorized",
                    )
                ):
                    self._notify_session_invalid(err)

        try:
            self.history.prune(retain_days=history_retain_days)
        except Exception:
            pass

        ok = bool(results) and not errors
        self.history.add_event(
            "query_done",
            note=(
                f"source={source}; ok={ok}; samples={len(results)}; "
                f"errors={len(errors)}; threshold={thr}"
            ),
        )
        self.last_status = {
            "ok": ok if results else False,
            "message": "ok" if ok else ("; ".join(errors) or "no_data"),
            "at": at,
            "latest": self.history.latest(),
            "samples": results,
            "errors": errors,
            "session_valid": self.session_valid() if not ok else True,
        }
        return self.last_status

    def send_test_email(self, to_addr: str | None = None) -> dict:
        """Send a one-shot test email using current settings."""
        recipients = [to_addr] if to_addr else self._recipient_emails()
        recipients = [r for r in recipients if r]
        if not recipients:
            self.history.add_event("email_failed", note="测试邮件：无收件人")
            return {"ok": False, "message": "无收件人"}
        if not settings_store.auth_code_configured():
            self.history.add_event("email_failed", note="测试邮件：SMTP 授权码未配置")
            return {"ok": False, "message": "SMTP 授权码未配置"}
        results = []
        for email in recipients:
            ok = self._send_and_log(
                to_addr=email,
                subject="roominfo 邮件测试",
                content=(
                    "这是一封来自 roominfo 的测试邮件。\n"
                    f"时间: {datetime.now().isoformat(timespec='seconds')}\n"
                    f"阈值: {settings_store.threshold()} 元\n"
                ),
                event_type="email_test",
            )
            results.append({"email": email, "ok": ok})
        any_ok = any(r["ok"] for r in results)
        return {
            "ok": any_ok,
            "message": "ok" if any_ok else "send failed",
            "results": results,
        }


# process-wide singleton for web + scheduler
monitor = MonitorService()
