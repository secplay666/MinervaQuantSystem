# 阶段 4 应用设计：表结构、决策作业、API、页面、权限

- 状态：P0 设计，开发中可修订。修订时在文末记录。
- 关联：[计划](../plans/stage4-daily-decision-support.md)、[ADR-008](../adr/ADR-008-review-and-execution-boundary.md)、[ADR-010](../adr/ADR-010-frontend-api-access.md)

## 1. 约定

- **金额**：一律用整数"分"（`*_fen`），与账本和回测一致。
- **数量**：一律用整数"股"。
- **权重**：浮点数。
- **时间**：时间戳带时区，存 UTC；交易日另存 `trade_date`；界面显示为 Asia/Shanghai（ARCHITECTURE §6）。
- **主键**：
  - 业务对象用字符串 ID，带前缀和时间，例如 `dr-20260930-acct1`；
  - 决策运行的 `run_id` 与数据平台的格式相同；
  - 日志类表用自增 ID。
- **只追加**：事件和审计只追加。撤销或冲正时生成反向记录，不做物理删除。

## 2. 决策作业 `quant-decision daily`

在采集之后运行，每个启用的账户各运行一次。

**输入**：
- 最新一次完整的采集运行：包括数据版本、原始日历和预期交易日；
- 账户在 T 日收盘后的持仓：由事件重放得到；
- 策略配置。

**步骤**：
1. **闸门**：见 §2.1。不通过时写一条 `status=blocked` 的决策运行记录和一个事件，然后结束。
2. **判断调仓日**：用原始日历判断 T 是否是调仓日（主策略是月末）。如果管理员强制触发，则当作调仓日。
3. **监控**（每天都做）：
   - 持仓的风险事件：ST 变化、停牌、次日除权除息、财报披露、跌停、单日大跌超过阈值；
   - 与最新目标组合的偏离。
4. **调仓日才做**：
   1. 构造 `PortfolioState`：以 T 日收盘价标价；`previous_target` 取上一次目标组合。
   2. 调用 `MultiFactorStrategy.on_close(view, state)`，得到目标权重和解释。
   3. 调用 `size_orders`，得到交易意图：参考价用 T 日收盘价；已知 T+1 除权的，附风险提示。
   4. 对每条意图做下单前风险检查，见 §2.2。
5. **写库**：`decision_runs`、`target_positions`、`order_intents`、`risk_checks`、`events`，写入同一个事务。
6. **报告**：`artifacts/decisions/<run_id>/`，包括 `report.md`、`intents.csv`、`targets.csv` 和 `manifest.json`。
   - 清单记录代码版本、配置哈希、数据版本、闸门结果和耗时。
   - 同一输入重跑，报告逐字节相同。

模拟账户的成交作业 `quant-decision paper`：
- 在 T+1 的采集之后、`daily` 之前运行；
- 对截止前批准的意图按 T+1 开盘模拟成交；
- 处理除权和退市；
- 写入成交、事件和快照。

### 2.1 闸门规则

| 编号 | 条件 | 阻断时的提示 |
|---|---|---|
| G1 | 最近一次采集的 `status=complete`，且最新交易日等于 T | 数据未更新或采集不完整，请查看数据健康页 |
| G2 | 当前标准层的数据版本等于该次采集记录的版本 | 采集后数据被改动，请确认后重跑 |
| G3 | 股票池规模、有效得分数量与近 6 个月中位数的偏离不超过 20% | 信号数量突变 |
| G4 | 手工账户：持仓在上一个调仓日之后确认过，或者期间没有已批准的意图 | 持仓可能过期，请先更新持仓 |
| G5 | 目标组合权重之和在 [0.90, 1.0] 之间；持股数与目标持股数的偏离不超过 10% | 目标组合异常 |

### 2.2 下单前风险检查

每条意图逐项检查。结论为 pass、warn 或 reject。

