# roomInfo_check — UESTC 宿舍电费监控

[![docker](https://github.com/kohakunamori/roomInfo_check/actions/workflows/docker.yml/badge.svg)](https://github.com/kohakunamori/roomInfo_check/actions/workflows/docker.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

监控电子科技大学「云中成电」绑定寝室的剩余电费：Web 仪表盘 + 历史曲线 + 低余额邮件告警。  
**校园登录 / MFA 全程在网页完成**——部署后点「刷新登录」即可。

## 5 分钟部署

前置：Docker + Docker Compose v2（Linux / NAS / 本机均可）。

```bash
git clone https://github.com/kohakunamori/roomInfo_check.git
cd roomInfo_check

cp .env.example .env
# 编辑 .env：把 WEB_AUTH_TOKEN 改成一串随机长密码
mkdir -p data

# 默认从 GHCR 拉预构建镜像（免本地 build）
docker compose up -d
```

打开 **http://127.0.0.1:3032** → 用 `WEB_AUTH_TOKEN` 登录。

### 首次校园登录（必须）

1. 进入 **认证接管**（侧栏）或点顶部 **刷新登录**
2. 等待认证容器启动（约几秒），页内出现 noVNC 画面
3. 在画面里完成统一身份认证 / MFA
4. 成功后会话写入 `data/`，认证容器自动退出

之后日常只需浏览器操作：改邮件设置、立即查询、会话过期时再点一次「刷新登录」。

### 配置告警（可选，也可网页里改）

在仪表盘 **设置** 中填写：发信邮箱 / SMTP 授权码 / 收件人 / 房间 ID / 阈值 → **发送测试邮件** → **立即查询**。

## 架构（三容器）

| 服务 | 作用 | 默认 |
|---|---|---|
| `roominfo` | 仪表盘 + 定时查询 + 历史库 | 常驻，`127.0.0.1:3032` |
| `roominfo-auth` | 有头浏览器 + noVNC（登录用） | **按需启动**，`127.0.0.1:3033` |
| `roominfo-auth-ctl` | 监听网页「刷新登录」按钮，启停 auth | 常驻极轻量 |

`docker.sock` **只**挂在 ctl 上，主容器拿不到。

## 常用命令

```bash
docker compose ps
docker compose logs -f roominfo
docker compose pull && docker compose up -d     # 更新镜像
docker compose down
```

本地开发要强制构建时：

```bash
ROOMINFO_IMAGE=roominfo:latest ROOMINFO_AUTH_IMAGE=roominfo-auth:latest \
  docker compose up -d --build
```

## 公网 / 远程访问

默认端口只绑本机 loopback。远程请加 **HTTPS 反向代理**：

| 路径 | 转发到 |
|---|---|
| `/` | `127.0.0.1:3032` |
| `/vnc/` | `127.0.0.1:3033/`（需 WebSocket） |

nginx 最小示例：

```nginx
location / {
    proxy_pass http://127.0.0.1:3032;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
}
location /vnc/ {
    proxy_pass http://127.0.0.1:3033/;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 3600s;
}
```

`.env` 中把 `WEB_PUBLIC_URL` 改成你的公网地址。`AUTH_NOVNC_URL` **留空即可**（远程浏览器会自动走同源 `/vnc/`）。

> 本机打开 `http://127.0.0.1:3032` 时，前端会直连 `http://127.0.0.1:3033` 的 noVNC，**无需反代**也能完成登录。

## 必改 / 常用配置

| 变量 | 默认 | 说明 |
|---|---|---|
| `WEB_AUTH_TOKEN` | change-me… | **必改**，仪表盘访问令牌 |
| `WEB_BIND` / `VNC_BIND` | 127.0.0.1:3032 / :3033 | 宿主机绑定 |
| `WEB_PUBLIC_URL` | http://127.0.0.1:3032/ | 邮件里展示的地址 |
| `AUTH_NOVNC_URL` | （空） | 留空自动选择；仅跨域时才填 |
| `CHECK_INTERVAL_MINUTES` | 60 | 查询间隔（网页可改） |
| `LOW_BALANCE_THRESHOLD` | 20 | 低余额阈值（网页可改） |

完整变量见 [docs/configuration.md](docs/configuration.md)。

## 安全要点

- `.env` 与 `data/`（会话 Cookie、SMTP 授权码）**不要提交 Git**
- 公网务必 HTTPS 反代 + 强 `WEB_AUTH_TOKEN`
- 服务器主容器保持 `ONLINE_BROWSER_REFRESH=false`（compose 已强制）

详见 [SECURITY.md](SECURITY.md)。

## 更多文档

| 文档 | 内容 |
|---|---|
| [docs/deployment.md](docs/deployment.md) | 部署细节 / 更新 / 排障 |
| [docs/configuration.md](docs/configuration.md) | 全部环境变量 |
| [docs/architecture.md](docs/architecture.md) | 模块与数据流 |
| [DOCKER.md](DOCKER.md) | Compose 命令速查 |
| [SECURITY.md](SECURITY.md) | 密钥与会话安全 |

## 致谢

Fork 自 [alargi/roomInfo_check](https://github.com/alargi/roomInfo_check)，感谢原作者。

## 许可证

MIT — 见 [LICENSE](LICENSE)。
