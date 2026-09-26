# 量化交易辅助系统

当前仓库已完成第一阶段"数据底座"和第二阶段"可验证回测"。总体设计见 [ARCHITECTURE.md](ARCHITECTURE.md)，关键决策见 [docs/adr/](docs/adr/)。

## 数据范围

- 数据适配器：AKShare 1.18.78（固定版本，来源选择见 [ADR-003](docs/adr/ADR-003-data-vendors.md)）
- 市场：沪深京 A 股，含 CDR；不含 B 股（[ADR-001](docs/adr/ADR-001-market-and-universe.md)）
- 股票池：**不带幸存者偏差**。包括当前在市证券，以及 2020-01-01 以后退市的全部证券
- 股票日线：2020-01-01 至最近一个已收盘的交易日，不复权
- 指数日线：从 1990-01-01 开始请求，各指数按实际可获得的历史为准
- 数据集：

| 数据集 | 说明 |
|---|---|
| `security_master` | 证券主数据：板块、上市日期、退市日期、状态（DuckDB 中 `instruments` 视图只含未退市证券） |
| `trading_calendar` | 交易日历 |
| `daily_bars` | 不复权日线，成交量单位为"股"，成交额单位为"元" |
| `adjustment_factors` | 后复权因子事件及派生的前复权因子（[ADR-004](docs/adr/ADR-004-adjustment-and-point-in-time.md)） |
| `index_bars` | 主要指数日线 |
| `market_snapshot` | 收盘后的全市场快照 |
| `suspension_events` | 停复牌事件（东方财富数据中心 + 百度日历，2023 年起） |
| `security_name_changes` / `risk_warning_intervals` | 风险警示（ST/\*ST/退市整理）区间，三个交易所都带生效日期：深市来自简称变更，沪市、北交所由交易所公告推导，并用名称和价格证据校正（[ADR-004](docs/adr/ADR-004-adjustment-and-point-in-time.md)） |
| `risk_warning_bulletins` / `risk_warning_adjustments` | 上交所、北交所风险警示相关公告，以及推导 ST 区间时每一处修正的记录 |
| `bar_gaps` | 对照交易日历检测出的日线缺口，并标注是否能被停牌事件解释 |

当前数据只用于数据平台和研究原型。AKShare 官方声明其数据仅用于学术研究，不应作为无人值守实盘的唯一依据。

## 环境初始化

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest
```

## 日常更新

建议在交易日北京时间 16:00 以后运行。16:00 之前运行时，当天不会被视为已收盘，不会写入未完成的日线。

```powershell
.\.venv\Scripts\quant-data.exe ingest --config configs/data_platform.json
```

一次增量运行依次完成：

1. 交易日历和证券主数据（含退市证券）；
2. 在市证券的日线，每次重新下载最近 3 个交易日以吸收供应商修订；
3. 全部在市证券的复权因子（已退市证券冻结）；
4. 指数；
5. 停复牌事件，以及三个交易所的 ST 区间（深市简称变更；沪市、北交所按季度或年度窗口抓取交易所公告，上交所有限流时剩余窗口顺延到下次运行）；
6. 收盘后快照；
7. 全量审计；
8. 生成质量报告、数据版本和 DuckDB 目录。

退出码 `0` 表示 `complete`，`2` 表示 `partial` 或 `failed`，详见质量报告。

运行产物：

```text
data/
├─ raw/                    # 按运行批次保存供应商原始响应，不可变，文件元数据记录实际来源
├─ canonical/              # 标准数据，唯一事实来源
├─ quarantine/             # 未通过阻断检查、没有写入标准层的数据
├─ manifests/              # <run_id>.json 运行清单；run_id=<id>/ 下为分区清单和原始文件列表
├─ reports/                # 质量报告（Markdown/JSON）及完整问题列表 issues.parquet
├─ archive/                # 重建前归档的旧标准层
└─ market.duckdb           # 查询入口（由标准层物化生成）
```

## 质量门槛

- **阻断**（写入被拒，本次运行记为 `partial`）：主键重复、OHLC 关系不合法、非正价格、成交量单位异常、空响应、指数历史缩短、证券列表异常缩减、最新交易日覆盖率低于 98%（已扣除停牌证券）、复权因子刷新成功率低于 99%。
- **警告**：个别证券下载失败、无法解释的日线缺口、超出涨跌幅限制且当日无复权事件、复权因子过期或缺失、历史复权因子被上游修订。

## 查询数据

```powershell
.\.venv\Scripts\quant-data.exe catalog
.\.venv\Scripts\quant-data.exe query "SELECT * FROM ingestion_runs ORDER BY run_id DESC LIMIT 5"
.\.venv\Scripts\quant-data.exe query "SELECT symbol, trade_date, close, hfq_close, factor_status FROM daily_bars_adjusted WHERE symbol='600519' ORDER BY trade_date DESC LIMIT 5"
```

```python
import duckdb

