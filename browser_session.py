import json
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

import requests


PORTAL_BASE_URL = "https://online.uestc.edu.cn"
PORTAL_PAGE_URL = f"{PORTAL_BASE_URL}/page/"
BEDROOM_URL = f"{PORTAL_BASE_URL}/site/bedroom"
IDAS_HOST = "idas.uestc.edu.cn"


class BrowserSessionError(Exception):
    """真实浏览器会话启动、登录或状态导出失败。"""


class BrowserMFARequired(BrowserSessionError):
    """headless 刷新遇到必须由用户完成的多因素复核。"""


def resolve_project_path(value, default_name):
    path = Path(value or default_name).expanduser()
    if not path.is_absolute():
        path = Path(__file__).resolve().parent / path
    return path


def clear_chromium_profile_locks(profile_dir) -> int:
    """Remove stale Chromium Singleton* locks under a persistent profile.

    After ``docker stop/rm`` (or crash) the profile on a shared volume often
    still has SingletonLock/Cookie/Socket pointing at a dead hostname.
    Chromium then exits immediately ("profile appears to be in use by another
    Chromium process") → noVNC only shows a black Xvfb screen.

    Only deletes Singleton* files — never the whole profile (cookies/cache stay).
    Returns the number of files removed.
    """
    if not profile_dir:
        return 0
    path = Path(profile_dir)
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        return 0
    removed = 0
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        target = path / name
        try:
            if target.exists() or target.is_symlink():
                target.unlink(missing_ok=True)
                removed += 1
        except OSError:
            pass
    try:
        for target in path.glob("Singleton*"):
            try:
                if target.is_file() or target.is_symlink():
                    target.unlink(missing_ok=True)
                    removed += 1
            except OSError:
                pass
    except OSError:
        pass
    if removed:
        print(
            f"[browser_session] cleared {removed} stale Chromium Singleton* "
            f"under profile={path}",
            flush=True,
        )
    return removed


def _is_playwright_managed_browser(path):
    normalized = str(path).replace("\\", "/").lower()
    return "ms-playwright" in normalized or "/.cache/ms-playwright/" in normalized


def _existing_executable(path):
    path = Path(path).expanduser()
    if not path.exists():
        return None
    # resolve 会跟随 Windows junction；若本机 Chrome 目录被错误联接到
    # ms-playwright，这里仍能识别并降级到真正的系统浏览器。
    resolved = path.resolve()
    return resolved


def find_browser_executable(configured=None):
    if configured:
        path = _existing_executable(configured)
        if path is not None:
            return path
        raise BrowserSessionError(f"配置的浏览器不存在: {configured}")

    # 优先本机已安装的系统浏览器；Playwright 自带 Chromium 仅作最后兜底。
    # 真实 Chrome/Edge 更有利于 IDAS “信任此浏览器”判定。
    preferred = []
    fallback = []

    def consider(path):
        resolved = _existing_executable(path)
        if resolved is None:
            return
        target = fallback if _is_playwright_managed_browser(resolved) else preferred
        if resolved not in target:
            target.append(resolved)

    local_app_data = os.getenv("LOCALAPPDATA")
    program_files = os.getenv("ProgramFiles")
    program_files_x86 = os.getenv("ProgramFiles(x86)")
    for root, relative in (
        (program_files, "Google/Chrome/Application/chrome.exe"),
        (program_files_x86, "Google/Chrome/Application/chrome.exe"),
        (local_app_data, "Google/Chrome/Application/chrome.exe"),
        (program_files, "Microsoft/Edge/Application/msedge.exe"),
        (program_files_x86, "Microsoft/Edge/Application/msedge.exe"),
        (local_app_data, "Microsoft/Edge/Application/msedge.exe"),
    ):
        if root:
            consider(Path(root) / relative)

    for command in (
        "google-chrome",
        "google-chrome-stable",
        "microsoft-edge",
        "msedge",
        "chromium",
        "chromium-browser",
        "chrome",
    ):
        executable = shutil.which(command)
        if executable:
            consider(executable)

    for path in preferred + fallback:
        return path
    raise BrowserSessionError(
        "没有找到 Chrome/Chromium/Edge；请配置 ONLINE_BROWSER_EXECUTABLE"
    )


def _import_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise BrowserSessionError(
            "缺少 Playwright。"
            "请先在可安装官方 Windows wheel 的 CPython 环境中执行 "
            "`python -m pip install playwright`（MSYS2/MinGW Python 通常没有匹配轮子）。"
            "若走代理：`$env:HTTPS_PROXY='http://127.0.0.1:7890'`。"
        ) from exc
    return sync_playwright


