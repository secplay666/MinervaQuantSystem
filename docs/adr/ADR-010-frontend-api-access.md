# ADR-010：前端、API 与外网接入（阶段 4）

- 状态：已接受
- 日期：2026-09-27
- 关联：ARCHITECTURE §5.12、§13、§15；ADR-005、ADR-008；[阶段 4 计划](../plans/stage4-daily-decision-support.md)；设计细节见 [stage4-app-design.md](../design/stage4-app-design.md)

## 背景

- ARCHITECTURE §15 原先建议"工作台用 Streamlit 起步"。现在的需求有三项：PC 网页、安卓 App、管理员管理用户和权限。这超出了 Streamlit 的能力。
- 部署条件：服务器上的普通用户没有 root 和 docker 权限；通过阿里云 frp 访问；没有域名；用户少于 10 人。

## 决定

### 1. 后端

- **位置**：`src/quant_system/app/`，与领域模块在同一个 Python 包里。
- **技术**：FastAPI + SQLAlchemy 2 + Alembic + Pydantic。
- **API 的职责**：只负责身份校验、输入验证和业务编排。重计算不在请求里进行（ARCHITECTURE §5.12）。

### 2. 决策作业

- CLI 命令 `quant-decision`，代码在 `src/quant_system/decision/`。
- 由 `scripts/daily_update.sh` 在采集之后调用。

### 3. 存储

- **业务库**：SQLite，WAL 模式，路径 `data/app/app.sqlite`。以后迁 PostgreSQL，所以只使用 SQLAlchemy 的可移植特性。
- **行情和研究数据**：只读，通过 `market.duckdb` 的只读连接和研究数据加载器读取。
- **备份**：业务库纳入 `backup_data.sh`，用 SQLite 的在线备份接口复制。

### 4. 认证

- 密码用 argon2id 哈希。
- 访问令牌（JWT）15 分钟过期；刷新令牌 7 天，每次使用后轮换，数据库里只存哈希。
- 连续 5 次登录失败，锁定 15 分钟。
- 管理员可以开启 TOTP 两步验证。
- 管理员可以撤销会话、禁用账号。
- 签名密钥放在仓库之外的环境文件里（`~/.config/minerva/app.env`，权限 600）。

### 5. 权限

- 三个角色：`admin`、`reviewer`、`viewer`。
- 权限码示例：`decision:approve`、`account:edit`、`user:manage`。
- 菜单和按钮权限由后端下发。权限矩阵见设计文档。

### 6. 前端

放在 `web/` 目录，是一个 pnpm monorepo，基于 vue-vben-admin v5：

- 锁定到一个具体版本，保留其 MIT 许可声明；
- 只保留需要的应用和包。

其中包括：

| 目录 | 内容 |
|---|---|
| `apps/web` | PC 网页，Ant Design Vue 版本 |
| `apps/mobile` | 手机端，Vue 3 + Vant 4，由 Capacitor 8 打包成安卓 App；同一份构建也可以作为手机网页，给不能装 APK 的设备（例如 HarmonyOS NEXT） |
| `packages/` | API 客户端和类型（由 OpenAPI 生成）、图表组件（ECharts 6、KLineChart v10） |

构建产物不进 git。APK 在本机用 Android Studio / Gradle 构建。

### 7. 接入与 TLS

```text
浏览器 / App ─TLS─▶ 阿里云 frps:<远程端口> ─TCP 转发─▶ 服务器 frpc ─▶ 127.0.0.1:<Caddy 端口>
                                                                   └▶ 127.0.0.1:<API 端口>（uvicorn）
```

- **frp 只做 TCP 转发**：TLS 在服务器上的 Caddy 终止，阿里云中转机看不到明文。
- **证书**：由 Caddy 的内部 CA 签发，证书包含阿里云公网 IP。
- **PC 端**：安装一次这个根证书。
- **App**：固定证书的公钥，不依赖手机系统的信任列表。
- **监听地址**：Caddy 和 uvicorn 都只监听 127.0.0.1，外部只能经 frp 访问。
- **分工**：frpc 的转发由用户配置，我们提供本地端口。

### 8. 运行方式

