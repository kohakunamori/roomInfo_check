# 配置参考

部署步骤见 [deployment.md](deployment.md)，整体设计见 [architecture.md](architecture.md)。本文只讲"有哪些配置、默认值是什么、写在哪里"。

## 1. 配置来源与优先级

同一项配置可能有三个来源，从高到低：

1. **网页设置** `data/settings.json` — 仪表盘「邮件设置」抽屉保存（`PUT /api/settings`），无需重启，文件权限 600
2. **环境变量 / `.env`** — compose 通过 `env_file` 注入；`.env` 常以只读方式挂载
3. **代码默认值** — `env.py` 中的兜底值

覆盖规则（`settings_store.py`）：`settings.json` 中某键**非空**才覆盖环境变量；删掉或置空即回退 `.env`。

**网页（或 API）可改的键**，其余一律只能改 `.env` 后重启：

| settings.json 键 | 对应环境变量 | 说明 |
|---|---|---|
| `smtp_from` | `SMTP_FROM` | 发信邮箱 |
| `smtp_from_name` | `SMTP_FROM_NAME` | 发件显示名 |
| `smtp_server` / `smtp_port` / `smtp_use_ssl` | `SMTP_SERVER` / `SMTP_PORT` / `SMTP_USE_SSL` | SMTP 服务器 |
| `smtp_auth_code` | `Authorization_code` | SMTP 授权码；保存后 API 永不回显明文，只返回"已配置" |
| `low_balance_threshold` | `LOW_BALANCE_THRESHOLD` | 告警阈值（元） |
| `check_interval_minutes` | `CHECK_INTERVAL_MINUTES` | 检查间隔（分钟，最小 1），保存后**热生效** |
| `session_alert_cooldown_hours` | `SESSION_ALERT_COOLDOWN_HOURS` | 会话失效邮件冷却（小时）；仅 API 可改，抽屉未暴露 |
| `recipients` | —（收件人在 `data.json`） | 收件人列表，逗号/换行分隔；保存时同步写回 `data.json` |
| `room_id` | — | 房间 ID；`settings.json` 优先于 `data.json` 首项 |

## 2. 环境变量全量表

默认值以 `env.py` 为准；「compose 覆盖」指 `docker-compose.yml` 对容器强制注入的值，改 `.env` 无效。

### 账号

| 变量 | 默认 | 说明 |
|---|---|---|
| `API_USERNAME` | 空 | 统一身份认证学号。Plan A（会话 Cookie 常驻）可留空 |
| `API_PASSWORD` | 空 | 统一身份认证密码。仅 auth 容器自动填表 / 密码兜底登录时用 |

### SMTP 邮件（网页可覆盖）

| 变量 | 默认 | 说明 |
|---|---|---|
| `Authorization_code` | 空 | SMTP **授权码**（不是邮箱登录密码） |
| `SMTP_SERVER` | `smtp.163.com` | SMTP 主机 |
| `SMTP_PORT` | `465` | 端口；465 配 SSL |
| `SMTP_FROM` | 空 | 发信邮箱，须与授权码账号一致 |
| `SMTP_FROM_NAME` | `roominfo` | 发件显示名 |
| `SMTP_USE_SSL` | `true` | SSL 直连（465 推荐）；`false` 走 587/STARTTLS 场景 |

### 监控参数（阈值/间隔/冷却网页可覆盖）

| 变量 | 默认 | 说明 |
|---|---|---|
| `DATA_FILE` | `data.json` | 订阅列表路径（房间 ID + 收件人）；compose 覆盖为 `./data.json` |
| `CHECK_INTERVAL_MINUTES` | `60` | 定时查询间隔（分钟，最小 1） |
| `LOW_BALANCE_THRESHOLD` | `20.0` | 余额 ≤ 该值（元）时发告警邮件 |
| `SESSION_ALERT_COOLDOWN_HOURS` | `12.0` | 会话失效通知邮件的最小间隔（小时），防轰炸 |