def _free_local_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class _without_proxy_env:
    """临时去掉代理环境变量，避免本机 CDP /json/version 被系统代理劫持。"""

    KEYS = (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    )

    def __enter__(self):
        # Windows 环境变量大小写不敏感：先快照再统一删除，避免
        # 先 pop 大写再 pop 小写时把同一项覆盖成 None。
        self._saved = {}
        for key in self.KEYS:
            if key in os.environ and key not in self._saved:
                self._saved[key] = os.environ.get(key)
        for key in list(os.environ):
            if key.lower() in {"http_proxy", "https_proxy", "all_proxy"}:
                os.environ.pop(key, None)
        # 明确声明本机地址不走代理，兼容仍读取 NO_PROXY 的客户端。
        no_proxy = os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or ""
        parts = [item.strip() for item in no_proxy.split(",") if item.strip()]
        for item in ("127.0.0.1", "localhost", "::1"):
            if item not in parts:
                parts.append(item)
        value = ",".join(parts)
        os.environ["NO_PROXY"] = value
        os.environ["no_proxy"] = value
        return self

    def __exit__(self, exc_type, exc, tb):
        for key in list(os.environ):
            if key.lower() in {"http_proxy", "https_proxy", "all_proxy"}:
                os.environ.pop(key, None)
        for key, value in self._saved.items():
            if value is not None:
                os.environ[key] = value
        return False


def _wait_for_cdp(endpoint, timeout=30):
    deadline = time.monotonic() + timeout
    session = requests.Session()
    session.trust_env = False
    last_error = None
    with _without_proxy_env():
        while time.monotonic() < deadline:
            try:
                response = session.get(f"{endpoint}/json/version", timeout=2)
                if response.ok:
                    return response.json()
                last_error = f"HTTP {response.status_code}"
            except requests.RequestException as exc:
                last_error = str(exc)
            time.sleep(0.5)
    raise BrowserSessionError(
        f"无法连接本机浏览器调试端口 {endpoint}"
        + (f"（{last_error}）" if last_error else "")
    )


def _atomic_json_write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    os.replace(temporary, path)


def _meta_path(state_file):
    return state_file.with_name(f"{state_file.name}.meta.json")


