# ADR-005：MVP 存储方案

- 状态：已接受
- 日期：2026-09-26
- 关联：ARCHITECTURE.md §10、§18

## 决定

1. **原始层（Raw）**：`data/raw/akshare/<数据集>/run_id=<运行>/<名称>.parquet`，保存供应商响应原样，不可变。Parquet 文件元数据记录实际来源端点（`quant_system.source`）。
2. **标准层（Canonical）**：`data/canonical/<数据集>/[symbol=…/]data.parquet`，是唯一事实来源。每行带 `source`、`run_id`、`ingested_at`、`schema_version`（当前 2.0）。
3. **写入保护**：
   - 标准层拒绝写入空表；
   - 未通过阻断级检查的分区写入 `data/quarantine/run_id=…/`，保留旧分区；
   - 指数历史不能被更短的数据覆盖。
4. **可重建**：`quant-data rebuild` 用当前标准化代码从原始层重放全部历史，写入 `data/staging/`，与现有标准层对比差异；加 `--apply` 后，旧标准层整体归档到 `data/archive/`，新标准层原子替换进来。标准化逻辑修复通过这一流程进入历史数据。
5. **数据版本**：每次运行对标准层所有文件计算 sha256，`data_version` 为全部 (路径, 哈希) 的哈希。清单和质量报告只保留汇总，逐分区统计写入 `data/manifests/run_id=…/partitions.parquet`。清单中同时记录 `code_version`（git 提交及工作区是否有未提交修改）和配置文件哈希。
6. **查询入口**：`data/market.duckdb` 在每次运行后由标准层**物化为表**，先构建到临时文件，再原子替换。视图包括 `daily_bars_adjusted` 和 `instruments`。`ingestion_runs` 由清单文件重建；中断过的运行显示为 `aborted_no_manifest`。数据库被其他进程占用时，本次运行记为 `partial`，之后可用 `quant-data catalog --rebuild` 补建。
7. **事务存储**：订单、审核、账本到阶段 4/5 再引入，先用 SQLite；进入实盘前切换到 PostgreSQL。

## 理由

- 按证券分区便于增量写入和单证券重建。物化到 DuckDB 表后，查询不必每次打开上万个 Parquet 文件（审计测得单次全表扫描约 1–2 秒的固定开销），视图里也不再写死绝对路径。
- 可重建性是阶段 1 的完成标准之一（"检测缺口并重建"），也是修复历史数据的唯一合规方式（ARCHITECTURE §5.2：数据修复必须保留原值与规则）。

## 风险

- `market.duckdb` 是标准层的完整副本（约数百 MB），每次运行都会重新构建。
- Windows 上若有进程打开了数据库，原子替换会失败；这种情况会被记录为 `catalog_status`，不会丢失清单。
- 标准层分区原地覆盖，旧版本只能通过原始层重放或 `data/archive/` 找回，没有做到按数据版本随时切换。回测阶段如需"冻结数据版本"，应在实验清单中记录 `data_version`，并归档对应的标准层快照。

## 复审条件

数据量或并发读取成为瓶颈，或进入需要事务一致性的交易阶段时复审。

## 补充（2026-09-27）：数据打包与迁移

- `scripts/pack_data.py` 只打包无法再生成的内容：原始层、清单、质量报告、检查点、研究产物（含实验登记表）。标准层也一并打包，恢复后可以用重建演练核对。重建归档、staging、因子缓存和 `market.duckdb` 不打包。
- 包格式是不压缩的 POSIX tar（Parquet 已压缩），按固定大小分卷。附带：
  - `MANIFEST.tsv`：每个文件的大小和 sha256；
  - `SHA256SUMS`：每个分卷的 sha256；
  - `PACK.json`：源提交、`data_version`、目录运行编号。
  所有文本文件都用 LF 换行，保证 Linux 上的 `sha256sum -c` 可以直接使用。
- 恢复后的验收：
  1. 分卷校验；
  2. 逐文件校验 sha256；
  3. `catalog --rebuild`；
  4. `audit`；
  5. `rebuild` 演练，对每个数据集做全列比较，差异必须为 0。
- 2026-09-27 首次迁移到服务器，经阿里云 OSS 中转，服务器端限速下载，没有占用 frp 带宽。验收全部通过：42,483 个文件校验一致，23 个数据集重建后与恢复的标准层一致；在服务器上重跑样本内主回测，结果与本机逐位相同。
- 迁移中发现两处跨机器不一致，已修复：
  - 因子中性化时，被完全拟合的名字（行业内只有一只）残差理论上为 0，实际是 BLAS 舍入噪声，其符号随 CPU 不同，决定了这些名字的排名。现在把这类残差置为精确的 0。
  - `bar_gaps.explained_by` 拼接停牌来源时没有指定顺序。现在按来源名排序。
