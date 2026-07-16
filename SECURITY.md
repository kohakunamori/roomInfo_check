# 安全说明

## 切勿提交的内容

| 类型 | 示例路径 / 变量 |
|------|-----------------|
| 账号密码 | `API_USERNAME` / `API_PASSWORD` |
| SMTP 授权码 | `Authorization_code` |
| Web 访问令牌 | `WEB_AUTH_TOKEN`、`FLASK_SECRET_KEY` |
| 门户会话 | `.uestc_session.json`、`data/.uestc_session.json` |
| 浏览器态 | `.uestc_browser_state.json`、`.uestc_chrome_profile/` |
| 导出包 | `deploy_bundle/`、含 session 的 `release/` |
| 历史库 | `data/history.db`（余额时间序列） |
| 抓包库 | `*.db`、mitm 流量文件 |

仓库已通过 `.gitignore` / `.dockerignore` 排除上述路径。若曾误提交：

1. 立即**轮换**统一身份认证密码、邮箱授权码与 `WEB_AUTH_TOKEN`  
2. 从 Git 历史中移除密钥（`git filter-repo` 等）  
3. 重新 `bootstrap_browser` / auth 容器并替换服务器 session  

## 文件权限建议

```bash
chmod 600 .env data/.uestc_session.json
chmod 700 data
```

## 服务器加固

- 主服务使用 Plan A：`ONLINE_BROWSER_REFRESH=false`  
- 不要把 session 打进镜像层；只通过 volume / bind mount 挂载  
- 生产设置强随机 `WEB_AUTH_TOKEN`；勿留空  
- Web / noVNC 默认只绑定 `127.0.0.1`，经反代或 SSH 隧道暴露  
- noVNC 等同远程桌面：仅在刷新凭据时短时开启  
- 日志中不要 `print` Cookie、密码、授权码、Token（`env.py` 自检只打印 bool）  
- `AUTH_MODE=docker` 时若挂载 `docker.sock`，视同 root 等价权限，需额外加固  

## 报告问题

若发现本仓库文档或示例中仍含可利用的真实凭据，请不要在公开 Issue 中粘贴完整密钥；先自行轮换，再通过私信或脱敏方式告知维护者。