def _load_json(path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {} if default is None else default


def _school_cookie_records(storage_state):
    records = []
    for cookie in storage_state.get("cookies", []):
        domain = str(cookie.get("domain") or "").lstrip(".").lower()
        if not (domain == "uestc.edu.cn" or domain.endswith(".uestc.edu.cn")):
            continue
        expires = cookie.get("expires")
        if expires is not None and float(expires) <= 0:
            expires = None
        records.append(
            {
                "name": cookie.get("name", ""),
                "value": cookie.get("value", ""),
                "domain": cookie.get("domain", ""),
                "path": cookie.get("path") or "/",
                "expires": int(expires) if expires is not None else None,
                "secure": bool(cookie.get("secure")),
            }
        )
    return records


def _portal_token(storage_state):
    for origin in storage_state.get("origins", []):
        if origin.get("origin") != PORTAL_BASE_URL:
            continue
        for item in origin.get("localStorage", []):
            if item.get("name") == "token" and item.get("value"):
                return item["value"]
    return None


def _normalize_bedroom_payload(data):
    if isinstance(data, dict):
        return data
    if not isinstance(data, str):
        return None

    text = data.strip()
    if not text:
        return None

    candidates = [text]
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        candidates.append(text[start : end + 1])

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            return parsed
        if isinstance(parsed, str):
            nested = _normalize_bedroom_payload(parsed)
            if nested is not None:
                return nested
    return None


def _is_complete_bedroom_data(data):
    if not isinstance(data, dict):
        return False
    if data.get("syje") is not None or data.get("sydl") is not None:
        return True
    if data.get("retcode") in (0, "0") and (
        data.get("fjh") or data.get("roomName") or data.get("roomId")
    ):
        return True
    return False


def _query_bedroom_in_page(page):
    if urlparse(page.url).hostname != "online.uestc.edu.cn":
        return None

    for _ in range(5):
        result = page.evaluate(
            """
            async ({url}) => {
              const response = await fetch(url, {
                credentials: 'include',
                headers: {
                  'Accept': 'application/json, text/plain, */*',
                  'X-Requested-With': 'XMLHttpRequest'
                }
              });
              return {status: response.status, text: await response.text()};
            }
            """,
            {"url": BEDROOM_URL},
        )
        if result.get("status") == 401:
            return None
        try:
            payload = json.loads(result.get("text") or "")
        except ValueError:
            page.wait_for_timeout(300)
            continue
        if not isinstance(payload, dict) or payload.get("e") not in (0, "0"):
            page.wait_for_timeout(300)
            continue
        data = _normalize_bedroom_payload(payload.get("d"))
        if _is_complete_bedroom_data(data):
            return data
        page.wait_for_timeout(300)
    return None


def _fill_login_form(page, username, password):
    username_input = page.locator("#username")
    password_input = page.locator("#password")
    if username_input.count() != 1 or password_input.count() != 1:
        return False
    if not username_input.is_visible() or not password_input.is_visible():
        return False

    username_input.fill(username or "")
    password_input.fill(password or "")
    remember = page.locator("#rememberMe")
    if remember.count() == 1 and remember.is_visible():
        try:
            remember.check()
        except Exception:
            pass

    submit = page.locator("#login_submit")
    if submit.count() == 1 and submit.is_visible():
        submit.click()
    else:
        password_input.press("Enter")
    return True


def _wait_for_portal(page, username, password, timeout, interactive, progress=None):
    deadline = time.monotonic() + timeout
    login_submitted = False
    mfa_announced = False
    while time.monotonic() < deadline:
        hostname = urlparse(page.url).hostname or ""
        if hostname == "online.uestc.edu.cn":
            bedroom = _query_bedroom_in_page(page)
            if bedroom is not None:
                return bedroom

        if hostname == IDAS_HOST:
            if "/reAuthCheck/" in page.url or "isMultifactor=true" in page.url:
                if not interactive:
                    raise BrowserMFARequired(
                        "真实浏览器状态已失效，IDAS 要求重新进行多因素复核；"
                        "请运行 bootstrap_browser.py"
                    )
                if progress and not mfa_announced:
                    progress("mfa_required", {"url": page.url})
                    mfa_announced = True
            elif not login_submitted and _fill_login_form(page, username, password):
                login_submitted = True
                if progress:
                    progress("password_submitted", {})

        page.wait_for_timeout(1000)
    raise BrowserSessionError("等待真实浏览器完成登录超时")


def _capture_context_state(context, page, state_file):
    storage_state = context.storage_state()
    metadata = page.evaluate(
        """
        () => ({
          userAgent: navigator.userAgent,
          language: navigator.language,
          timezone: Intl.DateTimeFormat().resolvedOptions().timeZone
        })
        """
    )
    _atomic_json_write(state_file, storage_state)
    _atomic_json_write(_meta_path(state_file), metadata)
    return {
        "cookies": _school_cookie_records(storage_state),
        "token": _portal_token(storage_state),
        "state_file": str(state_file),
        "metadata": metadata,
    }


def bootstrap_interactive_browser(
    username,
    password,
    state_file,
    profile_dir,
    browser_executable=None,
    timeout=300,
    progress=None,
    keep_open=False,
):
    state_file = resolve_project_path(state_file, ".uestc_browser_state.json")
    profile_dir = resolve_project_path(profile_dir, ".uestc_chrome_profile")
    executable = find_browser_executable(browser_executable)
    profile_dir.mkdir(parents=True, exist_ok=True)
    clear_chromium_profile_locks(profile_dir)
    port = _free_local_port()
    command = [
        str(executable),
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile_dir}",
        "--remote-allow-origins=*",
        "--no-first-run",
        "--no-default-browser-check",
        PORTAL_PAGE_URL,
    ]
    process = subprocess.Popen(command, cwd=str(Path(__file__).resolve().parent))
    browser = None
    playwright = None
    try:
        endpoint = f"http://127.0.0.1:{port}"
        _wait_for_cdp(endpoint, timeout=30)

        sync_playwright = _import_playwright()
        playwright = sync_playwright().start()
        # Playwright 读取环境代理时可能把 127.0.0.1 CDP 也代理出去。
        with _without_proxy_env():
            browser = playwright.chromium.connect_over_cdp(endpoint)
        if not browser.contexts:
            raise BrowserSessionError("Chrome 没有可用的浏览器上下文")
        context = browser.contexts[0]
        page = next(
            (
                item
                for item in context.pages
                if urlparse(item.url).hostname in {"online.uestc.edu.cn", IDAS_HOST}
            ),
            None,
        )
        if page is None:
            page = context.new_page()
            page.goto(PORTAL_PAGE_URL, wait_until="domcontentloaded")

        bedroom = _wait_for_portal(
            page,
            username,
            password,
            timeout=timeout,
            interactive=True,
            progress=progress,
        )
        result = _capture_context_state(context, page, state_file)
        result["bedroom"] = bedroom
        return result
    finally:
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass
        if playwright is not None:
            try:
                playwright.stop()
            except Exception:
                pass
        if not keep_open and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()