| 编号 | 内容 | 结论 |
|---|---|---|
| R1 | T 日收盘涨停且方向为买入，或收盘跌停且方向为卖出 | warn：次日可能无法成交 |
| R2 | T 日停牌，或已公布 T+1 停牌 | 买入：reject；卖出：warn |
| R3 | T+1 除权除息 | warn：数量可能需要调整 |
| R4 | 数量超过 20 日平均成交量 × 参与率上限 | warn |
| R5 | 单股权重超过上限，或行业偏离超过上限 | reject |
| R6 | 买入金额加费用，超过现金加卖出所得 | 整体 reject |
| R7 | 风险警示（ST）或退市整理期的证券 | 买入：reject |

## 3. 业务库表结构（SQLite → PostgreSQL）

| 表 | 主要字段 | 说明 |
|---|---|---|
| `users` | id, username, display_name, password_hash, is_active, must_change_password, totp_secret, failed_logins, locked_until, last_login_at | 账号 |
| `roles` / `permissions` / `role_permissions` / `user_roles` | code, name | 角色与权限码 |
| `refresh_tokens` | id, user_id, token_hash, expires_at, revoked_at, ip, user_agent | 可撤销会话 |
| `audit_log` | id, at, user_id, action, subject_type, subject_id, before_json, after_json, reason, ip, request_id | 只追加 |
| `accounts` | account_id, name, mode（manual/paper）, strategy_config, initial_cash_fen, start_date, is_active | 账户 |
| `decision_runs` | run_id, account_id, trade_date, next_session, kind（rebalance/monitor/forced）, status（complete/blocked/failed）, gates_json, ingest_run_id, data_version, code_version, config_hash, strategy_id, nav_fen, cash_fen, report_dir | 每个账户每天一条 |
| `target_positions` | run_id, symbol, target_weight, target_qty, rank, score, explanation_json | 目标组合 |
| `order_intents` | intent_id, run_id, account_id, symbol, side, qty, ref_price_fen, limit_up_fen, limit_down_fen, est_notional_fen, est_fees_fen, reason, rank, status, valid_until | 交易意图 |
| `risk_checks` | id, run_id, intent_id（可空，表示整体检查）, rule_id, decision, actual, limit_value, message | 风险检查结果 |
| `approvals` | id, intent_id, action（approve/modify/reject/override）, qty_before, qty_after, reason, user_id, at | 人工授权 |
| `fills` | fill_id, account_id, intent_id（可空）, trade_date, symbol, side, qty, price_fen, commission_fen, stamp_duty_fen, transfer_fee_fen, source（manual/import/paper）, batch_id, reversed_by | 成交事实 |
| `position_events` | event_id, account_id, trade_date, type（deposit/fill/corporate_action/delisting/adjustment/reversal）, symbol, qty_delta, cash_delta_fen, ref_id, reason, created_by | 可重放的账本事件 |
| `account_snapshots` | account_id, trade_date, cash_fen, market_value_fen, nav_fen | 日终快照 |
| `position_snapshots` | account_id, trade_date, symbol, qty, mark_fen, value_fen, cost_fen | 日终持仓 |
| `imports` | batch_id, account_id, kind（holdings/fills）, filename, sha256, rows, status, summary_json | 导入批次 |
| `events` | event_id, at, level（info/warning/critical）, category（data/decision/risk/account/system）, title, body, run_id, trade_date, account_id, action_hint | 通知中心 |
| `event_reads` | event_id, user_id, read_at | 已读状态 |

账户状态以 `position_events` 的重放为准，快照只是加速查询用的缓存。重放时复用 `quant_system.ledger` 的不变量检查：现金恒等式，以及持仓不得为负。

## 4. API（`/api/v1`，OpenAPI 自动生成）

