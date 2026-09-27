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
| `user:manage`、`audit:view` | ✓ | | |

## 7. 修订记录

- 2026-09-27：初版（P0）。