def bootstrap_headful_playwright(
    username,
    password,
    state_file,
    profile_dir=None,
    browser_executable=None,
    timeout=900,
    progress=None,
    locale="zh-CN",
    timezone_id="Asia/Shanghai",
):
    """Linux/container 友好的有头登录：依赖 DISPLAY（如 Xvfb），无需 CDP 启动本机 Chrome。

    成功后写入 Playwright storage_state，并返回 cookies/token 供 Plan A session 导出。
    """
    state_file = resolve_project_path(state_file, ".uestc_browser_state.json")
    if profile_dir:
        profile_dir = resolve_project_path(profile_dir, ".uestc_chrome_profile")
        profile_dir.mkdir(parents=True, exist_ok=True)
        # Stale Singleton* from a previous auth container → Chromium exits at once
        # (noVNC black screen). Clear locks only; keep profile cookies/cache.
        clear_chromium_profile_locks(profile_dir)

    executable = None
    if browser_executable:
        executable = find_browser_executable(browser_executable)
    else:
        try:
            executable = find_browser_executable(None)
        except BrowserSessionError:
            executable = None  # fall back to Playwright-managed Chromium

    # Match docker/auth-entrypoint.sh Xvfb geometry (default 1600x900).
    view_w = int(os.getenv("AUTH_VIEWPORT_WIDTH", "1600"))
    view_h = int(os.getenv("AUTH_VIEWPORT_HEIGHT", "900"))
    launch_args = [
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--no-first-run",
        "--no-default-browser-check",
        f"--window-size={view_w},{view_h}",
        "--window-position=0,0",
        "--start-maximized",
    ]
    sync_playwright = _import_playwright()
    with sync_playwright() as playwright:
        launch_kwargs = {
            "headless": False,
            "args": launch_args,
        }
        if executable is not None:
            launch_kwargs["executable_path"] = str(executable)

        if profile_dir is not None:
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                locale=locale,
                timezone_id=timezone_id,
                no_viewport=True,
                **launch_kwargs,
            )
            browser = None
            page = context.pages[0] if context.pages else context.new_page()
            try:
                page.set_viewport_size({"width": view_w, "height": view_h})
            except Exception:
                pass
        else:
            browser = playwright.chromium.launch(**launch_kwargs)
            context = browser.new_context(
                locale=locale,
                timezone_id=timezone_id,
                viewport={"width": view_w, "height": view_h},
            )
            page = context.new_page()

        try:
            page.goto(PORTAL_PAGE_URL, wait_until="domcontentloaded")
            bedroom = _wait_for_portal(
                page,
                username,
                password,
                timeout=timeout,
                interactive=True,
                progress=progress,
            )
            result = _capture_context_state(context, page, state_file)
            result["bedroom"] = bedroom
            return result
        finally:
            try:
                context.close()
            except Exception:
                pass
            if browser is not None:
                try:
                    browser.close()
                except Exception:
                    pass


def refresh_browser_session(
    username,
    password,
    state_file,
    browser_executable=None,
    timeout=90,
    headless=True,
):
    state_file = resolve_project_path(state_file, ".uestc_browser_state.json")
    if not state_file.exists():
        raise BrowserSessionError(
            f"浏览器状态文件不存在: {state_file}；请先运行 bootstrap_browser.py"
        )
    executable = None
    try:
        executable = find_browser_executable(browser_executable)
    except BrowserSessionError:
        if browser_executable:
            raise
        executable = None
    metadata = _load_json(_meta_path(state_file), {})
    sync_playwright = _import_playwright()
    with sync_playwright() as playwright:
        launch_kwargs = {"headless": headless, "args": ["--no-sandbox", "--disable-dev-shm-usage"]}
        if executable is not None:
            launch_kwargs["executable_path"] = str(executable)
        browser = playwright.chromium.launch(**launch_kwargs)
        try:
            context_options = {"storage_state": str(state_file)}
            if metadata.get("userAgent"):
                context_options["user_agent"] = metadata["userAgent"]
            if metadata.get("language"):
                context_options["locale"] = metadata["language"]
            if metadata.get("timezone"):
                context_options["timezone_id"] = metadata["timezone"]
            context = browser.new_context(**context_options)
            page = context.new_page()
            page.goto(PORTAL_PAGE_URL, wait_until="domcontentloaded")
            bedroom = _wait_for_portal(
                page,
                username,
                password,
                timeout=timeout,
                interactive=False,
            )
            result = _capture_context_state(context, page, state_file)
            result["bedroom"] = bedroom
            return result
        finally:
            browser.close()
