import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from browser_session import (
    BrowserMFARequired,
    BrowserSessionError,
    refresh_browser_session,
    resolve_project_path,
)
from env import (
    online_browser_executable,
    online_browser_state_file,
    online_browser_timeout,
    online_session_file,
    password,
    username,
)
from getInfo import UESTCAuthenticationError, UESTCLogin, UESTCQueryError


def _room_summary(room_info):
    if not isinstance(room_info, dict):
        return {}
    return {
        "room_name": room_info.get("fjh") or room_info.get("roomName"),
        "remaining_amount": room_info.get("syje"),
        "remaining_electricity": room_info.get("sydl"),
    }


def _export_plan_a_bundle(export_dir, session_file, browser_state_file=None, include_browser_state=False):
    """导出服务器方案 A 所需文件：主要是 session Cookie。"""
    export_dir = Path(export_dir).expanduser()
    if not export_dir.is_absolute():
        export_dir = Path(__file__).resolve().parent / export_dir
    export_dir.mkdir(parents=True, exist_ok=True)

    copied = []
    if not session_file or not Path(session_file).exists():
        raise BrowserSessionError("没有可导出的 session 文件")

    session_src = Path(session_file)
    session_dst = export_dir / session_src.name
    shutil.copy2(session_src, session_dst)
    copied.append(str(session_dst))

    if include_browser_state and browser_state_file:
        state_src = Path(browser_state_file)
        if state_src.exists():
            state_dst = export_dir / state_src.name
            shutil.copy2(state_src, state_dst)
            copied.append(str(state_dst))
            meta_src = state_src.with_name(f"{state_src.name}.meta.json")
            if meta_src.exists():
                meta_dst = export_dir / meta_src.name
                shutil.copy2(meta_src, meta_dst)
                copied.append(str(meta_dst))

    server_env = export_dir / "server.env.example"
    server_env.write_text(
        "\n".join(
            [
                "# 方案 A：无 GUI 服务器只使用 Cookie 会话，不跑浏览器刷新",
                'ONLINE_SESSION_FILE=".uestc_session.json"',
                'ONLINE_BROWSER_REFRESH="false"',
                'ONLINE_REMEMBER_ME="true"',
                "# 将本目录中的 .uestc_session.json 拷到服务器持久化路径后，",
                "# 把 ONLINE_SESSION_FILE 改成该绝对路径即可。",
                "",
            ]
        ),
        encoding="utf-8",
    )
    copied.append(str(server_env))

    readme = export_dir / "PLAN_A_README.txt"
    readme.write_text(
        "\n".join(
            [
                "UESTC 电费监测 - 方案 A 凭据包",
                f"exported_at: {datetime.now(timezone.utc).isoformat()}",
                "",
                "服务器部署：",
                "1. 拷贝 .uestc_session.json 到服务器持久化目录",
                "2. .env 中设置：",
                '   ONLINE_SESSION_FILE="/path/to/.uestc_session.json"',
                '   ONLINE_BROWSER_REFRESH="false"',
                "3. 运行: python main.py 或 python getInfo.py",
                "",
                "注意：",
                "- 该文件等同登录凭据，权限应设为 600，不要提交 Git",
                "- 会话失效后，在本机运行 refresh_credentials.py 再同步新文件",
                "- 若提示 MFA，需要本机运行 bootstrap_browser.py 重新建立可信浏览器态",
                "",
            ]
        ),
        encoding="utf-8",
    )
    copied.append(str(readme))
    return {"export_dir": str(export_dir), "files": copied}


