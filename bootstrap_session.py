import argparse
import json
import os
import shutil
from datetime import datetime, timezone
from getpass import getpass
from pathlib import Path

from env import password, username
from getInfo import UESTCLogin


def progress(event, data):
    if event == "qr_ready":
        message = f"QR_READY {data.get('path', '.uestc_mfa_qr.png')}"
    elif event == "scanned":
        message = "二维码已扫描，请在手机上确认登录。"
    elif event == "confirmed":
        message = "手机端已确认，正在换取门户 Cookie。"
    elif event == "sms_sent":
        message = "短信验证码已发送，请在当前终端输入收到的验证码。"
    elif event == "sms_submitted":
        message = "短信验证码已提交，正在换取门户 Cookie。"
    elif event == "factor_required":
        factor_type = str(data.get("type", ""))
        factor_names = {
            "3": "手机短信验证码",
            "8": "微信扫码确认",
        }
        message = f"统一认证要求下一复核因子：{factor_names.get(factor_type, factor_type)}"
    elif event == "factor_submit_result":
        message = (
            f"复核因子 {data.get('type', '未知')} 已提交，"
            f"结果码={data.get('code') or '空'}"
        )
    elif event == "factor_transition":
        message = (
            f"IDAS 因子跳转：{data.get('from', '未知')}"
            f" → {data.get('to', '未知')}"
        )
    else:
        message = f"认证状态更新: {event}"
    print(message, flush=True)


def main():
    parser = argparse.ArgumentParser(
        description="通过账号密码和一次多因素复核建立并持久化云中成电会话"
    )
    parser.add_argument(
        "--qr",
        default=".uestc_mfa_qr.png",
        help="临时二维码图片路径",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=180,
        help="等待扫码确认的秒数",
    )
    parser.add_argument(
        "--method",
        choices=("auto", "wechat", "sms"),
        default="auto",
        help="复核方式；auto 会按 IDAS 要求连续完成短信和微信因子",
    )
    parser.add_argument(
        "--trust-device",
        action="store_true",
        help="复核成功后选择统一认证页面的“信任此浏览器”",
    )
    parser.add_argument(
        "--keep-qr",
        action="store_true",
        help="完成或失败后保留临时二维码图片",
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="忽略旧 Cookie 缓存并在开始前创建备份，用于清理卡住的 MFA 状态",
    )
    args = parser.parse_args()

    qr_path = Path(args.qr).expanduser().resolve()
    client = UESTCLogin(username, password, load_session=not args.fresh)
    if args.fresh and client.session_file and client.session_file.exists():
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_path = client.session_file.with_name(
            f"{client.session_file.name}.bak-{timestamp}"
        )
        shutil.copy2(client.session_file, backup_path)
        try:
            os.chmod(backup_path, 0o600)
        except OSError:
            pass
        print(f"SESSION_BACKUP {backup_path}", flush=True)
    try:
        if args.method == "auto":
            client.login_with_multifactor(
                code_provider=lambda: getpass("请输入短信验证码（输入内容不会回显）: "),
                qr_path=qr_path,
                timeout=args.timeout,
                trust_device=args.trust_device,
                progress=progress,
            )
        elif args.method == "sms":
            client.login_with_sms(
                code_provider=lambda: getpass("请输入短信验证码（输入内容不会回显）: "),
                trust_device=args.trust_device,
                progress=progress,
            )
        else:
            client.login_with_qr(
                qr_path=qr_path,
                timeout=args.timeout,
                trust_device=args.trust_device,
                progress=progress,
            )
        info = client.query_room_info()
        print(
            json.dumps(
                {
                    "success": True,
                    "room_name": info["room_name"],
                    "remaining_amount": info["remaining_amount"],
                    "session_file": str(client.session_file)
                    if client.session_file
                    else None,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
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
    finally:
        if not args.keep_qr:
            qr_path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