| 分组 | 接口 | 权限 |
|---|---|---|
| 认证 | `POST /auth/login`、`/auth/refresh`、`/auth/logout`；`GET /auth/me`；`POST /auth/password`；`POST /auth/totp/*` | 已登录 |
| 用户 | `GET/POST /users`；`PATCH /users/{id}`；`POST /users/{id}/reset-password`；`GET /roles`；`PUT /roles/{code}/permissions`；`GET /sessions`；`DELETE /sessions/{id}` | `user:manage` |
| 审计 | `GET /audit` | `audit:view` |
| 系统 | `GET /health`：数据新鲜度、最近一次采集、决策和备份；`GET /jobs`：每日任务历史 | `data:view` |
| 通知 | `GET /events`；`POST /events/{id}/read`；`WS /ws/events` | `event:view` |
| 市场 | `GET /market/overview?date=`：指数、涨跌分布、涨跌停、成交额、行业；`GET /market/indices/{code}/bars` | `market:view` |
| 证券 | `GET /instruments/search?q=`；`GET /instruments/{symbol}`；`/bars?adjust=&start=&end=`；`/factors?date=`；`/fundamentals`；`/events` | `market:view` |
| 账户 | `GET /accounts`；`GET /accounts/{id}`；`/positions?date=`；`/nav`；`/exposures`；`/events` | `account:view` |
| 账户写入 | `POST /accounts/{id}/holdings`：预览和提交；`POST /accounts/{id}/fills`；`POST /accounts/{id}/adjustments`；`POST /position-events/{id}/reverse` | `account:edit` |
| 账户管理 | `POST /accounts`；`PATCH /accounts/{id}` | `account:manage` |
| 决策 | `GET /decisions?account=&date=`；`GET /decisions/{run_id}`：闸门、目标、报告；`GET /decisions/{run_id}/intents`；`GET /decisions/{run_id}/export.csv` | `decision:view` |
| 审核 | `POST /intents/{id}/approve`；`/modify`；`/reject` | `decision:approve` |
| 覆盖与触发 | `POST /intents/{id}/override`：硬拒绝后的覆盖；`POST /decisions/trigger`：强制调仓，异步执行，返回任务号 | `decision:trigger` |

## 5. 页面

**PC 端**（`web/apps/web`）：

| 页面 | 内容 |
|---|---|
| 首页 | 今日概览：数据状态、市场摘要、各账户净值和当日盈亏、待审核数量、最新事件 |
| 数据健康 | 采集历史（history.tsv）、质量问题、备份状态、闸门结果 |
| 市场 | 指数 K 线、涨跌分布、涨跌停统计、行业热力图 |
| 每日决策 | 选择账户和日期；闸门结果；目标组合（排名、得分、各类因子、行业）；交易清单与审核（批量批准、改数量、拒绝并写原因）；风险提示；导出 CSV |
| 账户 | 持仓、现金、净值曲线（对比等权全收益基准和模拟账户）、行业和风格暴露、回撤；成交回填；持仓录入和导入（预览差异后再确认）；事件流水和冲正 |
| 个股详情 | K 线和技术指标（KLineChart）、因子得分历史、财务要点、公司事件（ST、停牌、分红） |
| 通知中心 | 按级别和类别筛选，标记已读 |
| 系统管理 | 用户（新增、禁用、重置密码、分配角色）、角色权限、会话、审计日志；仅管理员可见 |

**手机端**（`web/apps/mobile`）：概览、决策（查看和审核）、持仓、个股 K 线、通知、我的（修改密码、退出）。

## 6. 权限矩阵

| 权限码 | admin | reviewer | viewer |
|---|:-:|:-:|:-:|
| `dashboard:view`、`market:view`、`data:view`、`event:view`、`decision:view`、`account:view` | ✓ | ✓ | ✓ |
| `decision:approve` | ✓ | ✓ | |
| `account:edit` | ✓ | ✓ | |
| `decision:trigger`、`account:manage` | ✓ | | |
| `user:manage`、`audit:view`、`notify:manage` | ✓ | | |

## 7. 修订记录

