# research：一次性研究脚本

每个子目录对应一次研究。脚本保留下来，是为了能复查结论、换新数据重跑。它们不属于日常流程，也没有测试。结论写在当天的工作记录（`WORKLOG_*.md`）、ADR 或设计文档里。

**运行方式**：除非注明，都在服务器上用开发环境的代码和数据副本运行，例如：

```bash
cd ~/L1/research/<子目录>        # 脚本拷到这里，产出的数据也写在这里
PYTHONPATH=~/L1/minerva-dev/src ~/L1/minerva-dev/.venv/bin/python <脚本> ...
```

- 数据目录可以用环境变量 `MINERVA_DATA` 指定，默认是 `~/L1/minerva-dev/data`。
- 只读开发环境的数据副本，不碰生产。
- 研究产出的数据文件（`data/`、`*.parquet`、日志）不入库。

| 目录 | 研究 | 主要脚本 | 结论在哪里 |
|---|---|---|---|
| `intraday/` | 沪深300 ETF 的日内数据能补多少；二级市场大单与次日一级市场申赎是否吻合；申赎能否预测指数（10-04、10-06） | `probe_intraday*.py`（数据源探测）、`fetch_intraday.py`、`fetch_em_ws.py`、`retry_flow_days.py`（取数）、`analyze_intraday.py`、`creation_vs_index.py`、`creation_events.py`（分析） | `WORKLOG_2026-10-04.md`、`WORKLOG_2026-10-06.md` |
| `market_flow/` | 主播说的"资金流向"从哪来：东方财富、同花顺、新浪、融资融券、北向资金能否取到；"主力净流入"与涨跌的关系；各家口径是否一致 | `probe_market_flow{,2,3,4}.py` | `WORKLOG_2026-10-06.md` |
| `money_map/` | 还原"钱去哪地图"的拥挤度公式并回测；券商盈利预测和增减持数据能否取到 | `money_map2.py`（还原和回测，报告见 `money_map2_report.txt`）、`probe_expectations.py`、`probe_eps_months.py`、`check_catalog.py` | `WORKLOG_2026-10-06.md`、`docs/design/money-map.md` |
| `stages/` | 四阶段（Weinstein）在全市场的表现；仓位管家各预设适合什么行情 | `stage_backtest.py`、`stage_filters.py`（最早的原型）、`stage_backtest_full.py`（2005 年起全历史，"阶段回测"页的算法） | `WORKLOG_2026-10-07.md` |
| `buyback/` | 回购、股东增减持之后的表现；检验"回购注销 + 业绩不差，半年胜率 70%" | `probe_buybacks.py`（回购表字段）、`probe_fields.py`（进度代码含义、增减持字段单位）、`buyback_study.py`（事件研究） | `WORKLOG_2026-10-07.md` |
| `breadth/` | 市场宽度在全历史上算一遍要多久、占多少内存 | `time_breadth.py`（最早的查询）、`measure_breadth.py`（页面实际的计算，带内存上限） | `WORKLOG_2026-10-07.md` |
| `etf/` | 资金波段在真实数据上的检查 | `check_waves.py` | `WORKLOG_2026-10-07.md` |
| `multifactor/` | 主策略（决策作业用的多因子 + 规则法）在不同市场阶段的月度胜率和超额；加减仓规则的粗算；每次调仓换掉多少只、持有多久 | `phase_winrate.py`、`exposure_overlays.py`、`turnover_detail.py` | `WORKLOG_2026-10-07.md`、`docs/guides/decision-and-paper-trading-guide.md` §14 |
| `regime/` | 能否客观判断牛熊；各类因子在不同市场状态下的表现；按状态切换因子权重是否有用（2006–2026） | `regime_factor_study.py`（配置 `configs/research/regime_study.json`） | `WORKLOG_2026-10-08.md` |
| `timing/` | 各数据源每天几点发布当天数据、之后会不会修订，决定每日采集能否提前（10-09 探测） | `probe_publication.py`、`timing_report.py` | `WORKLOG_2026-10-08.md` |
| `options/` | 上交所和深交所 ETF 期权历史数据（2015 年起逐合约风险指标和日线、每日统计），用于研究破位后买认沽等问题 | `download_options.py`（下载）、`put_after_breakdown.py`（破位后买认沽的检验）、`put_grid.py`（买入时机、卖出方式、期限、行权价、下跌快慢）、`underlyings.py`（有期权的 ETF 背后的指数：成分、行业、估值、波动、期权活跃度）、`hindsight_puts.py`（事后测算：某个高点买入各合约，到某日的收益） | `WORKLOG_2026-10-10.md`、`WORKLOG_2026-10-11.md`、`docs/research/options-put-after-breakdown-2026-10.md`、`docs/research/option-underlyings-2026-10.md`、`docs/research/options-hindsight-0922-2026-10.md` |
| `us_markets/` | 美股主要指数（标普500、道琼斯、纳斯达克、费城半导体、中国金龙，新浪）和富时中国 A50 期货的日线，用于研究美股和 A 股的关系 | `download_us.py`（下载）、`us_ashare_relation.py`（隔夜影响、开盘跳空、大涨大跌后、周月相关、每年涨跌、危机回撤） | `docs/research/us-ashare-relation-2026-10.md` |
| `macro/` | 美联储利率周期（美联储官网的历次调整）、中美国债收益率、人民币汇率与 A 股牛熊的关系；牛熊的事后划分和实时指标的滞后 | `parse_fed.py`（解析美联储官网的利率调整表）、`fed_cycles.py`（分析）、`bonds_stocks.py`（国债是否领先股票）、`stream_1024_checks.py`（10-24 直播里的比值规律、清仓时点等说法） | `docs/research/fed-cycles-ashare-2026-10.md`、`WORKLOG_2026-10-24.md` |
| `index_timing/` | 能否预测指数短期涨跌：量价（跌久放巨量、急跌）、宽基 ETF 大额申购、趋势、成交热度、拥挤度、融资余额、股债性价比，以及合起来的滚动样本外预测 | `index_signals.py`、`intraday_creation.py`（盘中能否估计当天的申购） | `docs/research/index-timing-2026-10.md` |
| `stock_flow/` | 个股的大资金流入流出（用日线的放量上涨、放量下跌近似）、短期反转和之后 1–20 天的超额 | `volume_price_events.py`；`accumulation_cycle.py`（筹码成本、集中度、吸筹和派发特征）、`shareholder_count.py`（股东户数，巨潮）、`insider_selling.py`（大涨后的减持）；`download_flow.py`（东方财富资金流，未成功，未使用） | `docs/research/stock-flow-2026-10.md`、`docs/research/accumulation-distribution-2026-10.md` |
| `checks/` | 新功能在真实数据上的核对（只写业务库的临时副本）：手工账户除权、机构持有页；回购增持、市场宽度和仓位管家标签 | `check_features.py`、`check_info_lists.py` | `WORKLOG_2026-10-07.md` |

**统一的写法**
- 只用当时能知道的信息：事件按公告日之后的第一个交易日买入，筛选条件只用公告时已公布的数据。
- 和"随便哪天买"的基准比，并说明样本数和样本是否重叠。