- API 和 Caddy 作为 systemd 用户服务（`quant-api.service`、`quant-web.service`）运行：失败后自动重启，低优先级。
- 单元文件放在 `deploy/systemd/`，用安装脚本启用，与每日定时器相同的做法。

### 9. 环境标识

界面上醒目显示当前环境（测试或生产），见 ARCHITECTURE §13。

## 理由

- **同一个 Python 包**：API 可以直接复用领域规则，不会出现两套实现。
- **SQLite、Caddy 和 Node**：都能在普通用户权限下运行，符合服务器"不影响生产业务"的约束。
- **vben**：PC 端和手机端在同一个 monorepo 里，可以共用 API 客户端和图表组件。vben 自带权限框架，用户管理页面的开发量很小。
- **TCP 转发加自签名 CA**：在没有域名、没有 ICP 备案的前提下，仍然做到端到端加密。

## 后果

- PC 端要手动安装一次根证书。以后有了域名和备案，可以换成公共证书，App 同时更新固定的公钥。
- 前端引入 Node/pnpm 工具链。服务器上把 Node 以用户态安装在 `~/.local`，不影响系统。
- SQLite 只允许单个写入者。API 和决策作业通过短事务和 WAL 共存。进入阶段 5 之前迁到 PostgreSQL。

## 修订（2026-09-28，P3/P4 实现时）

1. **TLS 不再用 Caddy**：改由 uvicorn 直接终止 TLS，证书来自我们自建的 CA（`quant-app tls init --ip <公网IP>`，代码在 `app/tls.py`，依赖 `cryptography` 库）。
   - 私钥和证书放在 `~/.config/minerva/tls/`，私钥文件权限 600，CA 私钥不离开服务器。
   - 前端构建产物也由同一个进程同源托管（`MINERVA_WEB_DIR`），附带 gzip 压缩和安全响应头。
   - 这样少了一个需要下载、升级的二进制程序，部署只剩一个 systemd 服务。
   - 接入路径变为：`浏览器/App ─TLS─▶ frps ─TCP─▶ frpc ─▶ 127.0.0.1:8443（quant-app serve）`。
   - 服务器证书有效期 397 天（客户端对叶子证书的上限），到期前用同一个 CA 重新签发即可，各设备不必重装根证书。
   - Windows 自带的 curl 默认要求吊销检查，所以对私有 CA 会失败（需要加 `--ssl-no-revoke`）。浏览器对缺少吊销信息的证书按宽松方式处理，不受影响。
2. **WebSocket**：uvicorn 需要 `websockets` 库才能处理 WebSocket 升级请求，已加入 `app` 依赖分组。TestClient 覆盖不到这一层，是在浏览器实测时发现的。
3. **前端的实际形态**：
   - 位于 `web/`：vben 5.7.0，只保留 `apps/web-antd`；去掉了 git 钩子，以免它往本仓库安装提交检查。
   - 采用前端路由模式：菜单按权限码过滤，`/auth/me` 返回的权限列表填进 vben 的 `roles` 字段。
   - 生产构建使用 hash 路由。
   - 手机端 `apps/mobile` 在 P5 加入。
4. **客户端 IP**：frp 的 TCP 转发不携带来源地址，所以服务看到的客户端都是 127.0.0.1。这带来两个影响：
   - 按 IP 的登录限流（15 分钟内失败 20 次返回 429）实际上变成了全局限流；
   - 审计日志里的 IP 都是 127.0.0.1。

   用户少于 10 人时可以接受。需要真实地址时，可以启用 frp 的 PROXY protocol，但服务端要能解析它（uvicorn 目前不支持），需要在前面加一层代理。
5. **手机端网络**：
   - App 的 fetch 和 XHR 走 CapacitorHttp（原生 HTTP），TLS 遵循 `network_security_config.xml`（系统 CA 加上我们的 CA），也没有跨域问题。
   - WebView 自己的网络栈不一定信任私有 CA，所以 App 内不使用 WebSocket，通知改为 App 在前台时每分钟轮询一次。
   - 同一份构建由服务在 `/m/` 同源托管，作为手机网页使用。
