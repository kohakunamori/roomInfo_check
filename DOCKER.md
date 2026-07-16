# Docker 速查

完整流程见 [README.md](README.md) 与 [docs/deployment.md](docs/deployment.md)。

## 服务

| 服务 | 默认端口 | 说明 |
|------|----------|------|
| `roominfo` | `127.0.0.1:3032→8080` | 仪表盘 + 历史 + 定时查询 |
| `roominfo-auth` | `127.0.0.1:3033→6080` | 按需 noVNC 登录（profile `auth`，用完退出） |
| `roominfo-auth-ctl` | — | 轮询触发文件，启停 auth |

默认镜像：

- `ghcr.io/kohakunamori/roominfo:latest`（amd64 + arm64）
- `ghcr.io/kohakunamori/roominfo-auth:latest`（amd64）

## 最小步骤

```bash
cp .env.example .env          # 改 WEB_AUTH_TOKEN
mkdir -p data
docker compose up -d          # 拉 GHCR 镜像并启动
# 浏览器 http://127.0.0.1:3032 → 登录 → 刷新登录 → MFA
```

## 常用命令

```bash
docker compose ps
docker compose logs -f roominfo
docker compose logs -f roominfo-auth-ctl
docker compose --profile auth logs -f roominfo-auth

docker compose pull && docker compose up -d   # 更新
docker compose restart roominfo
docker compose down

# 手工调试 auth（一般用网页按钮即可）
docker compose --profile auth up -d roominfo-auth
docker compose --profile auth stop roominfo-auth

# 探针
docker compose exec roominfo python getInfo.py
docker compose exec roominfo python emailSend.py
```

## 本地强制构建

```bash
ROOMINFO_IMAGE=roominfo:latest \
ROOMINFO_AUTH_IMAGE=roominfo-auth:latest \
  docker compose up -d --build
```

## 文件分工

| 路径 | 内容 |
|------|------|
| `.env` | 令牌 / SMTP / 绑定端口 |
| `data.json` | `[{ "room_id", "email" }]` |
| `data/.uestc_session.json` | 门户 Cookie（网页登录导出） |
| `data/history.db` | SQLite 采样历史 |
| `data/settings.json` | 网页运行时设置（授权码不回显） |
| `data/auth_status.json` | 刷凭据状态 |

## noVNC 怎么访问

| 场景 | URL |
|------|-----|
| 本机打开仪表盘 | 前端自动用 `http://127.0.0.1:3033/vnc.html?…` |
| 经反向代理 | 同源 `/vnc/vnc.html?…&path=vnc/websockify`（nginx 把 `/vnc/` 转到 3033） |
| 自定义 | 设 `AUTH_NOVNC_URL` 绝对地址 |

## 镜像 target

| target | 用途 |
|--------|------|
| `runtime` | Python + Flask + monitor，无浏览器 |
| `auth` | Chromium + Xvfb + noVNC + Playwright |

## 故障：noVNC 只有黑屏 + 光标

常见原因：共享 profile `data/.uestc_chrome_profile` 里残留上次容器的 Chromium **Singleton*** 锁（`SingletonLock` / `SingletonCookie` / `SingletonSocket`）。新容器 hostname 不同，Chromium 立刻退出，noVNC 只剩 Xvfb 黑屏，约 `AUTH_HOLD_SECONDS` 后容器自动停。

镜像内 `docker/auth-entrypoint.sh`（及 `browser_session`）会在启动前 / 退出时自动清理这些锁，**不会**删除整个 profile。若仍异常，可手动：

```bash
rm -f data/.uestc_chrome_profile/Singleton*
docker compose --profile auth up -d roominfo-auth
docker compose --profile auth logs -f roominfo-auth
# 日志应出现：chromium profile dir=... 且 auth_worker 进入 running / waiting_mfa
```