- 2026-09-27：初版（P0）。
- 2026-09-28（P1 实现时的修订）：
  - **停牌判断**：以下情况算作"已公告停牌"：
    - 当天开始的停牌；
    - 有结束日且结束日不早于当天的停牌；
    - 有预计复牌日且在当天之后的停牌。

    既没有结束日、也没有预计复牌日的旧记录忽略。原因是百度停牌日历里约 1,700 条记录的结束日为空，大多早已复牌；实测中它们曾让 10% 的买入被误拒。仍在停牌的股票在 T 日没有行情，由 R2 的"今日停牌"拦下。
  - **重跑规则**：
    - 被阻断或失败的运行，在后续运行时自动作废；
    - 已完成的运行，需要 `--rerun` 才能重跑；
    - 已有意图被审核或回填过的运行，拒绝重跑；
    - 当日已有完成的决策时，后续的闸门失败不会覆盖它。
  - **R5**：P1 只检查单股权重，以 1.25 倍上限为警告、2 倍为拒绝。行业偏离放到 P3 的暴露报告里。
  - **手工账户的除权**：P1 只提示（M5：除权后请核对持仓）。除权的自动处理与模拟账户的成交（P6）一起实现。
  - **每日任务**：`daily_update.sh` 的顺序为采集 → 决策 → 备份。退出码 4 表示有决策运行失败；被阻断不算失败。
  - **备份范围**：加入业务库 `data/app`，与实验登记表一样通过 SQLite 在线备份接口复制。
- 2026-09-28（P2 实现时的修订）：
  - **权限**：内置角色（管理员、审核员、只读）的权限由 `rbac.py` 定义，启动时同步到数据库，不能在界面上修改。用户要求的"开关权限"通过自定义角色实现：新建角色后可以逐项开关权限（`POST /roles`、`PUT /roles/{code}/permissions`）。
  - **防止锁死**：管理员不能停用自己，也不能移除自己的管理员角色；系统至少保留一名启用的管理员。
  - **WebSocket**：浏览器无法给 WebSocket 设置请求头，所以令牌作为连接后的第一条消息 `{"token": ...}` 发送。服务端每 3 秒推送一次新事件。
  - **强制调仓**：`POST /decisions/trigger` 在后台线程中运行，同一时间只允许一个任务；任务状态保存在内存里，通过 `GET /jobs/{id}` 查询，服务重启后丢失，但结果本身已写入数据库。
  - **修改数量**：
    - 买入：数量必须是整手；
    - 卖出：不能超过持仓，零股只能一次卖完；
    - 已有成交回填的意图不能再修改或拒绝。
  - **持仓录入**：代码、数量、成本价之间可以用逗号、制表符或空格分隔；代码的前缀后缀（如 sh600000、600000.SH）可以自动识别。持仓日期不能早于账户的最后一笔记录。
  - **行情概览**：涨跌停统计使用 `cn_a_share.json` 的规则（包括 ST 和日期分段）；新股的无涨跌幅窗口按上市 10 个自然日近似排除。结果按交易日缓存，数据库文件变化后失效。
- 2026-09-28（P6 模拟成交）：
  - **执行时机**：`decision/paper.py` 在每日决策作业里运行：模拟账户在生成当天的决策之前，先处理截至 T 的所有未处理交易日，所以决策用的是最新持仓。
  - **每个交易日的处理顺序**（与回测引擎相同）：
    1. 除权：整数股，零股折现金；
    2. 退市结算；
    3. 关闭被新决策取代的旧意图；
    4. 执行意图：先卖后买；无行情或开盘锁板时不成交；检查 T+1 可卖；成交量占比上限；资金；价格为开盘价加减滑点，并限制在涨跌停价内；
    5. 日终快照。
  - **防后见之明**：只执行在执行日 09:15 之前批准（或修改、覆盖）的意图；更晚批准的顺延到下一个交易日。
  - **重试**：
    - 没买到的股票最多重试 `buy_retry_sessions`（5）个交易日；卖出一直重试，直到被新决策取代。
    - 与回测的区别：回测在重试日会按当时的净值和价格重新计算数量，模拟账户保持人工批准的数量。所以只有第一执行日可以与回测逐笔对照（已测试）。
  - **意图的执行状态**：新增 `filled_qty`、`execution`、`execution_note` 字段（迁移 0002）。
    - `execution` 的取值：空或 `partial`（未结束）、`filled`、`unfilled`、`partial_closed`（部分成交后结束）。
    - 手工账户回填成交或冲正时，按未冲正的成交重新计算。
  - **进度标记**：账户新增 `paper_through`，记录已模拟到的交易日。同一交易日只处理一次，所以重跑不会产生任何写入；所有编号都由意图和交易日决定。