def refresh_credentials(
    state_file=None,
    session_file=None,
    browser_executable=None,
    timeout=None,
    force_browser=False,
    headless=True,
    export_dir=None,
    include_browser_state=False,
):
    """
    本机刷新方案 A 凭据。

    顺序：
    1. 现有 session 仍有效 -> 直接复用（除非 --force-browser）
    2. headless 加载 browser state 刷新
    3. 写入 .uestc_session.json 并验证 /site/bedroom

    若 IDAS 要求 MFA，抛出 UESTCAuthenticationError / BrowserMFARequired，
    提示运行 bootstrap_browser.py；不会自动进入微信/短信循环。
    """
    state_file = resolve_project_path(
        state_file or online_browser_state_file, ".uestc_browser_state.json"
    )
    configured_session = session_file if session_file is not None else online_session_file
    timeout = online_browser_timeout if timeout is None else timeout
    browser_executable = (
        browser_executable
        if browser_executable is not None
        else online_browser_executable
    )

    client = UESTCLogin(
        username,
        password,
        session_file=configured_session,
        browser_state_file=str(state_file),
        browser_executable=browser_executable,
        browser_refresh=False,
        browser_timeout=timeout,
        load_session=True,
    )

    method = None
    room_info = None

    if not force_browser:
        try:
            if client.is_session_valid():
                method = "existing_session"
                room_info = client._get_bedroom_data()
        except (UESTCAuthenticationError, UESTCQueryError, OSError):
            method = None

    if method is None:
        if not state_file.exists():
            raise BrowserSessionError(
                f"浏览器状态文件不存在: {state_file}；请先运行 bootstrap_browser.py"
            )
        try:
            result = refresh_browser_session(
                username=username,
                password=password,
                state_file=state_file,
                browser_executable=browser_executable,
                timeout=timeout,
                headless=headless,
            )
        except BrowserMFARequired as exc:
            raise UESTCAuthenticationError(
                f"{exc}；本机自动刷新无法代替 MFA，请运行 bootstrap_browser.py"
            ) from exc

        client.install_browser_auth(result)
        room_info = result.get("bedroom")
        if not isinstance(room_info, dict) or not UESTCLogin._is_complete_bedroom_data(
            room_info
        ):
            room_info = client._get_bedroom_data()
        method = "browser_state_refresh"

    # 再读一次，确保磁盘 session 与可查询状态一致。
    verify_client = UESTCLogin(
        username,
        password,
        session_file=configured_session,
        browser_refresh=False,
        load_session=True,
    )
    verified = verify_client._get_bedroom_data()
    if not room_info:
        room_info = verified

    output = {
        "success": True,
        "method": method,
        "session_file": str(verify_client.session_file)
        if verify_client.session_file
        else None,
        "browser_state_file": str(state_file),
        **_room_summary(room_info),
        "source": UESTCLogin.BEDROOM_URL,
        "refreshed_at": datetime.now(timezone.utc).isoformat(),
        "server_plan": "A",
        "server_hint": (
            "方案 A 服务器只需同步 session 文件，并设置 ONLINE_BROWSER_REFRESH=false"
        ),
    }

    if export_dir:
        output["export"] = _export_plan_a_bundle(
            export_dir=export_dir,
            session_file=verify_client.session_file,
            browser_state_file=state_file,
            include_browser_state=include_browser_state,
        )
    return output


def main():
    parser = argparse.ArgumentParser(
        description=(
            "本机全自动刷新 UESTC 凭据（方案 A）。"
            "优先复用 session；失效时用 headless 浏览器 state 刷新，"
            "并写回 .uestc_session.json 供服务器使用。"
        )
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=online_browser_timeout,
        help="headless 浏览器刷新超时秒数",
    )
    parser.add_argument(
        "--state",
        default=online_browser_state_file,
        help="Playwright storage state 路径",
    )
    parser.add_argument(
        "--session",
        default=online_session_file,
        help="输出/更新的 requests Cookie 会话文件",
    )
    parser.add_argument(
        "--browser-executable",
        default=online_browser_executable,
        help="Chrome/Chromium/Edge 路径",
    )
    parser.add_argument(
        "--force-browser",
        action="store_true",
        help="即使现有 session 有效，也强制走浏览器 state 刷新",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="使用可见浏览器刷新（默认 headless 全自动）",
    )
    parser.add_argument(
        "--export-dir",
        default=None,
        help="导出方案 A 服务器凭据包目录（含 session 与说明）",
    )
    parser.add_argument(
        "--include-browser-state",
        action="store_true",
        help="导出时附带 browser state（方案 A 服务器不需要，仅作备份）",
    )
    args = parser.parse_args()

    result = refresh_credentials(
        state_file=args.state,
        session_file=args.session,
        browser_executable=args.browser_executable,
        timeout=args.timeout,
        force_browser=args.force_browser,
        headless=not args.headed,
        export_dir=args.export_dir,
        include_browser_state=args.include_browser_state,
    )
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    try:
        main()
    except BrowserMFARequired as exc:
        print(
            json.dumps(
                {
                    "success": False,
                    "type": "BrowserMFARequired",
                    "message": str(exc),
                    "action": "run bootstrap_browser.py",
                    "exit_code": 2,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        raise SystemExit(2)
    except UESTCAuthenticationError as exc:
        message = str(exc)
        exit_code = 2 if "bootstrap_browser" in message or "MFA" in message or "多因素" in message else 1
        print(
            json.dumps(
                {
                    "success": False,
                    "type": type(exc).__name__,
                    "message": message,
                    "action": "run bootstrap_browser.py"
                    if exit_code == 2
                    else "check logs",
                    "exit_code": exit_code,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        raise SystemExit(exit_code)
    except Exception as exc:
        print(
            json.dumps(
                {
                    "success": False,
                    "type": type(exc).__name__,
                    "message": str(exc),
                    "exit_code": 1,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        raise SystemExit(1)