### 历史 / Web

| 变量 | 默认 | 说明 |
|---|---|---|
| `HISTORY_DB` | `./data/history.db` | SQLite 路径（采样 + 事件日志） |
| `HISTORY_RETAIN_DAYS` | `90` | 采样保留天数；≤0 不清理 |
| `HISTORY_RECHARGE_DELTA` | `1.0` | 相邻采样余额上升 ≥ 该值（元）记一条 `recharge` 事件 |
| `WEB_HOST` | `0.0.0.0` | Flask 监听地址（容器内保持 0.0.0.0，compose 已强制） |
| `WEB_PORT` | `8080` | Flask 端口（容器内，compose 已强制） |
| `WEB_AUTH_TOKEN` | 空 | 仪表盘访问令牌（登录页表单 / `Authorization: Bearer`）。**空 = 免认证，生产必设强随机长串** |
| `FLASK_SECRET_KEY` | 空（进程内随机） | Flask session 签名密钥；固定后重启不掉登录态 |
| `WEB_PUBLIC_URL` | `http://127.0.0.1:3032/` | 邮件与提示中展示的仪表盘外链；有域名/反代改成公网 URL |
| `SETTINGS_FILE` | `./data/settings.json` | 网页运行时设置文件路径 |
| `TZ` | `Asia/Shanghai`（compose 默认） | 容器时区 |

### Docker 绑定 / 镜像（compose 变量，非应用配置）

| 变量 | 默认 | 说明 |
|---|---|---|
| `WEB_BIND` | `127.0.0.1:3032` | 仪表盘宿主机绑定；默认仅 loopback，远程访问走反代或改 `0.0.0.0:3032` |
| `VNC_BIND` | `127.0.0.1:3033` | noVNC 宿主机绑定，同上 |
| `VNC_PUBLIC_PORT` | `3033` | 与 `VNC_BIND` 左侧端口一致；本机打开仪表盘时前端直连该端口的 noVNC |
| `ROOMINFO_IMAGE` | `ghcr.io/kohakunamori/roominfo:latest` | 主镜像（compose 默认拉 GHCR）；本地开发可设 `roominfo:latest` 并 `--build` |
| `ROOMINFO_AUTH_IMAGE` | `ghcr.io/kohakunamori/roominfo-auth:latest` | auth 镜像（仅 amd64 预构建） |

### auth 生命周期（按需启停浏览器登录）

| 变量 | 默认 | 说明 |
|---|---|---|
| `AUTH_MODE` | `manual` | `manual`：仪表盘写触发文件，由 `roominfo-auth-ctl` sidecar 启停 auth 容器（主容器不挂 docker.sock）；`docker`：主容器内直接调 `docker compose`（需自行挂载 docker.sock，进阶） |
| `AUTH_SERVICE_NAME` | `roominfo-auth` | compose 服务名 |
| `AUTH_COMPOSE_PROJECT` | 空 | 可选 compose project 名（`docker compose -p`） |
| `AUTH_NOVNC_URL` | 空 | 空 = 前端自动选择：本机浏览器 → `http://host:VNC_PUBLIC_PORT/vnc.html`；远程浏览器 → 同源 `/vnc/…`（需反代）。仅当 noVNC 与仪表盘不同源时填绝对地址 |
| `AUTH_STATUS_FILE` | `./data/auth_status.json` | 状态 JSON 路径（不含密钥） |
| `AUTH_TIMEOUT_SECONDS` | `900` | 有头登录总超时（秒）；超过 `timeout+hold+60` 秒 sidecar 强制停容器 |
| `AUTH_HOLD_SECONDS` | `20` | 登录成功/失败后 auth 容器再停留秒数，随后自动退出释放资源 |
| `AUTH_DATA_DIR` | `./data`（sidecar 内 `/project/data`） | 触发文件所在目录（共享卷） |
| `AUTH_LIFECYCLE_POLL_SECONDS` | `2`（compose 设定） | sidecar 轮询触发文件的间隔（秒） |
| `AUTH_PROJECT_ROOT` | 脚本自动推断（sidecar 内 `/project`） | `auth-lifecycle.sh` 的项目根 |
| `AUTH_XVFB_GEOMETRY` | `1600x900x24` | auth 容器虚拟屏幕分辨率 |
| `AUTH_VNC_GEOMETRY` | `1600x900` | x11vnc 几何参数，需与上项一致 |

