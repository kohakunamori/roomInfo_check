import smtplib
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr

from env import (
    Authorization_code,
    smtp_from_addr,
    smtp_from_name,
    smtp_port,
    smtp_server,
    smtp_use_ssl,
)


def send_email(
    to_addr,
    subject,
    content,
    smtp_server=None,
    smtp_port=None,
    from_addr=None,
    password=None,
    use_ssl=None,
    from_name=None,
):
    """发送邮件。SMTP 参数默认全部来自 .env / 环境变量。"""
    from env import (
        Authorization_code as env_auth,
        smtp_from_addr as env_from,
        smtp_from_name as env_from_name,
        smtp_port as env_port,
        smtp_server as env_server,
        smtp_use_ssl as env_ssl,
    )

    server_host = smtp_server or env_server
    port = int(smtp_port if smtp_port is not None else env_port)
    sender = from_addr or env_from
    display_name = from_name if from_name is not None else env_from_name
    auth = password if password is not None else env_auth
    ssl_mode = env_ssl if use_ssl is None else bool(use_ssl)

    if not sender:
        raise ValueError("SMTP from_addr is empty (set SMTP_FROM in .env)")
    if not auth:
        raise ValueError("SMTP Authorization_code is empty (set Authorization_code in .env)")
    if not to_addr:
        raise ValueError("SMTP to_addr is empty")

    message = MIMEText(content, "plain", "utf-8")
    message["From"] = formataddr((display_name or "roominfo", sender))
    message["To"] = formataddr(("", to_addr))
    message["Subject"] = Header(subject, "utf-8")

    server = None
    try:
        if ssl_mode or port == 465:
            server = smtplib.SMTP_SSL(server_host, port, timeout=30)
        else:
            server = smtplib.SMTP(server_host, port, timeout=30)
            server.ehlo()
            if port in {587, 25}:
                try:
                    server.starttls()
                    server.ehlo()
                except smtplib.SMTPException:
                    pass
        server.login(sender, auth)
        server.sendmail(sender, [to_addr], message.as_string())
        print("邮件发送成功")
        return True
    except Exception as e:
        print(f"邮件发送失败: {type(e).__name__}: {e}")
        return False
    finally:
        if server is not None:
            try:
                server.quit()
            except Exception:
                pass


if __name__ == "__main__":
    import json
    import sys
    from pathlib import Path

    from env import data_file

    to = None
    path = Path(data_file)
    if path.exists():
        try:
            subs = json.loads(path.read_text(encoding="utf-8"))
            if subs and subs[0].get("email"):
                to = subs[0]["email"]
        except Exception as exc:
            print(f"读取 {data_file} 失败: {exc}")
            sys.exit(1)

    if not to or to.endswith("@example.com"):
        print(
            "未配置有效收件人：请在 data.json 中设置 email，"
            "或：python -c \"from emailSend import send_email; send_email('you@example.com','test','body')\""
        )
        sys.exit(2)

    ok = send_email(
        to_addr=to,
        subject="邮箱发送功能测试",
        content="邮箱发送成功（配置文件 / Docker 路径）",
    )
    print("邮件测试结果:", "成功" if ok else "失败", "->", to)
    sys.exit(0 if ok else 1)
