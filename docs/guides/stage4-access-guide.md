# 阶段 4 访问与使用指南（测试环境）

- 日期：2026-09-28
- 适用：合并到 main 之前的独立测试部署。合并之后路径会变，见 §7。

## 1. 现在的部署

| 项目 | 位置 / 状态 |
|---|---|
| 代码 | 服务器 `~/L1/minerva-stage4`（`stage4-decision` 分支，独立的 Python 环境）；生产目录 `~/L1/MinervaQuantSystem` 未改动 |
| 数据 | 只读使用生产目录的行情数据；业务库在 `~/L1/MinervaQuantSystem/data/app/app.sqlite` |
| API 与网页 | systemd 用户服务 `quant-api`：`https://127.0.0.1:18443`，同时托管 PC 端（`/`）和手机网页（`/m/`）。8000 和 8443 端口被生产业务占用，所以没有使用 |
| 证书 | 自建 CA：`~/.config/minerva/tls/ca.crt`，服务器证书包含 IP `8.159.139.145` 和 `127.0.0.1` |
| 每日决策 | **临时**定时器 `quant-decision-stage4`：工作日 19:37 运行，22:27 补跑，在 18:17 的采集之后 |
| 模拟账户 | `paper1`，初始资金 1,000 万元，从 2026-09-28 开始 |
| 管理员 | 用户名 `admin`，临时密码在服务器文件 `~/.config/minerva/admin-initial-password.txt`（权限 600） |

## 2. 需要你做：配置 frp

在服务器的 frpc 配置里加一个 TCP 转发。远程端口可以自己选，下面以 20443 为例：

```toml
[[proxies]]
name = "minerva-web"
type = "tcp"
localIP = "127.0.0.1"
localPort = 18443
remotePort = 20443
```

然后在阿里云安全组放行 TCP 20443，并重载 frpc。

- **只做 TCP 转发**，不要选 https 或 http 类型：TLS 由我们的服务终止，阿里云上的中转机看不到明文。
- 限制：经 frp 进来的连接，客户端 IP 都显示为 127.0.0.1。所以登录限流是全局的，审计日志里的 IP 也都是 127.0.0.1。

## 3. 电脑端

1. **安装根证书（一次）**：双击 `C:\Users\Administrator\Desktop\TEST\minerva_ca.crt` → "安装证书" → "当前用户" → "将所有的证书都放入下列存储" → "受信任的根证书颁发机构"。
   - 也可以在服务器上用 `scp -P 20224 van@8.159.139.145:.config/minerva/tls/ca.crt .` 取得证书。
   - 这是我们自建 CA 的公开证书，不含私钥。
2. **打开网页**：浏览器访问 `https://8.159.139.145:20443/`。
3. **首次登录**：用 `admin` 和临时密码登录，查看密码：`ssh ... 'cat ~/.config/minerva/admin-initial-password.txt'`。登录后会被要求修改密码。修改完成后，建议删掉这个文件。
4. **建账号**：在"系统管理 → 用户"里给其他人建账号。临时密码只显示一次，对方首次登录时必须修改。

## 4. 手机端

- **安卓 App**：安装 `C:\Users\Administrator\Desktop\TEST\minerva-app-debug.apk`（调试签名，需要允许"安装未知来源应用"）。
  - 打开后在登录页填写服务器地址 `https://8.159.139.145:20443`，然后输入用户名和密码。
  - App 内置了我们的 CA，手机上不需要另外装证书。
- **手机浏览器**：访问 `https://8.159.139.145:20443/m/`。这种方式要先在手机"设置 → 安全 → 加密与凭据 → 安装证书 → CA 证书"里安装 `minerva_ca.crt`。适用于不能装 APK 的手机。
- App 在前台时每分钟刷新一次通知。离线推送不在这一版的范围内。

## 5. 接下来几天会发生什么

| 时间 | 事件 |
|---|---|
| 09-28（周一）晚 | 18:17 采集 09-28 数据 → 19:37 决策作业：`paper1` 是监控日，只生成快照和持仓提示 |
| 09-29（周二）晚 | 同上 |
| **09-30（周三）晚** | **月末调仓日**：生成第一批交易清单（约 100 笔买入） |
| 10-01～10-07 | 国庆休市。工作日的定时器照常触发，但没有新交易日，G1 会记为阻断或跳过，这是预期行为 |
| **10-08 09:15 之前** | **在网页或 App 上审核 09-30 的清单**。只有 09:15 之前批准的意图，才会按 10-08 开盘价模拟成交 |
| 10-08 晚 | 采集后，决策作业先模拟 10-08 的成交，再做当天的监控 |

如果想提前看效果，可以在"每日决策 → 强制调仓"里对 `paper1` 立即建仓（需要填写原因，会记入审计）。

## 6. 日常检查

- **网页**："数据健康"页汇总了采集、每日任务、备份和决策的状态。
- **服务器命令**：
  - `systemctl --user list-timers`
  - `journalctl --user -u quant-decision-stage4 -n 50`
  - `~/L1/minerva-stage4/.venv/bin/quant-decision --root ~/L1/MinervaQuantSystem show`

## 7. 合并到 main 之后（需要你确认后再做）

1. 合并 `stage4-decision` 到 main（快进合并），由你推送到 GitHub。
2. 在生产目录更新：
   - `git pull`；
   - `uv pip install -r requirements-lock.txt` 和 `uv pip install --no-deps -e .`；
   - 把前端构建产物放到 `web/apps/*/dist`；
   - 运行 `deploy/install_api_service.sh`（数据根就是生产目录本身）。
3. 删除临时定时器 `quant-decision-stage4` 和目录 `~/L1/minerva-stage4`。之后由 `daily_update.sh` 按顺序执行"采集 → 模拟成交与决策 → 备份"；备份会包含业务库。

## 8. 外部通知（可选，需要你做）

默认不向外发送任何消息，只在网页和 App 的通知中心显示。要推送到手机，选一个渠道：

| 渠道 | 手机上用什么看 | 限制 |
|---|---|---|
| **企业微信群机器人**（推荐） | 企业微信 App（能否通过"微信插件"在微信里收到，需要实测） | 每个机器人每分钟 20 条；个人可以注册未认证的企业 |
| Server酱 | 微信（服务号消息） | 免费版每天 5 条；消息经过第三方服务器 |

**步骤**（以企业微信为例）：

1. 在企业微信的一个群里：群设置 → 群机器人 → 添加 → 复制 webhook 地址。**这个地址就是密钥**，不要发到群聊或提交进仓库。
2. 用 van 登录服务器，在 `~/.config/minerva/app.env` 末尾加一行：`MINERVA_NOTIFY_WECOM=<webhook 地址>`。
   - 用 Server酱时改为：`MINERVA_NOTIFY_SERVERCHAN=<SendKey>`。
   - 可选：`MINERVA_NOTIFY_MIN_LEVEL=warning`，只推送警告和严重事件（默认连"交易清单已生成"这类消息也推送）。
3. 重启 API：`systemctl --user restart quant-api`。
4. 网页的"系统管理 → 外部通知"里点"发送测试消息"，或者在服务器上运行 `~/L1/minerva-stage4/.venv/bin/quant-app notify test`。

之后每晚决策作业结束时推送一条汇总。消息只含事件标题：持仓风险只发条数，不发证券代码；金额和持仓不外发。
