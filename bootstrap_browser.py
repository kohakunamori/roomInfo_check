import argparse
import json
from pathlib import Path

from browser_session import bootstrap_interactive_browser
from env import (
    online_browser_executable,
    online_browser_profile_dir,
    online_browser_state_file,
    password,
    username,
)
from getInfo import UESTCLogin


def progress(event, data):
    if event == "password_submitted":
        message = "账号密码已在真实 Chrome 中提交。"
    elif event == "mfa_required":
        message = (
            "浏览器已进入多因素复核页；请在打开的 Chrome 中完成验证，"
            "并选择“信任此浏览器”。"
        )
    else:
        message = f"浏览器认证状态更新: {event}"
    print(message, flush=True)


def main():
    parser = argparse.ArgumentParser(
        description="用真实 Chrome 完成 UESTC 登录并导出可部署的浏览器状态"
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="等待浏览器登录完成的秒数",
    )
    parser.add_argument(
        "--state",
        default=online_browser_state_file,
        help="Playwright storage state 输出路径",
    )
    parser.add_argument(
        "--profile",
        default=online_browser_profile_dir,
        help="首次交互登录使用的专用 Chrome profile 目录",
    )
    parser.add_argument(
        "--browser-executable",
        default=online_browser_executable,
        help="Chrome/Chromium/Edge 可执行文件路径",
    )
    parser.add_argument(
        "--keep-open",
        action="store_true",
        help="导出状态后暂不关闭浏览器",
    )
    args = parser.parse_args()

    result = bootstrap_interactive_browser(
        username=username,
        password=password,
        state_file=args.state,
        profile_dir=args.profile,
        browser_executable=args.browser_executable,
        timeout=args.timeout,
        progress=progress,
        keep_open=args.keep_open,
    )

    client = UESTCLogin(username, password, load_session=False)
    client.install_browser_auth(result)
    # 优先复用浏览器内已验证的 bedroom，避免紧接着再打一次接口时
    # 门户偶发返回字符串形态的 d 导致误报失败。
    room_info = result.get("bedroom")
    if not isinstance(room_info, dict):
        room_info = client._get_bedroom_data()
    output = {
        "success": True,
        "room_name": room_info.get("fjh") or room_info.get("roomName"),
        "remaining_amount": room_info.get("syje"),
        "remaining_electricity": room_info.get("sydl"),
        "browser_state_file": str(Path(args.state).expanduser()),
        "session_file": str(client.session_file) if client.session_file else None,
    }
    print(json.dumps(output, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(
            json.dumps(
                {
                    "success": False,
                    "type": type(exc).__name__,
                    "message": str(exc),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        raise SystemExit(1)