- 2026-09-28（P8 外部通知）：
  - **渠道**：企业微信群机器人、Server酱，写在服务器的 `app.env`（`MINERVA_NOTIFY_WECOM`、`MINERVA_NOTIFY_SERVERCHAN`），未配置时不发送。
  - **内容**：每次派发把新事件汇总成一条消息，只发标题。持仓风险提示的标题含证券代码，改为按账户只发条数；正文、金额、持仓都不外发。
  - **派发**：`daily_update.sh` 在备份之后运行 `quant-app notify dispatch`；采集或备份失败先记为事件（`quant-app event add`）。
  - **送达记录**：每个事件和渠道一行（`event_pushes`，迁移 0003），失败的在后续派发里重试，最多 3 次；只推送回溯窗口（默认 24 小时）内的事件，所以启用渠道时不会补发历史。
  - **管理页面**：系统管理 > 外部通知（`notify:manage`），可以查看渠道和送达记录，发送测试消息（每 30 秒最多一次，记入审计）。
- 2026-09-28（自查后的修订）：
  - **修改后重新检查**（ADR-008 §7）：修改数量时重算 R4、R5，并按修改后的数量重算本次决策的 R6。结果为拒绝时，不允许修改。
    - 决策运行在 `summary.limits` 中记录这些检查所需的参数：单股上限、成交量占比、每只股票的量能上限。
    - 这之前生成的决策没有这些参数，只能减少数量。
  - **模拟账户的锁定**：已批准的模拟意图，从执行日 09:15 起到当晚模拟成交入账之前，不能拒绝或修改，否则等于看过开盘再决定（ADR-008 §6）。
    - 节假日按交易日处理，只会更早锁定，不会漏锁。
    - 截止之后才批准的意图，从批准后的第一个交易日开始计算重试次数。
  - **模拟账户的账本**：不能手工冲销，只能由系统维护。已放弃的意图（`unfilled`、`partial_closed`）不会因为冲销而重新打开。
  - **手工账户的日期**：
    - 成交日期和被冲销记录的日期，必须晚于已确认持仓的日期（确认的持仓已包含当天的成交），并且不能晚于今天；
    - 持仓日期也不能晚于今天。
  - **费用**：不能为负，业务层和账本都检查。
  - **持仓预览过期**：预览时记录账本的标记（记录数和最大序号）。确认时只要账本有任何新记录，就要求重新预览。
  - **粘贴持仓**：含制表符的行按制表符分列，这样 Excel 里的 `1,000` 不会被拆开。
  - **并发**：API 进程内，账本写入（回填、确认持仓、冲销）串行执行。冲销和成交标记在同一个事务里完成。决策作业在模拟成交后先提交一次，构建策略时不再占用写锁。
- 2026-09-28（补齐计划里的页面功能）：
  - **账户监控**（P3 的"暴露、回撤"）：
    - `GET /accounts/{id}/exposure`：持仓按申万一级行业的权重，对照最近一次调仓决策的目标权重；另有前十大权重、有效持股数（1/Σ权重²）和现金比例。
    - 网页账户页新增"行业暴露"标签，"净值曲线"改为"净值与回撤"，显示最大回撤、当前回撤和回撤曲线；手机端账户页显示回撤、前十大权重和行业暴露。
  - **个股的因子**（P3 的"个股详情：因子"）：
    - 决策作业在每次调仓运行时，写出全市场的综合得分和各类因子得分 `scores.csv`，放在决策报告目录里；排名只在股票池内计算。
    - `GET /instruments/{symbol}/signals`（`decision:view`）返回最近一次调仓的得分、股票池内排名，以及近期入选目标组合的记录。
    - 个股页新增"策略得分"卡片。
  - **M10 财报披露**（P1 的"财报披露"监控）：持仓股在上一交易日之后、当日及之前首次公告的定期报告，按证券产生一条提示，例如"发布 2026 年中报"。数据取自 `fin_income` 的最早公告日。