con = duckdb.connect("data/market.duckdb", read_only=True)
df = con.execute(
    """
    SELECT symbol, trade_date, close, hfq_close, volume_shares
    FROM daily_bars_adjusted
    WHERE symbol = '600519'
    ORDER BY trade_date DESC
    LIMIT 20
    """
).fetchdf()
```

使用复权价格时请遵守 [ADR-004](docs/adr/ADR-004-adjustment-and-point-in-time.md)：

- 成交、涨跌停、费用一律用不复权价；
- 收益率用 `hfq_*` 的比值；
- `qfq_*` 以最新一次除权为锚点，不是时间点数据，只能用于展示。

## 维护命令

```powershell
# 从原始层重建标准层：默认只写入 data/staging 并输出差异，加 --apply 才替换（旧标准层归档到 data/archive）
.\.venv\Scripts\quant-data.exe rebuild
.\.venv\Scripts\quant-data.exe rebuild --apply

# 只运行部分步骤（交易日历、证券主数据和审计总会运行），例如只刷新停复牌与 ST 历史
.\.venv\Scripts\quant-data.exe ingest --steps status_history

# 对整个标准层做审计（只读）
.\.venv\Scripts\quant-data.exe audit

# 数据库被其他进程占用、目录构建失败时，关闭占用进程后补建
.\.venv\Scripts\quant-data.exe catalog --rebuild
```

## 回测

```powershell
.\.venv\Scripts\quant-backtest.exe run --config configs/strategies/momentum_top50.json
```

- 输出目录为 `artifacts/backtests/<run_id>/`，包括：
  - `report.md`：绩效、分年度收益、执行统计、会计检查、已知局限；
  - `manifest.json`：代码版本、配置哈希、规则文件哈希、数据版本和输入指纹；
  - 订单、成交、拒单、持仓、净值、信号等 parquet 文件。
- 成交假设：T 日收盘后生成信号，T+1 开盘价加滑点成交；同时处理涨跌停、停牌、T+1 可卖、整手、参与率、除权和退市（[ADR-002](docs/adr/ADR-002-signal-timing-and-fills.md)）。
- 市场规则按生效日期写在 `configs/market_rules/cn_a_share.json`，包括涨跌幅、新股无涨跌幅日、申报数量、印花税、过户费。
- 示范策略（120 日动量 Top50）只用于验证引擎，验证方案和结果见 [ADR-006](docs/adr/ADR-006-demo-strategy-and-validation.md)。
- 回归测试使用已提交的固定数据集 `tests/fixtures/backtest_cn_small/`（40 只股票，由 `scripts/extract_backtest_fixture.py` 生成）。更新基准文件需要显式设置 `UPDATE_GOLDEN=1`。

## 数据层约束

- 原始层不可变，每次采集使用独立的 `run_id`。标准层可以从原始层完整重建（[ADR-005](docs/adr/ADR-005-storage.md)）。
- 标准层只保存不复权价格；复权通过 `adjustment_factors` 在查询时计算。
- 策略和回测只能读取本地标准数据，不直接调用 AKShare。
- 成交量统一为"股"，成交额统一为人民币"元"；`volume_scale` 记录对供应商原值所做的换算。
- 每条标准记录保存来源、采集时间、运行批次和模式版本。每次运行在清单中记录 `data_version`、`code_version` 和 `config_hash`。
- 北交所退市证券需要在 `configs/data_platform.json` 的 `manual_delistings` 中维护。
- AKShare 升级前必须先运行测试和小样本采集，并对照 ADR-003 中的缺陷清单逐项验证，不能自动跟随最新版。
