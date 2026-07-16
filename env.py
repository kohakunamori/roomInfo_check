from dotenv import load_dotenv
import os

# 加载 .env 文件（Docker 中通常由 compose env_file 注入，本地则读当前目录）
load_dotenv()


def _as_bool(value, default=False):
    if value is None:
        return default
    return str(value).lower() in {"1", "true", "yes", "on"}


def _as_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value, default):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# ── 账号 ──────────────────────────────────────────────────────────
username = os.getenv("API_USERNAME")
password = os.getenv("API_PASSWORD")

# ── 邮件 SMTP ─────────────────────────────────────────────────────
Authorization_code = os.getenv("Authorization_code")
smtp_server = os.getenv("SMTP_SERVER", "smtp.163.com")
smtp_port = _as_int(os.getenv("SMTP_PORT"), 465)
smtp_from_addr = os.getenv("SMTP_FROM", "")
smtp_from_name = os.getenv("SMTP_FROM_NAME", "roominfo")
smtp_use_ssl = _as_bool(os.getenv("SMTP_USE_SSL"), True)

# ── 监测运行参数 ──────────────────────────────────────────────────
data_file = os.getenv("DATA_FILE", "data.json")
check_interval_minutes = _as_int(os.getenv("CHECK_INTERVAL_MINUTES"), 60)
low_balance_threshold = _as_float(os.getenv("LOW_BALANCE_THRESHOLD"), 20.0)
# 会话失效邮件冷却（小时），避免每小时重复轰炸
session_alert_cooldown_hours = _as_float(os.getenv("SESSION_ALERT_COOLDOWN_HOURS"), 12.0)

# ── 历史 / Web ────────────────────────────────────────────────────
history_db = os.getenv("HISTORY_DB", "./data/history.db")
history_retain_days = _as_int(os.getenv("HISTORY_RETAIN_DAYS"), 90)
history_recharge_delta = _as_float(os.getenv("HISTORY_RECHARGE_DELTA"), 1.0)
web_host = os.getenv("WEB_HOST", "0.0.0.0")
web_port = _as_int(os.getenv("WEB_PORT"), 8080)
web_auth_token = os.getenv("WEB_AUTH_TOKEN", "")
web_enable = _as_bool(os.getenv("WEB_ENABLE"), True)
# 仪表盘外链（邮件/前端提示用；部署后按你的域名/端口覆盖）
web_public_url = os.getenv("WEB_PUBLIC_URL", "http://127.0.0.1:3032/")

# ── 容器内刷凭据（auth profile） ──────────────────────────────────
auth_mode = os.getenv("AUTH_MODE", "manual")  # docker | manual
auth_service_name = os.getenv("AUTH_SERVICE_NAME", "roominfo-auth")
auth_compose_project = os.getenv("AUTH_COMPOSE_PROJECT", "") or None
# 留空 → 前端使用同源相对路径 /vnc/…（经反代时无需配置）；
# 仅当 noVNC 与仪表盘不同源时才需要设置绝对地址。
auth_novnc_url = os.getenv("AUTH_NOVNC_URL", "")
auth_status_file = os.getenv("AUTH_STATUS_FILE", "./data/auth_status.json")
auth_timeout_seconds = _as_int(os.getenv("AUTH_TIMEOUT_SECONDS"), 900)

# ── 门户会话 / 浏览器 ─────────────────────────────────────────────
online_token = os.getenv("ONLINE_TOKEN")
online_cookie = os.getenv("ONLINE_COOKIE")
idas_cookie = os.getenv("IDAS_COOKIE")
online_user_agent = os.getenv(
    "ONLINE_USER_AGENT",
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/138.0.0.0 Safari/537.36"
    ),
)
online_accept_language = os.getenv(
    "ONLINE_ACCEPT_LANGUAGE",
    "zh-CN,zh;q=0.9,en;q=0.8",
)
online_session_file = os.getenv("ONLINE_SESSION_FILE", ".uestc_session.json")
online_remember_me = _as_bool(os.getenv("ONLINE_REMEMBER_ME"), True)
online_browser_state_file = os.getenv(
    "ONLINE_BROWSER_STATE_FILE", ".uestc_browser_state.json"
)
online_browser_profile_dir = os.getenv(
    "ONLINE_BROWSER_PROFILE_DIR", ".uestc_chrome_profile"
)
# 空字符串视为未配置，交给 browser_session 自动探测系统 Chrome/Edge。
online_browser_executable = os.getenv("ONLINE_BROWSER_EXECUTABLE") or None
online_browser_refresh = _as_bool(os.getenv("ONLINE_BROWSER_REFRESH"), True)
online_browser_timeout = _as_int(os.getenv("ONLINE_BROWSER_TIMEOUT"), 90)

if __name__ == "__main__":
    print(
        "Configuration loaded: "
        f"username={bool(username)}, password={bool(password)}, "
        f"smtp_authorization={bool(Authorization_code)}, "
        f"smtp_server={smtp_server}, smtp_port={smtp_port}, "
        f"smtp_from={bool(smtp_from_addr)}, smtp_use_ssl={smtp_use_ssl}, "
        f"data_file={data_file}, "
        f"check_interval_minutes={check_interval_minutes}, "
        f"low_balance_threshold={low_balance_threshold}, "
        f"history_db={history_db}, web_port={web_port}, "
        f"web_auth_configured={bool(web_auth_token)}, "
        f"online_token={bool(online_token)}, online_cookie={bool(online_cookie)}, "
        f"idas_cookie={bool(idas_cookie)}, "
        f"online_session_file={online_session_file}, "
        f"online_remember_me={online_remember_me}, "
        f"online_browser_state_file={bool(online_browser_state_file)}, "
        f"online_browser_refresh={online_browser_refresh}, "
        f"online_browser_timeout={online_browser_timeout}"
    )
