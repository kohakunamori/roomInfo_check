import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from getInfo import UESTCAuthenticationError
from refresh_credentials import _export_plan_a_bundle, refresh_credentials


class RefreshCredentialsTests(unittest.TestCase):
    def test_export_plan_a_bundle_copies_session_and_writes_hints(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            session = root / ".uestc_session.json"
            session.write_text(
                json.dumps({"version": 1, "cookies": []}), encoding="utf-8"
            )
            export_dir = root / "bundle"

            result = _export_plan_a_bundle(
                export_dir=export_dir,
                session_file=session,
                browser_state_file=None,
                include_browser_state=False,
            )

            self.assertTrue((export_dir / ".uestc_session.json").exists())
            self.assertTrue((export_dir / "server.env.example").exists())
            self.assertTrue((export_dir / "PLAN_A_README.txt").exists())
            self.assertIn(str(export_dir / ".uestc_session.json"), result["files"])
            env_text = (export_dir / "server.env.example").read_text(encoding="utf-8")
            self.assertIn('ONLINE_BROWSER_REFRESH="false"', env_text)

    def test_refresh_reuses_valid_session_without_browser(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            session = root / "session.json"
            state = root / "state.json"
            session.write_text("{}", encoding="utf-8")
            state.write_text("{}", encoding="utf-8")

            bedroom = {"fjh": "215", "syje": "10", "sydl": "20"}

            mock_client = Mock()
            mock_client.is_session_valid.return_value = True
            mock_client._get_bedroom_data.return_value = bedroom
            mock_client.session_file = session

            mock_verify = Mock()
            mock_verify._get_bedroom_data.return_value = bedroom
            mock_verify.session_file = session

            with patch(
                "refresh_credentials.UESTCLogin", side_effect=[mock_client, mock_verify]
            ), patch("refresh_credentials.refresh_browser_session") as refresh_mock:
                result = refresh_credentials(
                    state_file=str(state),
                    session_file=str(session),
                    force_browser=False,
                )

            self.assertTrue(result["success"])
            self.assertEqual(result["method"], "existing_session")
            self.assertEqual(result["room_name"], "215")
            refresh_mock.assert_not_called()

    def test_refresh_uses_browser_state_when_session_invalid(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            session = root / "session.json"
            state = root / "state.json"
            session.write_text("{}", encoding="utf-8")
            state.write_text("{}", encoding="utf-8")
            bedroom = {"fjh": "215", "syje": "12.5", "sydl": "30"}

            mock_client = Mock()
            mock_client.is_session_valid.return_value = False
            mock_client.session_file = session
            mock_client.install_browser_auth = Mock()
            mock_client._get_bedroom_data.return_value = bedroom

            mock_verify = Mock()
            mock_verify._get_bedroom_data.return_value = bedroom
            mock_verify.session_file = session

            browser_result = {
                "cookies": [],
                "token": None,
                "bedroom": bedroom,
                "metadata": {},
            }

            with patch(
                "refresh_credentials.UESTCLogin", side_effect=[mock_client, mock_verify]
            ), patch(
                "refresh_credentials.refresh_browser_session",
                return_value=browser_result,
            ) as refresh_mock, patch(
                "refresh_credentials.UESTCLogin._is_complete_bedroom_data",
                return_value=True,
            ):
                result = refresh_credentials(
                    state_file=str(state),
                    session_file=str(session),
                    force_browser=False,
                )

            self.assertEqual(result["method"], "browser_state_refresh")
            self.assertEqual(result["remaining_amount"], "12.5")
            refresh_mock.assert_called_once()
            mock_client.install_browser_auth.assert_called_once_with(browser_result)

    def test_refresh_mfa_becomes_auth_error(self):
        from browser_session import BrowserMFARequired

        with TemporaryDirectory() as directory:
            root = Path(directory)
            session = root / "session.json"
            state = root / "state.json"
            session.write_text("{}", encoding="utf-8")
            state.write_text("{}", encoding="utf-8")

            mock_client = Mock()
            mock_client.is_session_valid.return_value = False
            mock_client.session_file = session

            with patch(
                "refresh_credentials.UESTCLogin", return_value=mock_client
            ), patch(
                "refresh_credentials.refresh_browser_session",
                side_effect=BrowserMFARequired("need mfa"),
            ):
                with self.assertRaisesRegex(UESTCAuthenticationError, "bootstrap_browser"):
                    refresh_credentials(
                        state_file=str(state),
                        session_file=str(session),
                        force_browser=True,
                    )


if __name__ == "__main__":
    unittest.main()