不要设置 `AUTH_KEEP_VNC`（compose 注释已强调）：闲置 Chromium 白白吃内存，auth 容器设计为用完即退。

### 门户会话（`ONLINE_*`）

| 变量 | 默认 | 说明 |
|---|---|---|
| `ONLINE_SESSION_FILE` | `.uestc_session.json` | 门户 Cookie 会话文件；compose 强制 `./data/.uestc_session.json`（主/auth 容器共享） |
| `ONLINE_BROWSER_REFRESH` | `true` | 会话失效时是否本机起浏览器刷新。**服务器主容器必须 `false`**（compose 已对 roominfo 强制 false、对 roominfo-auth 强制 true） |
| `ONLINE_REMEMBER_ME` | `true` | 登录时勾选"7 天免登录"，延长会话寿命 |
| `ONLINE_TOKEN` | 空 | 可选：手工导入门户 localStorage token |
| `ONLINE_COOKIE` | 空 | 可选：手工导入门户站点 Cookie 串 |
| `IDAS_COOKIE` | 空 | 可选：手工导入统一身份认证（CAS）Cookie |
| `ONLINE_USER_AGENT` | Chrome 138 桌面 UA | 查询请求的 UA |
| `ONLINE_ACCEPT_LANGUAGE` | `zh-CN,zh;q=0.9,en;q=0.8` | 请求语言头 |
| `ONLINE_BROWSER_STATE_FILE` | `.uestc_browser_state.json` | Playwright storage state；compose 对 auth 容器设为 `./data/.uestc_browser_state.json` |
| `ONLINE_BROWSER_PROFILE_DIR` | `.uestc_chrome_profile` | 独立浏览器 profile 目录；compose 对 auth 容器设为 `./data/.uestc_chrome_profile` |
| `ONLINE_BROWSER_EXECUTABLE` | 空（自动探测） | 浏览器可执行文件；auth 容器 compose 强制 `/usr/bin/chromium` |
| `ONLINE_BROWSER_TIMEOUT` | `90` | 浏览器刷新超时（秒） |

## 3. 触发文件机制（`AUTH_MODE=manual`）

主容器不挂 docker.sock，通过共享的 `data/` 目录与 `roominfo-auth-ctl` sidecar（`docker/auth-lifecycle.sh`，每 2s 轮询）协作：

| 文件 | 谁写 | 谁读/删 | 格式 | 效果 |
|---|---|---|---|---|
| `data/.auth_start_request` | roominfo（菜单「刷新登录」→ `POST /api/auth/start`） | sidecar 读到后执行 `docker compose --profile auth up -d roominfo-auth` 并删除 | 单行 UTC ISO 时间戳 | 启动 auth 容器；写入时会顺手清掉未消费的 stop 请求 |
| `data/.auth_stop_request` | roominfo（菜单「结束刷新」→ `POST /api/auth/stop`） | sidecar 读到后 `compose stop` + `docker rm -f` 并删除 | 单行 UTC ISO 时间戳 | 强制停止 auth 容器；写入时清掉未消费的 start 请求 |
| `data/auth_status.json` | roominfo（`auth_control.write_status`）与 auth 容器（`auth_worker.py`）都会写 | roominfo（`GET /api/auth/status`、仪表盘轮询）；sidecar 读 `state` / `started_at` 判断终态延迟强停与总超时强停 | JSON：`state` / `message` / `novnc_url` / `updated_at` / `started_at` / `deadline_ts` / `error` 等 | 状态机：`idle` → `starting` → `running` / `waiting_mfa` →（`success` \| `failed`）；异常路径 `waiting_host`。读取时 `novnc_url` 总被当前环境变量覆盖，不信任文件里的旧值 |

