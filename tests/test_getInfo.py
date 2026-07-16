import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock

import requests

from bootstrap_session import progress
from getInfo import UESTCAuthenticationError, UESTCLogin, UESTCQueryError


class UESTCLoginTests(unittest.TestCase):
    @staticmethod
    def make_login():
        login = UESTCLogin.__new__(UESTCLogin)
        login._is_logged_in = False
        return login

    @staticmethod
    def make_mfa_response(reauth_type):
        response = Mock()
        response.status_code = 200
        response.url = (
            "https://idas.uestc.edu.cn/authserver/reAuthCheck/"
            "reAuthLoginView.do?isMultifactor=true&"
            "service=https%3A%2F%2Fonline.uestc.edu.cn%2Fcommon%2FactionCasLogin"
        )
        response.text = f'var reAuthType = "{reauth_type}";'
        response.history = []
        return response

    def test_query_maps_current_portal_fields(self):
        login = self.make_login()
        login._get_bedroom_data = Mock(
            return_value={
                "fjh": "示例寝室",
                "syje": "18.50",
                "sydl": "66.6",
                "roomId": "5123",
                "buiId": "31",
            }
        )

        result = login.query_room_info("legacy-room-id")

        self.assertEqual(result["room_name"], "示例寝室")
        self.assertEqual(result["remaining_amount"], "18.50")
        self.assertEqual(result["remaining_electricity"], "66.6")
        self.assertEqual(result["room_id"], "legacy-room-id")
        self.assertEqual(result["building_id"], "31")
        self.assertEqual(result["source"], "https://online.uestc.edu.cn/site/bedroom")

    def test_normalize_bedroom_payload_accepts_json_string(self):
        payload = UESTCLogin._normalize_bedroom_payload(
            '{"fjh":"215","syje":"35.72","sydl":"66.6"}'
        )
        self.assertEqual(payload["fjh"], "215")
        self.assertEqual(payload["syje"], "35.72")
        self.assertEqual(
            UESTCLogin._normalize_bedroom_payload({"fjh": "215"}),
            {"fjh": "215"},
        )
        self.assertIsNone(UESTCLogin._normalize_bedroom_payload("not-json"))
        self.assertIsNone(UESTCLogin._normalize_bedroom_payload(None))

    def test_normalize_bedroom_payload_extracts_prefixed_json(self):
        payload = UESTCLogin._normalize_bedroom_payload(
            '失败{"fjh":"215","syje":"35.72","sydl":"66.6"}'
        )
        self.assertEqual(payload["fjh"], "215")
        self.assertEqual(payload["syje"], "35.72")

    def test_secondary_bedroom_payload_is_detected(self):
        secondary = {
            "fjh": "215",
            "url": "http://222.197.164.98:7000/zxapi/services/query/findeletric",
            "sign": "ABC",
            "str3": "payload",
        }
        self.assertTrue(UESTCLogin._is_secondary_bedroom_payload(secondary))
        self.assertFalse(
            UESTCLogin._is_complete_bedroom_data(secondary)
        )
        self.assertTrue(
            UESTCLogin._is_complete_bedroom_data(
                {"fjh": "215", "syje": "1.23", "sydl": "2.3"}
            )
        )

    def test_get_bedroom_data_retries_secondary_payload(self):
        login = self.make_login()
        login.session = Mock()
        login.headers = {}
        login.REQUEST_TIMEOUT = 20
        login.portal_token = None
        login._save_session_cookies = Mock()
        login._is_multifactor_response = Mock(return_value=False)

        secondary = Mock()
        secondary.status_code = 200
        secondary.url = "https://online.uestc.edu.cn/site/bedroom"
        secondary.headers = {"Content-Type": "application/json"}
        secondary.json.return_value = {
            "e": 0,
            "d": '失败{"fjh":"215","url":"http://example.invalid","sign":"x"}',
            "m": "ok",
        }
        secondary.raise_for_status = Mock()

        direct = Mock()
        direct.status_code = 200
        direct.url = "https://online.uestc.edu.cn/site/bedroom"
        direct.headers = {"Content-Type": "application/json"}
        direct.json.return_value = {
            "e": 0,
            "d": {"fjh": "215", "syje": "35.72", "sydl": "66.6"},
            "m": "ok",
        }
        direct.raise_for_status = Mock()

        login.session.get.side_effect = [secondary, direct]

        data = login._get_bedroom_data()

        self.assertEqual(data["syje"], "35.72")
        self.assertEqual(login.session.get.call_count, 2)
        login._save_session_cookies.assert_called_once_with()

    def test_query_reauthenticates_after_expired_session(self):
        login = self.make_login()
        login._get_bedroom_data = Mock(
            side_effect=[
                UESTCAuthenticationError("expired"),
                {"fjh": "示例寝室", "syje": 30},
            ]
        )
        login.login = Mock(return_value=True)

        result = login.query_room_info()

        login.login.assert_called_once_with()
        self.assertEqual(result["remaining_amount"], 30)

    def test_session_business_error_still_means_authenticated(self):
        login = self.make_login()
        login._get_bedroom_data = Mock(side_effect=UESTCQueryError("未绑定寝室"))

        self.assertTrue(login.is_session_valid())
        self.assertTrue(login._is_logged_in)

    def test_session_authentication_error_is_invalid(self):
        login = self.make_login()
        login._get_bedroom_data = Mock(side_effect=UESTCAuthenticationError("expired"))

        self.assertFalse(login.is_session_valid())
        self.assertFalse(login._is_logged_in)

    def test_query_retries_when_endpoint_returns_http_401_as_auth_error(self):
        login = self.make_login()
        login._get_bedroom_data = Mock(
            side_effect=[
                UESTCAuthenticationError("HTTP 401"),
                {"fjh": "示例寝室", "syje": "9.90"},
            ]
        )
        login.login = Mock(return_value=True)

        result = login.query_room_info()

        login.login.assert_called_once_with()
        self.assertEqual(result["remaining_amount"], "9.90")

    def test_session_cookies_are_persisted_and_restored(self):
        with TemporaryDirectory() as directory:
            session_file = Path(directory) / "cookies.json"

            writer = self.make_login()
            writer.session = requests.Session()
            writer.session_file = session_file
            writer._cookie_fingerprint = None
            writer.session.cookies.set(
                "cookie_vjuid_portal_login",
                "test-cookie-value",
                domain="online.uestc.edu.cn",
                path="/",
            )
            writer._save_session_cookies()

            reader = self.make_login()
            reader.session = requests.Session()
            reader.session_file = session_file
            reader._cookie_fingerprint = None
            reader._load_session_cookies()

            self.assertEqual(
                reader.session.cookies.get(
                    "cookie_vjuid_portal_login",
                    domain="online.uestc.edu.cn",
                    path="/",
                ),
                "test-cookie-value",
            )

    def test_cookie_save_skips_unchanged_cookie_jar(self):
        login = self.make_login()
        login.session = requests.Session()
        login.session_file = Mock()
        login._cookie_fingerprint = login._records_fingerprint([])

        login._save_session_cookies()

        login.session_file.parent.mkdir.assert_not_called()

    def test_build_login_form_enables_remember_me(self):
        login = self.make_login()
        login.username = "user"
        login.password = "password"
        login.remember_me = True
        login.encrypt_password = Mock(return_value="encrypted")

        form = login._build_login_form("execution", "salt", {"lt": "value"})

        self.assertEqual(form["username"], "user")
        self.assertEqual(form["password"], "encrypted")
        self.assertEqual(form["rememberMe"], "true")
        self.assertEqual(form["rmShown"], "1")

    def test_mfa_service_is_read_from_redirect_url(self):
        response = Mock()
        response.url = (
            "https://idas.uestc.edu.cn/authserver/reAuthCheck/reAuthLoginView.do"
            "?isMultifactor=true&service=https%3A%2F%2Fonline.uestc.edu.cn%2Fcallback"
        )

        service = UESTCLogin._extract_mfa_service(response)

        self.assertEqual(service, "https://online.uestc.edu.cn/callback")

    def test_browser_cookie_headers_are_scoped_to_correct_domains(self):
        login = self.make_login()
        login.session = requests.Session()

        login._install_cookie_header(
            "trusted_device=idas-value; shared=value",
            domain="idas.uestc.edu.cn",
            path="/authserver",
        )
        login._install_cookie_header(
            "cookie_vjuid_portal_login=portal-value",
            domain="online.uestc.edu.cn",
            path="/",
        )

        self.assertEqual(
            login.session.cookies.get(
                "trusted_device",
                domain="idas.uestc.edu.cn",
                path="/authserver",
            ),
            "idas-value",
        )
        self.assertEqual(
            login.session.cookies.get(
                "cookie_vjuid_portal_login",
                domain="online.uestc.edu.cn",
                path="/",
            ),
            "portal-value",
        )

    def test_qr_login_can_resume_cached_multifactor_page(self):
        login = self.make_login()
        login._is_logged_in = False
        response = Mock()
        response.url = (
            "https://idas.uestc.edu.cn/authserver/reAuthCheck/reAuthLoginView.do"
            "?isMultifactor=true&service=https%3A%2F%2Fonline.uestc.edu.cn%2Fcallback"
        )
        login._get_cas_login_page = Mock(
            return_value=(response, None, None, {})
        )
        login.session = Mock()

        result = login._password_login_response(allow_multifactor=True)

        self.assertIs(result, response)
        login.session.post.assert_not_called()

    def test_progress_events_without_path_do_not_fail(self):
        progress("scanned", {})
        progress("confirmed", {})

    def test_select_qr_reauth_type_switches_server_to_type_8(self):
        login = self.make_login()
        login.headers = {}
        login.REQUEST_TIMEOUT = 20
        response = Mock()
        response.json.return_value = {
            "code": 1,
            "data": {"reAuthType": "8"},
        }
        login.session = Mock()
        login.session.post.return_value = response

        selected = login._select_qr_reauth_type(
            "https://idas.uestc.edu.cn/authserver",
            "https://online.uestc.edu.cn/common/actionCasLogin",
            "3",
        )

        self.assertEqual(selected, "8")
        response.raise_for_status.assert_called_once_with()
        sent_data = login.session.post.call_args.kwargs["data"]
        self.assertEqual(sent_data["reAuthType"], "8")
        self.assertEqual(sent_data["isMultifactor"], "true")

    def test_select_qr_reauth_type_skips_request_when_already_type_8(self):
        login = self.make_login()
        login.session = Mock()

        selected = login._select_qr_reauth_type(
            "https://idas.uestc.edu.cn/authserver",
            "https://online.uestc.edu.cn/common/actionCasLogin",
            "8",
        )

        self.assertEqual(selected, "8")
        login.session.post.assert_not_called()

    def test_parse_wechat_poll_response(self):
        status, code = UESTCLogin._parse_wechat_poll_response(
            "window.wx_errcode=405;window.wx_code='temporary-code';"
        )

        self.assertEqual(status, 405)
        self.assertEqual(code, "temporary-code")

    def test_parse_wechat_scanned_status_without_code(self):
        status, code = UESTCLogin._parse_wechat_poll_response(
            "window.wx_errcode=404;window.wx_code='';"
        )

        self.assertEqual(status, 404)
        self.assertEqual(code, "")

    def test_sanitized_response_chain_does_not_include_query_values(self):
        response = Mock()
        response.status_code = 200
        response.url = (
            "https://idas.uestc.edu.cn/authserver/callback"
            "?code=secret-code&state=secret-state"
        )
        response.history = []

        chain = UESTCLogin._sanitized_response_chain(response)

        self.assertIn("参数=code,state", chain)
        self.assertNotIn("secret-code", chain)
        self.assertNotIn("secret-state", chain)

    def test_wechat_callback_uses_official_cas_login_finalizer(self):
        login = self.make_login()
        login.session = Mock()
        login.headers = {}
        login.REQUEST_TIMEOUT = 20
        login._save_session_cookies = Mock()
        login._complete_login_response = Mock(return_value=True)
        login._get_bedroom_data = Mock(return_value={"fjh": "示例寝室", "syje": 20})

        callback_response = Mock()
        callback_response.url = (
            "https://idas.uestc.edu.cn/authserver/reAuthCheck/reAuthLoginView.do"
        )
        callback_response.history = []

        final_response = Mock()
        final_response.url = "https://online.uestc.edu.cn/page/"
        final_response.history = []
        login.session.get.return_value = final_response

        result = login._finish_wechat_callback(
            callback_response,
            "https://idas.uestc.edu.cn/authserver",
            "https://online.uestc.edu.cn/common/actionCasLogin",
        )

        self.assertTrue(result)
        login.session.get.assert_called_once_with(
            "https://idas.uestc.edu.cn/authserver/login",
            params={
                "service": "https://online.uestc.edu.cn/common/actionCasLogin"
            },
            headers=login._mfa_navigation_headers(callback_response.url),
            allow_redirects=True,
            timeout=20,
        )
        login._complete_login_response.assert_called_once_with(final_response)
        login._get_bedroom_data.assert_called_once_with()

    def test_extract_reauth_user_id_from_username_input(self):
        response = Mock()
        response.text = '<input id="username" value="current-user-id" readonly>'

        result = UESTCLogin._extract_reauth_user_id(response)

        self.assertEqual(result, "current-user-id")

    def test_send_sms_reauth_code_uses_official_type_3_parameters(self):
        login = self.make_login()
        login.session = Mock()
        login.headers = {}
        login.REQUEST_TIMEOUT = 20
        response = Mock()
        response.json.return_value = {"res": "success", "codeTime": 120}
        login.session.post.return_value = response

        result = login._send_sms_reauth_code(
            "https://idas.uestc.edu.cn/authserver",
            "https://online.uestc.edu.cn/common/actionCasLogin",
            "current-user-id",
        )

        self.assertEqual(result["res"], "success")
        login.session.post.assert_called_once_with(
            "https://idas.uestc.edu.cn/authserver/dynamicCode/"
            "getDynamicCodeByReauth.do",
            data={
                "userName": "current-user-id",
                "authCodeTypeName": "reAuthDynamicCodeType",
            },
            headers=login._mfa_ajax_headers(
                "https://idas.uestc.edu.cn/authserver",
                "https://online.uestc.edu.cn/common/actionCasLogin",
            ),
            timeout=20,
        )

    def test_sms_login_submits_code_and_trust_device(self):
        login = self.make_login()
        login.session = Mock()
        login.headers = {}
        login.REQUEST_TIMEOUT = 20
        login.is_session_valid = Mock(return_value=False)
        login._save_session_cookies = Mock()
        login._extract_mfa_service = Mock(
            return_value="https://online.uestc.edu.cn/common/actionCasLogin"
        )
        login._extract_reauth_type = Mock(return_value="3")
        login._select_reauth_type = Mock(return_value="3")
        login._extract_reauth_user_id = Mock(return_value="current-user-id")
        login._send_sms_reauth_code = Mock(return_value={"res": "success"})
        login._complete_login_response = Mock(return_value=True)
        login._get_bedroom_data = Mock(return_value={"fjh": "示例寝室", "syje": 20})

        mfa_response = Mock()
        mfa_response.url = (
            "https://idas.uestc.edu.cn/authserver/reAuthCheck/"
            "reAuthLoginView.do?isMultifactor=true"
        )
        login._password_login_response = Mock(return_value=mfa_response)

        submit_response = Mock()
        submit_response.json.return_value = {"code": "success"}
        final_response = Mock()
        final_response.url = "https://online.uestc.edu.cn/page/"
        final_response.history = []
        login.session.post.return_value = submit_response
        login.session.get.return_value = final_response
        progress_callback = Mock()

        result = login.login_with_sms(
            code_provider=Mock(return_value="123456"),
            trust_device=True,
            progress=progress_callback,
        )

        self.assertTrue(result)
        submit_data = login.session.post.call_args.kwargs["data"]
        self.assertEqual(submit_data["reAuthType"], "3")
        self.assertEqual(submit_data["dynamicCode"], "123456")
        self.assertEqual(submit_data["skipTmpReAuth"], "true")
        progress_callback.assert_any_call("sms_sent", {})
        progress_callback.assert_any_call("sms_submitted", {})
        login._complete_login_response.assert_called_once_with(final_response)
        login._get_bedroom_data.assert_called_once_with()

    def test_wechat_callback_can_return_next_required_factor(self):
        login = self.make_login()
        login.session = Mock()
        login._save_session_cookies = Mock()
        callback_response = self.make_mfa_response("3")

        result = login._finish_wechat_callback(
            callback_response,
            "https://idas.uestc.edu.cn/authserver",
            "https://online.uestc.edu.cn/common/actionCasLogin",
            allow_next_factor=True,
        )

        self.assertIs(result, callback_response)
        login.session.get.assert_not_called()

    def test_auto_multifactor_resumes_cached_wechat_factor(self):
        login = self.make_login()
        login.is_session_valid = Mock(return_value=False)
        login._password_login_response = Mock(return_value=self.make_mfa_response("8"))
        login._save_session_cookies = Mock()
        login._select_qr_reauth_type = Mock(return_value="8")
        login._login_with_wechat_combined = Mock(return_value=True)
        progress_callback = Mock()

        result = login.login_with_multifactor(
            code_provider=Mock(),
            trust_device=True,
            progress=progress_callback,
        )

        self.assertTrue(result)
        progress_callback.assert_any_call("factor_required", {"type": "8"})
        login._login_with_wechat_combined.assert_called_once()

    def test_auto_multifactor_chains_sms_then_wechat(self):
        login = self.make_login()
        sms_response = self.make_mfa_response("3")
        wechat_response = self.make_mfa_response("8")
        login.is_session_valid = Mock(return_value=False)
        login._password_login_response = Mock(return_value=sms_response)
        login._save_session_cookies = Mock()
        login._complete_sms_factor = Mock(return_value=wechat_response)
        login._select_qr_reauth_type = Mock(return_value="8")
        login._login_with_wechat_combined = Mock(return_value=True)
        progress_callback = Mock()

        result = login.login_with_multifactor(
            code_provider=Mock(return_value="123456"),
            trust_device=True,
            progress=progress_callback,
        )

        self.assertTrue(result)
        progress_callback.assert_any_call("factor_required", {"type": "3"})
        progress_callback.assert_any_call("factor_required", {"type": "8"})
        login._complete_sms_factor.assert_called_once()
        login._login_with_wechat_combined.assert_called_once()

    def test_auto_multifactor_stops_repeated_factor_loop(self):
        login = self.make_login()
        sms_response = self.make_mfa_response("3")
        login.is_session_valid = Mock(return_value=False)
        login._password_login_response = Mock(return_value=sms_response)
        login._save_session_cookies = Mock()
        login._complete_sms_factor = Mock(return_value=self.make_mfa_response("3"))

        with self.assertRaisesRegex(UESTCAuthenticationError, "避免循环"):
            login.login_with_multifactor(code_provider=Mock(return_value="123456"))

    def test_install_browser_auth_imports_cookies_token_and_user_agent(self):
        with TemporaryDirectory() as directory:
            session_file = Path(directory) / "cookies.json"
            login = self.make_login()
            login.session = requests.Session()
            login.session_file = session_file
            login._cookie_fingerprint = None
            login.headers = {"User-Agent": "old-agent", "Accept-Language": "en"}
            login.portal_token = None

            result = login.install_browser_auth(
                {
                    "cookies": [
                        {
                            "name": "cookie_vjuid_portal_login",
                            "value": "portal-from-browser",
                            "domain": "online.uestc.edu.cn",
                            "path": "/",
                            "secure": True,
                            "expires": None,
                        },
                        {
                            "name": "CASTGC",
                            "value": "cas-from-browser",
                            "domain": "idas.uestc.edu.cn",
                            "path": "/authserver",
                            "secure": True,
                            "expires": 4102444800,
                        },
                    ],
                    "token": "portal-token-from-browser",
                    "metadata": {
                        "userAgent": "Mozilla/5.0 TestBrowser",
                        "language": "zh-CN",
                    },
                    "bedroom": {"fjh": "示例寝室", "syje": "12.34"},
                }
            )

            self.assertTrue(result)
            self.assertTrue(login._is_logged_in)
            self.assertEqual(login.portal_token, "portal-token-from-browser")
            self.assertEqual(login.headers["User-Agent"], "Mozilla/5.0 TestBrowser")
            self.assertEqual(login.headers["Accept-Language"], "zh-CN")
            self.assertEqual(
                login.session.cookies.get(
                    "cookie_vjuid_portal_login",
                    domain="online.uestc.edu.cn",
                    path="/",
                ),
                "portal-from-browser",
            )
            self.assertEqual(
                login.session.cookies.get(
                    "CASTGC",
                    domain="idas.uestc.edu.cn",
                    path="/authserver",
                ),
                "cas-from-browser",
            )
            self.assertTrue(session_file.exists())

    def test_login_prefers_valid_session_before_browser_or_password(self):
        login = self.make_login()
        login.is_session_valid = Mock(return_value=True)
        login._try_browser_session_refresh = Mock(return_value=False)
        login._password_login_response = Mock()

        self.assertTrue(login.login())
        login.is_session_valid.assert_called_once_with()
        login._try_browser_session_refresh.assert_not_called()
        login._password_login_response.assert_not_called()

    def test_login_uses_browser_refresh_before_password(self):
        login = self.make_login()
        login.is_session_valid = Mock(return_value=False)
        login._try_browser_session_refresh = Mock(return_value=True)
        login._password_login_response = Mock()

        self.assertTrue(login.login())
        login._try_browser_session_refresh.assert_called_once_with()
        login._password_login_response.assert_not_called()

    def test_login_falls_back_to_password_when_browser_refresh_unavailable(self):
        login = self.make_login()
        login.is_session_valid = Mock(return_value=False)
        login._try_browser_session_refresh = Mock(return_value=False)
        final_response = Mock()
        final_response.url = "https://online.uestc.edu.cn/page/"
        login._password_login_response = Mock(return_value=final_response)
        login._is_logged_in = False
        login._is_multifactor_response = Mock(return_value=False)
        login._complete_login_response = Mock(return_value=True)

        self.assertTrue(login.login())
        login._password_login_response.assert_called_once_with()
        login._complete_login_response.assert_called_once_with(final_response)

    def test_login_mfa_message_points_to_browser_bootstrap(self):
        login = self.make_login()
        login.is_session_valid = Mock(return_value=False)
        login._try_browser_session_refresh = Mock(return_value=False)
        login._password_login_response = Mock(
            return_value=self.make_mfa_response("8")
        )
        login._is_logged_in = False
        login._save_session_cookies = Mock()

        with self.assertRaisesRegex(UESTCAuthenticationError, "bootstrap_browser.py"):
            login.login()
        login._save_session_cookies.assert_called_once_with()

    def test_browser_refresh_surfaces_mfa_requirement(self):
        from browser_session import BrowserMFARequired

        login = self.make_login()
        login.browser_refresh = True
        state_file = Mock()
        state_file.exists.return_value = True
        login.browser_state_file = state_file
        login.browser_executable = None
        login.browser_timeout = 30
        login.username = "user"
        login.password = "password"

        import browser_session as browser_session_module

        original = browser_session_module.refresh_browser_session
        browser_session_module.refresh_browser_session = Mock(
            side_effect=BrowserMFARequired("需要重新复核")
        )
        try:
            with self.assertRaisesRegex(UESTCAuthenticationError, "bootstrap_browser.py"):
                login._try_browser_session_refresh()
        finally:
            browser_session_module.refresh_browser_session = original

    def test_browser_refresh_skips_when_disabled_or_missing_state(self):
        login = self.make_login()
        login.browser_refresh = False
        login.browser_state_file = Path("missing.json")
        self.assertFalse(login._try_browser_session_refresh())

        login.browser_refresh = True
        login.browser_state_file = None
        self.assertFalse(login._try_browser_session_refresh())

    def test_browser_session_helpers_filter_school_cookies_and_token(self):
        from browser_session import _portal_token, _school_cookie_records

        storage_state = {
            "cookies": [
                {
                    "name": "keep",
                    "value": "school",
                    "domain": ".online.uestc.edu.cn",
                    "path": "/",
                    "expires": -1,
                    "secure": True,
                },
                {
                    "name": "drop",
                    "value": "other",
                    "domain": "example.com",
                    "path": "/",
                    "expires": 100,
                    "secure": False,
                },
            ],
            "origins": [
                {
                    "origin": "https://online.uestc.edu.cn",
                    "localStorage": [{"name": "token", "value": "portal-token"}],
                }
            ],
        }

        cookies = _school_cookie_records(storage_state)
        self.assertEqual(len(cookies), 1)
        self.assertEqual(cookies[0]["name"], "keep")
        self.assertIsNone(cookies[0]["expires"])
        self.assertEqual(_portal_token(storage_state), "portal-token")

    def test_find_browser_executable_prefers_system_chrome_over_playwright(self):
        from browser_session import find_browser_executable
        from unittest.mock import patch

        system_chrome = Path("C:/Program Files/Google/Chrome/Application/chrome.exe")
        playwright_chrome = Path(
            "C:/Users/demo/AppData/Local/ms-playwright/chromium/chrome.exe"
        )

        def existing_executable(path):
            path = Path(path)
            text = str(path).replace("\\", "/")
            if text.endswith("Program Files/Google/Chrome/Application/chrome.exe"):
                return system_chrome
            if "ms-playwright" in text:
                return playwright_chrome
            return None

        with patch(
            "browser_session._existing_executable", side_effect=existing_executable
        ), patch("browser_session.shutil.which", return_value=str(playwright_chrome)):
            selected = find_browser_executable()

        self.assertEqual(selected, system_chrome)

    def test_find_browser_executable_skips_chrome_junction_to_playwright(self):
        from browser_session import find_browser_executable
        from unittest.mock import patch

        local_chrome = Path(
            "C:/Users/demo/AppData/Local/Google/Chrome/Application/chrome.exe"
        )
        playwright_chrome = Path(
            "C:/Users/demo/AppData/Local/ms-playwright/chromium/chrome.exe"
        )
        system_edge = Path(
            "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
        )

        def existing_executable(path):
            path = Path(path)
            text = str(path).replace("\\", "/")
            if text.endswith("Local/Google/Chrome/Application/chrome.exe"):
                # 模拟被 junction 到 Playwright Chromium 的“假 Chrome”。
                return playwright_chrome
            if text.endswith("Microsoft/Edge/Application/msedge.exe"):
                return system_edge
            if "ms-playwright" in text:
                return playwright_chrome
            return None

        with patch(
            "browser_session._existing_executable", side_effect=existing_executable
        ), patch("browser_session.shutil.which", return_value=None), patch(
            "browser_session.os.getenv",
            side_effect=lambda key, default=None: {
                "LOCALAPPDATA": "C:/Users/demo/AppData/Local",
                "ProgramFiles": "C:/Program Files",
                "ProgramFiles(x86)": "C:/Program Files (x86)",
            }.get(key, default),
        ):
            selected = find_browser_executable()

        self.assertEqual(selected, system_edge)
        self.assertNotEqual(selected, local_chrome)

    def test_without_proxy_env_restores_original_values(self):
        import os
        from browser_session import _without_proxy_env

        os.environ["HTTP_PROXY"] = "http://127.0.0.1:7890"
        os.environ["https_proxy"] = "http://127.0.0.1:7890"
        with _without_proxy_env():
            self.assertNotIn("HTTP_PROXY", os.environ)
            self.assertNotIn("https_proxy", os.environ)
            self.assertIn("127.0.0.1", os.environ.get("NO_PROXY", ""))
        self.assertEqual(os.environ.get("HTTP_PROXY"), "http://127.0.0.1:7890")
        self.assertEqual(os.environ.get("https_proxy"), "http://127.0.0.1:7890")


if __name__ == "__main__":
    unittest.main()
