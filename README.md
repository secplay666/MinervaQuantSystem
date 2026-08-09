# 量化交易辅助系统

当前仓库正在实现第一阶段数据平台。总体设计见 [ARCHITECTURE.md](ARCHITECTURE.md)。

## 当前数据范围

- 数据适配器：AKShare 1.18.78
- 市场：A股
- 样本：20只覆盖沪深主板、创业板、科创板的代表性股票
- 股票日线区间：2020-01-01 至最近可获得交易日
- 指数日线区间：从 1990-01-01 开始请求，各指数按实际可获得历史为准
- 数据集：证券列表、交易日历、未复权日线、主要指数日线、当日市场快照
- 存储：Raw Parquet、Canonical Parquet、DuckDB 查询视图

当前数据只用于数据平台和研究原型。AKShare 官方声明数据接口和数据仅用于学术研究，不应把该数据源直接作为无人值守实盘的唯一依据。

## 环境初始化

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

## 下载数据

```powershell
.\.venv\Scripts\quant-data.exe ingest --config configs/data_platform.json
```

运行会生成：

```text
data/
├─ raw/                    # 按运行批次保存供应商原始结果
├─ canonical/              # 统一字段、单位和类型后的标准数据
├─ manifests/              # 每次采集运行清单
├─ reports/                # 数据质量报告
└─ market.duckdb           # 本地查询入口
```

## 查询数据

```powershell
.\.venv\Scripts\quant-data.exe catalog
.\.venv\Scripts\quant-data.exe query "SELECT * FROM daily_bars ORDER BY trade_date DESC LIMIT 10"
```

也可以在Python中直接使用DuckDB：

```python
import duckdb

con = duckdb.connect("data/market.duckdb", read_only=True)
df = con.execute(
    """
    SELECT symbol, trade_date, close, volume_shares
    FROM daily_bars
    WHERE symbol = '600519'
    ORDER BY trade_date DESC
    LIMIT 20
    """
).fetchdf()
```

## 数据层约束

- Raw层不可变，每次采集使用独立 `run_id`。
- Canonical层只保存未复权真实价格；后续单独引入复权因子。
- 策略和回测只能读取本地标准数据，不直接调用AKShare。
- 成交量统一换算为“股”，成交额统一为人民币“元”。
- 每条标准记录保存来源、采集时间、运行批次和模式版本。
- AKShare升级必须先运行测试和小样本采集，不能自动追随最新版。
