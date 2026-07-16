# 部署指南

日常第三方部署请先看仓库根目录 [README.md](../README.md) 的「5 分钟部署」。本文补充细节与排障。

## 方式怎么选

| 方式 | 适用 | 说明 |
|------|------|------|
| **Docker Compose** | NAS / 任意 Linux | **推荐**；默认拉 GHCR 预构建镜像 |
| deploy.sh + systemd | 无 Docker 的 Linux | `package_release` 生成的 `server-plan-a` |
| `python main.py` | 开发调试 | 需自行保证 session 有效 |

主服务一律 **Plan A**：只挂载会话文件，`ONLINE_BROWSER_REFRESH=false`（compose 已强制）。

---

## Docker Compose（推荐）

### 1. 准备

```bash
git clone https://github.com/kohakunamori/roomInfo_check.git
cd roomInfo_check
cp .env.example .env
# 必改 WEB_AUTH_TOKEN
mkdir -p data
chmod 600 .env
```

可选：复制 `data.json.example` → `data.json` 预填房间 ID / 收件人（之后也可在网页改）。

### 2. 启动

```bash
docker compose up -d
docker compose ps
docker compose logs -f --tail=50 roominfo
```

默认从 GHCR 拉镜像：

- `ghcr.io/kohakunamori/roominfo:latest`
- `ghcr.io/kohakunamori/roominfo-auth:latest`（点「刷新登录」时才拉/起）

本地构建：

```bash
ROOMINFO_IMAGE=roominfo:latest ROOMINFO_AUTH_IMAGE=roominfo-auth:latest \
  docker compose up -d --build
```

### 3. 网页完成校园登录

1. 打开 `http://127.0.0.1:3032`，用 `WEB_AUTH_TOKEN` 登录
2. 侧栏 **认证接管** 或顶部 **刷新登录**
3. `roominfo` 写 `data/.auth_start_request` → `roominfo-auth-ctl` 在约 2s 内执行  
   `docker compose --profile auth up -d roominfo-auth`
4. 页内 noVNC 完成 MFA；成功后会话写入 `data/.uestc_session.json`，auth 容器退出

**本机访问**：前端直连 `http://127.0.0.1:3033`（无需反代）。  
**远程访问**：见下方反代；前端改用同源 `/vnc/`。

### 4. 配置邮件与房间

仪表盘 **设置**：SMTP / 授权码 / 收件人 / 房间 ID / 阈值 / 间隔 → 测试邮件 → **立即查询**。

### 5. 更新

```bash
git pull
docker compose pull
docker compose up -d
```

---

## 反向代理

两个端口默认只绑 `127.0.0.1`。公网务必 HTTPS：

```nginx
server {
    listen 443 ssl;
    server_name your.domain.example;
    # ssl_certificate / ssl_certificate_key ...

    location / {
        proxy_pass http://127.0.0.1:3032;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /vnc/ {
        proxy_pass http://127.0.0.1:3033/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_read_timeout 3600s;
    }
}
```

`.env`：`WEB_PUBLIC_URL=https://your.domain.example/`，`AUTH_NOVNC_URL` 留空。

SSH 隧道临时访问：

```bash
ssh -L 3032:127.0.0.1:3032 -L 3033:127.0.0.1:3033 user@nas
```

---

## 目录布局

```text
roomInfo_check/
├── docker-compose.yml
├── Dockerfile
├── docker/auth-lifecycle.sh
├── .env
├── data.json
└── data/
    ├── .uestc_session.json
    ├── history.db
    ├── settings.json
    └── auth_status.json
```

NAS 示例：`/vol1/1000/appdata/roominfo`。

---

## 故障排查

| 现象 | 排查 |
|------|------|
| 仪表盘 401 | `WEB_AUTH_TOKEN` 是否与登录输入一致 |
| 点刷新登录无反应 | `docker compose logs roominfo-auth-ctl`；确认 docker.sock 挂载 |
| noVNC 空白 / 连不上 | 本机：确认 3033 已映射；远程：反代 `/vnc/` + WebSocket；看 `roominfo-auth` 日志 |
| noVNC 只有黑屏光标，约 20s 后容器退出 | 共享 profile 残留 `Singleton*` 锁（容器异常 stop 后常见）。新镜像会自动清理；也可 `rm -f data/.uestc_chrome_profile/Singleton*` 后重开 auth |
| 查询失败 / session 无效 | 再点一次「刷新登录」完成 MFA |
| 邮件失败 | 465 + SSL、授权码、发件人一致 |
| auth 写不了 session | `./data` 可写，主/auth 挂同一目录 |
| 拉不到镜像 | 确认能访问 `ghcr.io`；或改本地 build |

**切勿**在日志或聊天中粘贴 Cookie、密码、Token 明文。