sidecar 兜底逻辑：容器仍在跑但状态已是 `success`/`failed`/`idle` 超过 `AUTH_HOLD_SECONDS` → 强停；`started_at` 距今超过 `AUTH_TIMEOUT_SECONDS + AUTH_HOLD_SECONDS + 60` → 强停。

不想给任何容器挂 docker.sock？删掉 `roominfo-auth-ctl` 服务，需要刷新登录时在宿主机手动执行：

```bash
docker compose --profile auth up -d roominfo-auth
```

## 4. `data/` 目录文件清单

`data/` 含真实会话凭据，已在 `.gitignore` 中，**不要提交、不要打进镜像**。

| 文件 | 敏感度 | 说明 |
|---|---|---|
| `settings.json` | 高（含 SMTP 授权码） | 网页运行时设置，权限 600，API 永不回显授权码明文 |
| `history.db` | 中（余额曲线） | SQLite：`samples` 采样表 + `events` 事件日志表 |
| `.uestc_session.json` | **高（等同登录凭据）** | 门户 Cookie 会话，auth 容器登录成功后导出，主容器据此查询 |
| `.uestc_browser_state.json` | 高 | Playwright storage state（auth 容器写） |
| `.uestc_chrome_profile/` | 高 | auth 容器 Chromium 独立 profile |
| `auth_status.json` | 低（无密钥） | auth 流程状态机，见上表 |
| `.auth_start_request` / `.auth_stop_request` | 低 | 瞬态触发文件，被 sidecar 消费后删除 |

另有仓库根的 `data.json`（订阅列表：`room_id` + `email`），由邮件设置抽屉自动同步维护，一般无需手改。

## 5. 日志事件类型

事件写入 `history.db` 的 `events` 表，仪表盘「日志」抽屉（`GET /api/events`）分页展示。全集（核对自 `monitor.py` / `webapp.py` / `history.py`）：

| 类型 | 来源 | 含义 |
|---|---|---|
| `query_start` | monitor | 一轮查询开始（note 含 `source=monitor/web/startup`） |
| `query_done` | monitor | 一轮查询结束（汇总 ok/样本数/错误数/当前阈值） |
| `query_failed` | monitor | 登录失败、会话检查异常或单房间查询失败 |
| `check_ok` | monitor | 查询成功且余额未达阈值 |
| `alert` | monitor | 低余额触发告警（`amount_to` 为当前余额） |
| `recharge` | history（自动） | 相邻采样余额上升 ≥ `HISTORY_RECHARGE_DELTA`，判定为充值 |
| `email_sent` | monitor | 邮件发送成功（告警/会话失效/测试邮件共用） |
| `email_failed` | monitor | 邮件发送失败/异常，或测试邮件无收件人、SMTP 授权码未配置 |
| `email_test` | monitor | 测试邮件路径的事件类型（实际发送结果记为 `email_sent` / `email_failed`） |
| `session_invalid` | monitor | 门户会话失效（含"冷却中""无收件人""已通知 n 人"等 note） |
| `settings_updated` | webapp | `PUT /api/settings` 保存成功（note 含阈值/收件人数/SMTP 概要，无密钥） |
| `settings_failed` | webapp | 设置保存失败 |
| `auth_start` | webapp | `POST /api/auth/start`（刷新登录）已请求 |
| `auth_stop` | webapp | `POST /api/auth/stop`（结束刷新）已请求 |
| `startup_failed` | webapp | 启动时的首次查询失败 |

## 6. 权限建议

```bash
chmod 600 .env
chmod 700 data
```

`data/settings.json` 与 `data/.uestc_session.json` 由程序按 600 写出；`.env` 与 `data/` 均不入库。
