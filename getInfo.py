import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

import execjs
import requests
from bs4 import BeautifulSoup

from env import (
    idas_cookie,
    online_accept_language,
    online_browser_executable,
    online_browser_refresh,
    online_browser_state_file,
    online_browser_timeout,
    online_cookie,
    online_remember_me,
    online_session_file,
    online_token,
    online_user_agent,
)


class UESTCAuthenticationError(Exception):
    """统一身份认证或门户会话无效。"""


class UESTCQueryError(Exception):
    """门户已认证，但寝室电费查询失败。"""


class UESTCLogin:
    """登录云中成电门户并读取当前账号绑定寝室的剩余电费。"""

    PORTAL_BASE_URL = "https://online.uestc.edu.cn"
    PORTAL_PAGE_URL = f"{PORTAL_BASE_URL}/page/"
    PORTAL_LOGIN_URL = (
        f"{PORTAL_BASE_URL}/common/actionCasLogin?"
        + urlencode({"redirect_url": PORTAL_PAGE_URL})
    )
    BEDROOM_URL = f"{PORTAL_BASE_URL}/site/bedroom"
    REQUEST_TIMEOUT = 20

    def __init__(
        self,
        username,
        password,
        portal_token=None,
        portal_cookie=None,
        cas_cookie=None,
        session_file=None,
        remember_me=None,
        user_agent=None,
        accept_language=None,
        browser_state_file=None,
        browser_executable=None,
        browser_refresh=None,
        browser_timeout=None,
        load_session=True,
    ):
        self.session = requests.Session()
        self.username = username
        self.password = password
        self.portal_token = portal_token if portal_token is not None else online_token
        self.portal_cookie = portal_cookie if portal_cookie is not None else online_cookie
        self.cas_cookie = cas_cookie if cas_cookie is not None else idas_cookie
        self.user_agent = user_agent if user_agent is not None else online_user_agent
        self.accept_language = (
            accept_language
            if accept_language is not None
            else online_accept_language
        )
        self.remember_me = (
            remember_me if remember_me is not None else online_remember_me
        )
        configured_session_file = (
            session_file if session_file is not None else online_session_file
        )
        self.session_file = self._resolve_session_file(configured_session_file)
        configured_browser_state = (
            browser_state_file
            if browser_state_file is not None
            else online_browser_state_file
        )
        self.browser_state_file = self._resolve_session_file(configured_browser_state)
        self.browser_executable = (
            browser_executable
            if browser_executable is not None
            else online_browser_executable
        )
        self.browser_refresh = (
            browser_refresh
            if browser_refresh is not None
            else online_browser_refresh
        )
        self.browser_timeout = (
            browser_timeout
            if browser_timeout is not None
            else online_browser_timeout
        )
        self._cookie_fingerprint = None
        self.headers = {
            "User-Agent": self.user_agent,
            "Accept-Language": self.accept_language,
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,image/apng,*/*;q=0.8"
            ),
            "Connection": "keep-alive",
        }
        encrypt_path = Path(__file__).with_name("encrypt.js")
        self.ctx = execjs.compile(encrypt_path.read_text(encoding="utf-8"))
        self._is_logged_in = False
        if load_session:
            self._load_session_cookies()
        self._install_cookie_header(
            self.cas_cookie,
            domain="idas.uestc.edu.cn",
            path="/authserver",
        )
        self._install_cookie_header(
            self.portal_cookie,
            domain="online.uestc.edu.cn",
            path="/",
        )

    @staticmethod
    def _resolve_session_file(session_file):
        if not session_file:
            return None
        path = Path(session_file).expanduser()
        if not path.is_absolute():
            path = Path(__file__).resolve().parent / path
        return path

    def _cookie_records(self):
        records = []
        for cookie in self.session.cookies:
            records.append(
                {
                    "name": cookie.name,
                    "value": cookie.value,
                    "domain": cookie.domain or "",
                    "path": cookie.path or "/",
                    "expires": cookie.expires,
                    "secure": bool(cookie.secure),
                }
            )
        return sorted(
            records,
            key=lambda item: (item["domain"], item["path"], item["name"]),
        )

    @staticmethod
    def _records_fingerprint(records):
        return json.dumps(records, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def _load_session_cookies(self):
        """从磁盘恢复上次成功查询时保存的 CAS/门户 Cookie。"""
        if not self.session_file or not self.session_file.exists():
            return
        try:
            payload = json.loads(self.session_file.read_text(encoding="utf-8"))
            records = payload.get("cookies", [])
            now = int(time.time())
            for item in records:
                expires = item.get("expires")
                if expires is not None and int(expires) <= now:
                    continue
                cookie_args = {
                    "path": item.get("path") or "/",
                    "secure": bool(item.get("secure")),
                }
                domain = item.get("domain")
                if domain:
                    cookie_args["domain"] = domain
                if expires is not None:
                    cookie_args["expires"] = int(expires)
                self.session.cookies.set(
                    item["name"],
                    item["value"],
                    **cookie_args,
                )
            self._cookie_fingerprint = self._records_fingerprint(
                self._cookie_records()
            )
        except (OSError, ValueError, KeyError, TypeError):
            # Cookie 缓存损坏时回退到账号密码登录，不让监控程序直接崩溃。
            self._cookie_fingerprint = None

    def _save_session_cookies(self):
        """仅在 Cookie 发生变化时原子保存，供服务器重启后继续使用。"""
        if not self.session_file:
            return
        records = self._cookie_records()
        fingerprint = self._records_fingerprint(records)
        if fingerprint == self._cookie_fingerprint:
            return

        payload = {
            "version": 1,
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "cookies": records,
        }
        self.session_file.parent.mkdir(parents=True, exist_ok=True)
        temporary_file = self.session_file.with_name(
            f"{self.session_file.name}.tmp"
        )
        temporary_file.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        try:
            os.chmod(temporary_file, 0o600)
        except OSError:
            pass
        os.replace(temporary_file, self.session_file)
        self._cookie_fingerprint = fingerprint

    def _install_cookie_header(self, cookie_header, domain, path):
        """将浏览器复制出的 Cookie 请求头安装到指定学校域名。"""
        if not cookie_header:
            return
        for item in cookie_header.split(";"):
            name, separator, value = item.strip().partition("=")
            if separator and name and value:
                self.session.cookies.set(
                    name,
                    value,
                    domain=domain,
                    path=path,
                )

    def install_browser_auth(self, browser_result):
        """把真实浏览器导出的 Cookie/token 写入当前 requests 会话并持久化。

        browser_result 期望来自 browser_session.bootstrap_interactive_browser /
        refresh_browser_session 的返回值，至少包含 cookies 列表，可选 token、
        metadata.userAgent、bedroom。
        """
        if not isinstance(browser_result, dict):
            raise UESTCAuthenticationError("浏览器认证结果格式无效")

        for item in browser_result.get("cookies") or []:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            if not name:
                continue
            cookie_args = {
                "path": item.get("path") or "/",
                "secure": bool(item.get("secure")),
            }
            domain = item.get("domain")
            if domain:
                cookie_args["domain"] = domain
            expires = item.get("expires")
            if expires is not None:
                try:
                    expires_int = int(expires)
                except (TypeError, ValueError):
                    expires_int = None
                if expires_int is not None and expires_int > 0:
                    cookie_args["expires"] = expires_int
            value = item.get("value")
            self.session.cookies.set(
                name,
                "" if value is None else value,
                **cookie_args,
            )

        token = browser_result.get("token")
        if token:
            self.portal_token = token

        metadata = browser_result.get("metadata") or {}
        user_agent = metadata.get("userAgent")
        if user_agent:
            self.user_agent = user_agent
            self.headers["User-Agent"] = user_agent
        language = metadata.get("language")
        if language:
            self.accept_language = language
            self.headers["Accept-Language"] = language

        self._save_session_cookies()
        if browser_result.get("bedroom") is not None:
            self._is_logged_in = True
        return True

    def _try_browser_session_refresh(self):
        """使用 Playwright storage state 在 headless Chrome 中刷新门户会话。

        遇到 MFA 时明确要求运行 bootstrap_browser.py，不自动重做微信/短信。
        浏览器依赖采用延迟导入，避免普通单测在未安装 Playwright 时失败。
        """
        if not self.browser_refresh:
            return False
        if not self.browser_state_file or not self.browser_state_file.exists():
            return False

        try:
            from browser_session import (
                BrowserMFARequired,
                BrowserSessionError,
                refresh_browser_session,
            )
        except ImportError:
            return False

        try:
            result = refresh_browser_session(
                username=self.username,
                password=self.password,
                state_file=self.browser_state_file,
                browser_executable=self.browser_executable,
                timeout=self.browser_timeout,
                headless=True,
            )
        except BrowserMFARequired as exc:
            raise UESTCAuthenticationError(
                f"{exc}；请运行 bootstrap_browser.py 在可见 Chrome 中重新完成复核"
            ) from exc
        except BrowserSessionError:
            return False

        self.install_browser_auth(result)
        if result.get("bedroom") is not None or self.is_session_valid():
            self._is_logged_in = True
            return True
        return False

    def encrypt_password(self, password, salt):
        return self.ctx.call("encryptPassword", password, salt)

    @staticmethod
    def _is_multifactor_response(response):
        return (
            "/reAuthCheck/" in response.url
            or "isMultifactor=true" in response.url
            or "reAuthLoginView" in response.url
        )

    @staticmethod
    def _hidden_form_fields(soup):
        fields = {}
        for element in soup.select("input[name]"):
            input_type = (element.get("type") or "").lower()
            if input_type == "hidden":
                fields[element["name"]] = element.get("value", "")
        return fields

    def _get_cas_login_page(self, allow_multifactor=False):
        response = self.session.get(
            self.PORTAL_LOGIN_URL,
            headers=self.headers,
            timeout=self.REQUEST_TIMEOUT,
        )
        response.raise_for_status()

        if self._is_multifactor_response(response):
            if allow_multifactor:
                return response, None, None, {}
            raise UESTCAuthenticationError(
                "统一身份认证要求进行多因素复核，自动查询无法代替该验证。"
                "请运行 bootstrap_browser.py 在真实 Chrome 中完成登录，"
                "或配置有效的 ONLINE_TOKEN / ONLINE_COOKIE 后重试。"
            )

        if response.url.startswith(self.PORTAL_BASE_URL):
            self._is_logged_in = True
            return response, None, None, {}

        soup = BeautifulSoup(response.text, "html.parser")
        execution = soup.find("input", {"name": "execution"})
        salt = soup.find("input", {"id": "pwdEncryptSalt"})
        if not execution or not salt:
            raise UESTCAuthenticationError("统一身份认证页面缺少必要的登录字段")
        return response, execution.get("value"), salt.get("value"), self._hidden_form_fields(soup)

    def _build_login_form(self, execution, salt, form_data):
        form_data.update(
            {
                "username": self.username,
                "password": self.encrypt_password(self.password, salt),
                "captcha": "",
                "_eventId": "submit",
                "cllt": "userNameLogin",
                "dllt": "generalLogin",
                "execution": execution,
            }
        )
        if self.remember_me:
            # 与登录页“7天免登录”复选框的真实字段保持一致。
            form_data["rememberMe"] = "true"
            form_data["rmShown"] = "1"
        return form_data

    def _password_login_response(self, allow_multifactor=False):
        response, execution, salt, form_data = self._get_cas_login_page(
            allow_multifactor=allow_multifactor
        )
        if self._is_logged_in:
            return response
        if allow_multifactor and self._is_multifactor_response(response):
            # 持久化 CASTGC 可能让 CAS 直接回到上次未完成的复核页。
            # 扫码引导应接管该页面，而不是再次要求账号密码表单。
            return response
        return self.session.post(
            response.url,
            data=self._build_login_form(execution, salt, form_data),
            headers=self.headers,
            allow_redirects=True,
            timeout=self.REQUEST_TIMEOUT,
        )

    def _complete_login_response(self, login_response):
        if "idas.uestc.edu.cn/authserver" in login_response.url:
            raise UESTCAuthenticationError("统一身份认证登录失败，请检查账号、密码或账号状态")

        if not login_response.url.startswith(self.PORTAL_BASE_URL):
            raise UESTCAuthenticationError(
                f"统一身份认证未返回云中成电门户: {login_response.url}"
            )

        self._is_logged_in = True
        self._save_session_cookies()
        return True

    def login(self):
        """建立 online.uestc.edu.cn 门户会话。

        优先级：
        1. 已保存的门户 Cookie / token / session 文件
        2. 浏览器 storage state 的 headless 刷新
        3. 原始 requests 账号密码登录（最后兜底，可能再次触发 MFA）
        """
        if self.is_session_valid():
            return True

        if self._try_browser_session_refresh():
            return True

        login_response = self._password_login_response()
        if self._is_logged_in:
            return True

        if self._is_multifactor_response(login_response):
            self._save_session_cookies()
            raise UESTCAuthenticationError(
                "账号密码验证成功，但统一身份认证要求进行多因素复核。"
                "纯 HTTP 会话通常无法复用“信任此浏览器”记录；"
                "请运行 bootstrap_browser.py 在真实 Chrome 中完成登录并选择信任，"
                "或配置有效的 ONLINE_TOKEN / ONLINE_COOKIE。"
            )
        return self._complete_login_response(login_response)

    @staticmethod
    def _extract_mfa_service(response):
        service = parse_qs(urlparse(response.url).query).get("service", [None])[0]
        if not service:
            raise UESTCAuthenticationError("多因素复核页面缺少 CAS service 参数")
        return service

    @staticmethod
    def _extract_mfa_uuid(response):
        soup = BeautifulSoup(response.text, "html.parser")
        element = soup.find(id="mfa_uuid") or soup.find("input", {"name": "mfa_uuid"})
        return element.get("value", "") if element else ""

    @staticmethod
    def _extract_reauth_type(response):
        import re

        patterns = (
            r'["\']?reAuthType["\']?\s*:\s*["\']([^"\']+)["\']',
            r'["\']?reAuthType["\']?\s*=\s*["\']([^"\']+)["\']',
        )
        for pattern in patterns:
            match = re.search(pattern, response.text)
            if match:
                return match.group(1)
        raise UESTCAuthenticationError("多因素复核页面缺少 reAuthType")

    def _select_reauth_type(self, context_path, service, current_type, target_type):
        """模拟复核页切换验证方式，并确认服务端已保存目标类型。"""
        target_type = str(target_type)
        if current_type == target_type:
            return current_type
        response = self.session.post(
            f"{context_path}/reAuthCheck/changeReAuthType.do",
            data={
                "isMultifactor": "true",
                "reAuthType": target_type,
                "service": service,
            },
            headers=self._mfa_ajax_headers(context_path, service),
            timeout=self.REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
        if str(payload.get("code")) != "1":
            raise UESTCAuthenticationError(
                payload.get("message") or f"无法切换到复核方式 {target_type}"
            )
        selected_type = str((payload.get("data") or {}).get("reAuthType") or "")
        if selected_type != target_type:
            raise UESTCAuthenticationError(
                f"统一身份认证没有启用复核方式 {target_type}，"
                f"当前类型: {selected_type or '未知'}"
            )
        return selected_type

    def _mfa_ajax_headers(self, context_path, service):
        referer = f"{context_path}/reAuthCheck/reAuthLoginView.do?" + urlencode(
            {"isMultifactor": "true", "service": service}
        )
        return {
            **self.headers,
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Origin": "https://idas.uestc.edu.cn",
            "Referer": referer,
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "X-Requested-With": "XMLHttpRequest",
        }

    def _mfa_navigation_headers(self, referer, cross_site=False):
        return {
            **self.headers,
            "Referer": referer,
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "cross-site" if cross_site else "same-origin",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
        }

    def _select_qr_reauth_type(self, context_path, service, current_type):
        """模拟复核页点击“微信扫码”；学校当前按钮 ID/类型为 8。"""
        return self._select_reauth_type(
            context_path,
            service,
            current_type,
            "8",
        )

    @staticmethod
    def _extract_reauth_user_id(response):
        """提取发送动态验证码所需的当前账号标识，不打印或持久化该值。"""
        import re

        soup = BeautifulSoup(response.text, "html.parser")
        element = soup.select_one("#username") or soup.find(
            "input",
            {"name": "username"},
        )
        if element and element.get("value", "").strip():
            return element.get("value", "").strip()

        patterns = (
            r'["\']?reAuthUserId["\']?\s*:\s*["\']([^"\']+)',
            r'["\']?reAuthUserId["\']?\s*=\s*["\']([^"\']+)',
        )
        for pattern in patterns:
            match = re.search(pattern, response.text)
            if match:
                return match.group(1).strip()
        raise UESTCAuthenticationError("多因素复核页面缺少短信验证码账号标识")

    def _send_sms_reauth_code(self, context_path, service, reauth_user_id):
        """按官方 type 3 协议向账号绑定手机号发送动态验证码。"""
        response = self.session.post(
            f"{context_path}/dynamicCode/getDynamicCodeByReauth.do",
            data={
                "userName": reauth_user_id,
                "authCodeTypeName": "reAuthDynamicCodeType",
            },
            headers=self._mfa_ajax_headers(context_path, service),
            timeout=self.REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError as exc:
            raise UESTCAuthenticationError("短信验证码接口没有返回 JSON") from exc

        if payload.get("res") != "success":
            raise UESTCAuthenticationError(
                payload.get("returnMessage") or "短信验证码发送失败"
            )
        return payload

    def _complete_sms_factor(
        self,
        login_response,
        code_provider,
        trust_device=False,
        progress=None,
    ):
        """完成当前 type 3 因子并返回 CAS 的下一页面。"""
        service = self._extract_mfa_service(login_response)
        current_reauth_type = self._extract_reauth_type(login_response)
        context_path = "https://idas.uestc.edu.cn/authserver"
        reauth_type = self._select_reauth_type(
            context_path,
            service,
            current_reauth_type,
            "3",
        )
        reauth_user_id = self._extract_reauth_user_id(login_response)
        self._send_sms_reauth_code(context_path, service, reauth_user_id)
        if progress:
            progress("sms_sent", {})

        dynamic_code = str(code_provider() or "").strip()
        if not dynamic_code:
            raise UESTCAuthenticationError("未输入短信验证码")

        submit_response = self.session.post(
            f"{context_path}/reAuthCheck/reAuthSubmit.do",
            data={
                "service": service,
                "reAuthType": reauth_type,
                "isMultifactor": "true",
                "password": "",
                "dynamicCode": dynamic_code,
                "uuid": "",
                "answer1": "",
                "answer2": "",
                "otpCode": "",
                "skipTmpReAuth": "true" if trust_device else "false",
            },
            headers=self._mfa_ajax_headers(context_path, service),
            timeout=self.REQUEST_TIMEOUT,
        )
        submit_response.raise_for_status()
        try:
            submit_data = submit_response.json()
        except ValueError as exc:
            raise UESTCAuthenticationError("短信复核提交接口没有返回 JSON") from exc
        if submit_data.get("code") in {"reAuth_failed", "reAuth_unauthorized"}:
            raise UESTCAuthenticationError(
                submit_data.get("msg") or "短信验证码复核失败"
            )
        if progress:
            progress(
                "factor_submit_result",
                {
                    "type": reauth_type,
                    "code": str(submit_data.get("code", "")),
                },
            )
        if progress:
            progress("sms_submitted", {})

        final_response = self.session.get(
            f"{context_path}/login",
            params={"service": service},
            headers=self._mfa_navigation_headers(login_response.url),
            allow_redirects=True,
            timeout=self.REQUEST_TIMEOUT,
        )
        self._save_session_cookies()
        return final_response

    def login_with_sms(self, code_provider, trust_device=False, progress=None):
        """账号密码认证后，通过官方 type 3 短信验证码完成一次复核。"""
        if self.is_session_valid():
            return True

        login_response = self._password_login_response(allow_multifactor=True)
        if self._is_logged_in:
            return True
        if not self._is_multifactor_response(login_response):
            return self._complete_login_response(login_response)

        self._save_session_cookies()
        final_response = self._complete_sms_factor(
            login_response,
            code_provider=code_provider,
            trust_device=trust_device,
            progress=progress,
        )
        if self._is_multifactor_response(final_response):
            final_type = self._extract_reauth_type(final_response)
            messages = self._auth_page_messages(final_response)
            detail = (
                f"；最终类型={final_type}；"
                f"跳转链={self._sanitized_response_chain(final_response)}"
            )
            if messages:
                detail += f"；页面提示={' | '.join(messages)}"
            raise UESTCAuthenticationError("短信已提交，但统一认证仍要求复核" + detail)

        self._complete_login_response(final_response)
        self._get_bedroom_data()
        return True

    @staticmethod
    def _parse_wechat_poll_response(text):
        import re

        error_match = re.search(r"wx_errcode\s*=\s*(\d+)", text)
        code_match = re.search(r"wx_code\s*=\s*['\"]([^'\"]*)['\"]", text)
        return (
            int(error_match.group(1)) if error_match else None,
            code_match.group(1) if code_match else "",
        )

    @staticmethod
    def _sanitized_response_chain(response):
        """只保留状态码、主机、路径和参数名，避免泄露 ticket/code/state。"""
        chain = []
        for item in [*response.history, response]:
            parsed = urlparse(item.url)
            query_keys = sorted(parse_qs(parsed.query, keep_blank_values=True))
            query_suffix = f"?参数={','.join(query_keys)}" if query_keys else ""
            chain.append(
                f"{item.status_code}:{parsed.hostname or '未知主机'}"
                f"{parsed.path}{query_suffix}"
            )
        return " -> ".join(chain)

    @staticmethod
    def _auth_page_messages(response):
        """从认证页已知提示区域读取短消息，不转储完整页面或表单内容。"""
        soup = BeautifulSoup(response.text, "html.parser")
        messages = []
        for selector in (
            ".reauth_error_submit",
            ".reauth_error",
            ".authError",
            ".error-msg",
            ".loginError",
            "[role='alert']",
            ".alert-danger",
        ):
            for element in soup.select(selector):
                text = element.get_text(" ", strip=True)
                if text and text not in messages:
                    messages.append(text[:120])
        return messages

    def _finish_wechat_callback(
        self,
        callback_response,
        context_path,
        service,
        allow_next_factor=False,
    ):
        """完成微信 callback 后的 CAS service 收尾，并在失败时给出脱敏诊断。"""
        self._save_session_cookies()
        attempts = [("回调", callback_response)]

        if allow_next_factor and self._is_multifactor_response(callback_response):
            return callback_response

        if not self._is_multifactor_response(callback_response):
            try:
                self._complete_login_response(callback_response)
                self._get_bedroom_data()
                return True
            except UESTCAuthenticationError:
                # callback 有时只更新 CAS 状态，仍需按官方流程访问 /login?service=。
                pass

        final_response = self.session.get(
            f"{context_path}/login",
            params={"service": service},
            headers=self._mfa_navigation_headers(callback_response.url),
            allow_redirects=True,
            timeout=self.REQUEST_TIMEOUT,
        )
        self._save_session_cookies()
        attempts.append(("CAS收尾", final_response))

        if allow_next_factor and self._is_multifactor_response(final_response):
            return final_response

        if not self._is_multifactor_response(final_response):
            try:
                self._complete_login_response(final_response)
                self._get_bedroom_data()
                return True
            except UESTCAuthenticationError:
                pass

        detail_parts = []
        for label, response in attempts:
            final_type = "不适用"
            if self._is_multifactor_response(response):
                try:
                    final_type = self._extract_reauth_type(response)
                except UESTCAuthenticationError:
                    final_type = "未知"
            part = (
                f"{label}[最终类型={final_type}；"
                f"跳转链={self._sanitized_response_chain(response)}"
            )
            page_messages = self._auth_page_messages(response)
            if page_messages:
                part += f"；页面提示={' | '.join(page_messages)}"
            detail_parts.append(part + "]")

        raise UESTCAuthenticationError(
            "微信已确认，但 IDAS 未能完成 CAS service 回调；"
            + "；".join(detail_parts)
        )

    def _login_with_wechat_combined(
        self,
        context_path,
        service,
        qr_path,
        timeout,
        trust_device,
        progress,
        allow_next_factor=False,
        referer=None,
    ):
        """按 IDAS type 8 的微信 OAuth 联合登录流程完成二次复核。"""
        qr_file = Path(qr_path).expanduser().resolve()
        deadline = time.monotonic() + timeout

        while time.monotonic() < deadline:
            combined_response = self.session.get(
                f"{context_path}/combinedLogin.do",
                params={
                    "type": "weixin",
                    "reAuth": "2",
                    "success": service,
                    "skipTmpReAuth": "true" if trust_device else "false",
                },
                headers=self._mfa_navigation_headers(referer or context_path),
                allow_redirects=True,
                timeout=self.REQUEST_TIMEOUT,
            )
            combined_response.raise_for_status()
            if urlparse(combined_response.url).hostname != "open.weixin.qq.com":
                raise UESTCAuthenticationError(
                    f"微信联合登录没有进入微信页面: {combined_response.url}"
                )

            soup = BeautifulSoup(combined_response.text, "html.parser")
            qr_element = soup.select_one("img.js_qrcode_img")
            if not qr_element or not qr_element.get("src"):
                raise UESTCAuthenticationError("微信登录页面缺少二维码地址")
            qr_url = urljoin(combined_response.url, qr_element["src"])

            poll_match = None
            import re

            for script in soup.find_all("script"):
                script_text = script.string or script.get_text() or ""
                poll_match = re.search(
                    r"fordevtool\s*=\s*['\"]([^'\"]+/connect/l/qrconnect\?uuid=[^'\"]+)['\"]",
                    script_text,
                )
                if poll_match:
                    break
            if not poll_match:
                raise UESTCAuthenticationError("微信登录页面缺少二维码状态轮询地址")
            poll_url = poll_match.group(1).replace("&amp;", "&")

            state = parse_qs(urlparse(combined_response.url).query).get("state", [""])[0]
            if not state:
                raise UESTCAuthenticationError("微信登录页面缺少 OAuth state")

            qr_response = self.session.get(
                qr_url,
                headers={**self.headers, "Referer": combined_response.url},
                timeout=self.REQUEST_TIMEOUT,
            )
            qr_response.raise_for_status()
            qr_file.parent.mkdir(parents=True, exist_ok=True)
            qr_file.write_bytes(qr_response.content)
            try:
                os.chmod(qr_file, 0o600)
            except OSError:
                pass
            if progress:
                progress("qr_ready", {"path": str(qr_file)})

            last_status = None
            scanned = False
            while time.monotonic() < deadline:
                poll_params = {"last": last_status} if last_status is not None else None
                poll_response = self.session.get(
                    poll_url,
                    params=poll_params,
                    headers={**self.headers, "Referer": combined_response.url},
                    timeout=min(35, self.REQUEST_TIMEOUT + 15),
                )
                poll_response.raise_for_status()
                status, wx_code = self._parse_wechat_poll_response(
                    poll_response.text
                )
                if status == 405 and wx_code:
                    if progress:
                        progress("confirmed", {})
                    callback_response = self.session.get(
                        f"{context_path}/callback",
                        params={"code": wx_code, "state": state},
                        headers=self._mfa_navigation_headers(
                            combined_response.url,
                            cross_site=True,
                        ),
                        allow_redirects=True,
                        timeout=self.REQUEST_TIMEOUT,
                    )
                    if progress:
                        progress(
                            "factor_submit_result",
                            {"type": "8", "code": "oauth_callback"},
                        )
                    return self._finish_wechat_callback(
                        callback_response,
                        context_path,
                        service,
                        allow_next_factor=allow_next_factor,
                    )
                if status == 404:
                    last_status = 404
                    if not scanned:
                        scanned = True
                        if progress:
                            progress("scanned", {})
                    continue
                if status == 403:
                    raise UESTCAuthenticationError("已在微信中取消本次登录")
                if status == 402:
                    # 微信二维码过期，回到外层重新创建 OAuth state 和二维码。
                    break
                if status in {408, None}:
                    continue
                raise UESTCAuthenticationError(
                    f"微信二维码返回未知状态: {status}"
                )

        raise UESTCAuthenticationError("等待微信扫码确认超时，请重新运行引导")

    def login_with_multifactor(
        self,
        code_provider,
        qr_path=".uestc_mfa_qr.png",
        timeout=180,
        trust_device=False,
        progress=None,
        max_factors=4,
    ):
        """按 IDAS 返回的顺序连续完成 type 3/8，直到获得门户会话。"""
        if self.is_session_valid():
            return True

        response = self._password_login_response(allow_multifactor=True)
        if self._is_logged_in:
            return True
        if not self._is_multifactor_response(response):
            self._complete_login_response(response)
            self._get_bedroom_data()
            return True

        self._save_session_cookies()
        completed_types = []
        context_path = "https://idas.uestc.edu.cn/authserver"

        for _ in range(max_factors):
            current_type = self._extract_reauth_type(response)
            if current_type in completed_types:
                raise UESTCAuthenticationError(
                    "统一身份认证重复要求已完成的复核因子 "
                    f"{current_type}，为避免循环已停止；"
                    f"已完成顺序={','.join(completed_types) or '无'}"
                )
            if progress:
                progress("factor_required", {"type": current_type})

            if current_type == "3":
                response = self._complete_sms_factor(
                    response,
                    code_provider=code_provider,
                    trust_device=trust_device,
                    progress=progress,
                )
            elif current_type == "8":
                service = self._extract_mfa_service(response)
                self._select_qr_reauth_type(
                    context_path,
                    service,
                    current_type,
                )
                response = self._login_with_wechat_combined(
                    context_path=context_path,
                    service=service,
                    qr_path=qr_path,
                    timeout=timeout,
                    trust_device=trust_device,
                    progress=progress,
                    allow_next_factor=True,
                    referer=response.url,
                )
                if response is True:
                    return True
            else:
                raise UESTCAuthenticationError(
                    f"自动引导暂不支持统一认证复核类型 {current_type}"
                )

            completed_types.append(current_type)
            self._save_session_cookies()
            if self._is_multifactor_response(response) and progress:
                progress(
                    "factor_transition",
                    {
                        "from": current_type,
                        "to": self._extract_reauth_type(response),
                    },
                )
            if not self._is_multifactor_response(response):
                self._complete_login_response(response)
                self._get_bedroom_data()
                return True

        raise UESTCAuthenticationError(
            f"统一身份认证在 {max_factors} 个因子后仍未完成，已停止继续复核"
        )

    def login_with_qr(
        self,
        qr_path=".uestc_mfa_qr.png",
        timeout=180,
        trust_device=False,
        progress=None,
    ):
        """
        账号密码认证后，通过官方微信扫码复核完成 CAS 登录。

        progress(event, data) 会收到 qr_ready、scanned、confirmed 事件。
        trust_device=True 对应复核页中的“信任此浏览器”。
        """
        if self.is_session_valid():
            return True

        login_response = self._password_login_response(allow_multifactor=True)
        if self._is_logged_in:
            return True
        if not self._is_multifactor_response(login_response):
            return self._complete_login_response(login_response)

        self._save_session_cookies()
        service = self._extract_mfa_service(login_response)
        initial_uuid = self._extract_mfa_uuid(login_response)
        current_reauth_type = self._extract_reauth_type(login_response)
        context_path = "https://idas.uestc.edu.cn/authserver"
        reauth_type = self._select_qr_reauth_type(
            context_path,
            service,
            current_reauth_type,
        )
        if reauth_type == "8":
            return self._login_with_wechat_combined(
                context_path=context_path,
                service=service,
                qr_path=qr_path,
                timeout=timeout,
                trust_device=trust_device,
                progress=progress,
                referer=login_response.url,
            )

        qr_file = Path(qr_path).expanduser().resolve()

        def generate_qr(previous_uuid):
            token_response = self.session.get(
                f"{context_path}/reAuthCheck/getToken",
                params={"ts": int(time.time() * 1000), "uuid": previous_uuid},
                headers=self.headers,
                timeout=self.REQUEST_TIMEOUT,
            )
            token_response.raise_for_status()
            token_data = token_response.json()
            if str(token_data.get("errCode")) != "1" or not token_data.get("uuid"):
                raise UESTCAuthenticationError(
                    token_data.get("errMsg") or "统一身份认证未能生成复核二维码"
                )
            current_uuid = token_data["uuid"]
            qr_response = self.session.get(
                f"{context_path}/reAuthCheck/getCode",
                params={"uuid": current_uuid},
                headers=self.headers,
                timeout=self.REQUEST_TIMEOUT,
            )
            qr_response.raise_for_status()
            qr_file.parent.mkdir(parents=True, exist_ok=True)
            qr_file.write_bytes(qr_response.content)
            try:
                os.chmod(qr_file, 0o600)
            except OSError:
                pass
            if progress:
                progress("qr_ready", {"path": str(qr_file)})
            return current_uuid

        mfa_uuid = generate_qr(initial_uuid)

        deadline = time.monotonic() + timeout
        scanned = False
        while time.monotonic() < deadline:
            status_response = self.session.get(
                f"{context_path}/reAuthCheck/getStatus.htl",
                params={"ts": int(time.time() * 1000), "uuid": mfa_uuid},
                headers=self.headers,
                timeout=self.REQUEST_TIMEOUT,
            )
            status_response.raise_for_status()
            status = status_response.text.strip()
            if status == "1":
                if progress:
                    progress("confirmed", {})
                break
            if status == "2" and not scanned:
                scanned = True
                if progress:
                    progress("scanned", {})
            if status == "3":
                if time.monotonic() + 5 >= deadline:
                    raise UESTCAuthenticationError("等待扫码确认超时，请重新运行引导")
                mfa_uuid = generate_qr(mfa_uuid)
                scanned = False
                continue
            time.sleep(1)
        else:
            raise UESTCAuthenticationError("等待扫码确认超时，请重新运行引导")

        submit_response = self.session.post(
            f"{context_path}/reAuthCheck/reAuthSubmit.do",
            data={
                "service": service,
                "reAuthType": reauth_type,
                "isMultifactor": "true",
                "password": "",
                "dynamicCode": "",
                # 官方 type 8 的 doLogin() 不提交 UUID；扫码状态保存在服务端会话。
                "uuid": mfa_uuid if reauth_type == "14" else "",
                "answer1": "",
                "answer2": "",
                "otpCode": "",
                "skipTmpReAuth": "true" if trust_device else "false",
            },
            headers=self._mfa_ajax_headers(context_path, service),
            timeout=self.REQUEST_TIMEOUT,
        )
        submit_response.raise_for_status()
        submit_data = submit_response.json()
        if submit_data.get("code") in {"reAuth_failed", "reAuth_unauthorized"}:
            raise UESTCAuthenticationError(
                submit_data.get("msg") or "多因素复核提交失败"
            )

        final_response = self.session.get(
            f"{context_path}/login",
            params={"service": service},
            headers=self._mfa_navigation_headers(login_response.url),
            allow_redirects=True,
            timeout=self.REQUEST_TIMEOUT,
        )
        if self._is_multifactor_response(final_response):
            raise UESTCAuthenticationError("扫码已确认，但统一身份认证仍要求复核")
        self._complete_login_response(final_response)
        self._get_bedroom_data()
        return True

    def _bedroom_headers(self):
        headers = {
            **self.headers,
            "Accept": "application/json, text/plain, */*",
            "Referer": self.PORTAL_PAGE_URL,
            "Origin": self.PORTAL_BASE_URL,
            "X-Requested-With": "XMLHttpRequest",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
        }
        if self.portal_token:
            headers["token"] = self.portal_token
        return headers

    def _get_bedroom_data(self):
        last_error = None
        # 门户偶发返回签名二级查询形态（内含校内 IP），外网不可达；
        # 直接结果与二级形态会交替出现，因此做有限次重试。
        for attempt in range(5):
            response = self.session.get(
                self.BEDROOM_URL,
                headers=self._bedroom_headers(),
                allow_redirects=True,
                timeout=self.REQUEST_TIMEOUT,
            )

            if self._is_multifactor_response(response):
                raise UESTCAuthenticationError("统一身份认证要求进行多因素复核")
            if "idas.uestc.edu.cn/authserver" in response.url:
                raise UESTCAuthenticationError("云中成电门户会话已失效")
            if response.status_code == 401:
                raise UESTCAuthenticationError("云中成电门户会话已失效")
            if response.status_code == 403:
                raise UESTCQueryError("当前账号没有寝室电费接口访问权限")

            response.raise_for_status()
            try:
                payload = response.json()
            except ValueError as exc:
                content_type = response.headers.get("Content-Type", "")
                if "html" in content_type.lower() or response.url.startswith(
                    self.PORTAL_PAGE_URL
                ):
                    raise UESTCAuthenticationError(
                        "电费接口返回了登录页面，云中成电门户会话无效"
                    ) from exc
                raise UESTCQueryError("电费接口没有返回 JSON") from exc

            if not isinstance(payload, dict):
                raise UESTCQueryError(
                    f"电费接口响应格式错误: {type(payload).__name__}"
                )

            code = payload.get("e")
            if code in (401, "401"):
                raise UESTCAuthenticationError(
                    payload.get("m") or "云中成电门户会话已失效"
                )
            if code not in (0, "0"):
                raise UESTCQueryError(
                    payload.get("m") or f"电费查询失败，错误码: {code}"
                )

            data = self._normalize_bedroom_payload(payload.get("d"))
            if self._is_complete_bedroom_data(data):
                self._save_session_cookies()
                return data

            if self._is_secondary_bedroom_payload(data):
                last_error = UESTCQueryError(
                    "电费接口返回了需二次请求的签名数据，但校内查询地址不可达；"
                    "正在重试门户直接结果"
                )
                time.sleep(0.35 * (attempt + 1))
                continue

            last_error = UESTCQueryError(
                "电费接口响应中缺少可用的寝室电费字段"
                f"（d 类型={type(payload.get('d')).__name__}）"
            )
            time.sleep(0.2 * (attempt + 1))

        if last_error is not None:
            raise last_error
        raise UESTCQueryError("电费接口响应中缺少 d 对象")

    @staticmethod
    def _normalize_bedroom_payload(data):
        """兼容 d 为对象，或带前缀/转义的 JSON 字符串。"""
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
                nested = UESTCLogin._normalize_bedroom_payload(parsed)
                if nested is not None:
                    return nested
        return None

    @staticmethod
    def _is_complete_bedroom_data(data):
        if not isinstance(data, dict):
            return False
        if data.get("syje") is not None or data.get("sydl") is not None:
            return True
        # 有些成功响应同时带 retcode/msg。
        if data.get("retcode") in (0, "0") and (
            data.get("fjh") or data.get("roomName") or data.get("roomId")
        ):
            return True
        return False

    @staticmethod
    def _is_secondary_bedroom_payload(data):
        if not isinstance(data, dict):
            return False
        if data.get("syje") is not None or data.get("sydl") is not None:
            return False
        return bool(data.get("url") or data.get("sign") or data.get("str3"))

    def is_session_valid(self):
        """以当前网页真实使用的 /site/bedroom 接口检查会话。"""
        try:
            self._get_bedroom_data()
            self._is_logged_in = True
            return True
        except UESTCAuthenticationError:
            self._is_logged_in = False
            return False
        except UESTCQueryError:
            # 门户返回了业务错误，说明认证本身仍有效。
            self._is_logged_in = True
            return True
        except requests.RequestException:
            self._is_logged_in = False
            return False

    def query_room_info(self, roomid=None):
        """
        查询当前账号绑定的寝室电费。

        当前网页接口不接受 room_id 参数；保留 roomid 仅用于兼容旧调用方。
        现行 /site/bedroom 同时返回剩余金额 syje 与剩余电量 sydl。
        """
        try:
            room_info = self._get_bedroom_data()
        except UESTCAuthenticationError:
            self.login()
            room_info = self._get_bedroom_data()

        room_name = room_info.get("fjh") or room_info.get("roomName")
        remaining_amount = room_info.get("syje")
        remaining_electricity = room_info.get("sydl")
        if not room_name and remaining_amount is None and remaining_electricity is None:
            raise UESTCQueryError("当前账号没有可用的寝室电费信息")

        return {
            "building_id": room_info.get("buiId"),
            "room_id": (
                str(roomid)
                if roomid is not None
                else (str(room_info.get("roomId")) if room_info.get("roomId") is not None else None)
            ),
            "room_name": room_name,
            "remaining_amount": remaining_amount,
            "remaining_electricity": remaining_electricity,
            "source": self.BEDROOM_URL,
        }


if __name__ == "__main__":
    from env import password, username

    login = UESTCLogin(username, password)
    info = login.query_room_info()
    print(
        json.dumps(
            {
                "room_name": info["room_name"],
                "remaining_amount": info["remaining_amount"],
                "remaining_electricity": info["remaining_electricity"],
                "source": info["source"],
            },
            ensure_ascii=False,
        )
    )
