# 架构说明

## 业务目标

对电子科技大学「云中成电」当前登录账号**已绑定**的寝室，周期查询剩余电费；将采样写入本地历史；当金额 ≤ 阈值时，向订阅邮箱发送提醒。可选 Web 仪表盘与容器内刷凭据。

## 运行时拓扑

```text
┌─ roominfo (runtime image) ──────────────────────────────┐
│  webapp.py  Flask :8080                                 │
│    ├─ Token 登录 / 仪表盘 / Chart.js                    │
│    ├─ /api/status|latest|history|events|query           │
│    └─ /api/auth/start|stop|status                       │
│  monitor.py  schedule 线程                               │
│    └─ getInfo.UESTCLogin → /site/bedroom                │
│         ├─ history.py (SQLite samples + events)         │
│         └─ emailSend (阈值告警)                         │
│  ONLINE_BROWSER_REFRESH=false  （Plan A）               │
└───────────────────────┬─────────────────────────────────┘
                        │ volume: ./data
                        │  session · history.db · auth_status
┌───────────────────────▼─────────────────────────────────┐
│  roominfo-auth (auth image, profile)                    │
│  Xvfb :99 · x11vnc · websockify/noVNC :6080             │
│  auth_worker.py → bootstrap_headful_playwright          │
│  install_browser_auth → data/.uestc_session.json        │
└─────────────────────────────────────────────────────────┘
```

## 数据流（查询）

```text
schedule / POST /api/query
    │
    ▼
MonitorService.query_once
    │  恢复 ONLINE_SESSION_FILE Cookie
    │  （主容器不启用浏览器刷新）
    ▼
GET https://online.uestc.edu.cn/site/bedroom
    │  e=0, d={ fjh, syje, sydl }
    ▼
HistoryStore.add_sample  (+ 可选 recharge 事件)
    │
    ├─ 高于阈值 → 日志
    └─ 低于阈值 → SMTP 邮件 + alert 事件
```

## 门户接口

| 项 | 值 |
|----|-----|
| 方法 | `GET` |
| URL | `https://online.uestc.edu.cn/site/bedroom` |
| 鉴权 | 门户 Cookie；部分场景附加 `token` 头 |
| 成功 | `{ "e": 0, "d": { "fjh", "syje", "sydl" } }` |

旧地址 `eportal.uestc.edu.cn/.../queryRoomInfo.do` **已废弃**。

当前接口**不接受任意房间号**，只返回账号绑定寝室。

## 鉴权优先级（login）

1. 已保存的 session 文件 / Cookie 且 `/site/bedroom` 验证通过  
2. 本机 / auth 容器 `ONLINE_BROWSER_REFRESH=true` 时，用 Playwright state 刷新  
3. 账号密码 CAS 登录（`encrypt.js` + 可选 MFA 引导）  

**主服务 Plan A 关闭第 2 步**，避免在无 GUI / 高风险 IP 上进入 MFA 死循环。

## 为何要 Plan A + 可选 auth 容器

实测：纯 `requests` 走完微信/短信后，IDAS 风险模型仍可能再次要求 MFA。  
真实 Chromium 完成“信任此浏览器”后导出的 Cookie，在服务器上可稳定查询一段时间。

| 角色 | 职责 |
|------|------|
| 主容器 `roominfo` | 只读 session、定时查询、历史、邮件、仪表盘 |
| 按需 `roominfo-auth` | noVNC 完成 MFA，写回共享 session |
| 本机工具 | 仍可用 `bootstrap_browser` / `refresh_credentials` |

## 组件职责

| 模块 | 职责 |
|------|------|
| `webapp.py` / `main.py` | Flask 仪表盘 + 调度线程入口 |
| `monitor.py` | 查询、写历史、发邮件 |
| `history.py` | SQLite samples / events |
| `auth_control.py` | 刷凭据状态与 compose 启停（可选） |
| `auth_worker.py` | auth 容器内有头登录 worker |
| `getInfo.py` | 登录态、查询、session 持久化 |
| `emailSend.py` | SMTP 发送（默认 465 SSL） |
| `env.py` | 环境 / `.env` 配置 |
| `browser_session.py` | CDP / Playwright / headful 会话 |
| `bootstrap_browser.py` | 本机可见浏览器首次引导 |
| `refresh_credentials.py` | 本机自动刷新并导出 |
| `index.py` | 兼容入口 → `webapp` |

## Web 鉴权

- `WEB_AUTH_TOKEN` 非空：登录页或 `Authorization: Bearer <token>`  
- 空 token：仅建议内网调试（仪表盘会提示）  
- Compose 默认绑定 `127.0.0.1:3032`（dashboard）与 `127.0.0.1:3033`（noVNC）

## 限制与风险

- 学校可随时吊销会话或再次要求 MFA  
- Session 泄露 = 账号门户权限泄露  
- 邮件依赖第三方 SMTP 可达性  
- noVNC 暴露面等同于远程桌面，务必 loopback / 短时 / 隧道  

## 扩展点

- 多收件人：在 `data.json` 增加数组元素  
- 改阈值 / 间隔：改 `.env` 后重启容器  
- `AUTH_MODE=docker` + 挂载 docker.sock：可从仪表盘直接起 auth（进阶，注意安全）  
