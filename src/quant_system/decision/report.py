"""Decision report per run: report.md, targets.csv, intents.csv, manifest.json.

report.md and the CSVs contain no timestamps, so rerunning the same inputs
reproduces them byte for byte; run metadata (created_at, timings) lives in
manifest.json and the database.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from ..data_platform.utils import json_dump
from .gates import GateResult
from .intents import IntentDraft
from .monitor import Alert

DECISION_LABEL = {"pass": "通过", "warn": "警告", "reject": "拒绝"}
KIND_LABEL = {"rebalance": "调仓日", "monitor": "监控日", "forced": "强制调仓"}


def _yuan(fen: int | None) -> str:
    return "" if fen is None else f"{fen / 100:,.2f}"


def write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.write_text(frame.to_csv(index=False, lineterminator="\n"), encoding="utf-8", newline="")


def write_report(directory: Path, *, header: dict[str, Any], gates: list[GateResult], alerts: list[Alert],
                 targets: list[dict[str, Any]], intents: list[IntentDraft], manifest: dict[str, Any]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    lines = [f"# 决策报告 {header['account_name']} · {header['trade_date']}", "",
             f"- 运行：`{header['run_id']}`（{KIND_LABEL.get(header['kind'], header['kind'])}，"
             f"状态 {header['status']}）",
             f"- 执行日：{header.get('next_session') or '—'}；审核截止：{header.get('valid_until') or '—'}",
             f"- 账户净值：{_yuan(header.get('nav_fen'))} 元；现金：{_yuan(header.get('cash_fen'))} 元；"
             f"持仓 {header.get('positions', 0)} 只",
             f"- 数据：采集 `{header.get('ingest_run_id')}`，数据版本 `{str(header.get('data_version'))[:16]}`", ""]
    lines += ["## 闸门", "", "| 闸门 | 结果 | 说明 |", "|---|---|---|"]
    lines += [f"| {g.gate} | {'通过' if g.passed else '**阻断**'} | {g.message}"
              f"{'；' + g.hint if g.hint and not g.passed else ''} |" for g in gates]
    if intents:
        buys = [d for d in intents if d.side == "buy"]
        sells = [d for d in intents if d.side == "sell"]
        lines += ["", "## 交易清单", "",
                  f"卖出 {len(sells)} 笔，约 {_yuan(sum(d.est_notional_fen for d in sells))} 元；"
                  f"买入 {len(buys)} 笔，约 {_yuan(sum(d.est_notional_fen for d in buys))} 元；"
                  f"预计费用 {_yuan(sum(d.est_fees_fen for d in intents))} 元。", "",
                  "| # | 代码 | 方向 | 数量 | 参考价 | 次日涨停 | 次日跌停 | 金额 | 原因 | 风险 |",
                  "|---|---|---|---:|---:|---:|---:|---:|---|---|"]
        for d in intents:
            notes = "；".join(f"{c.rule_id} {c.message}" for c in d.checks)
            lines.append(f"| {d.seq} | {d.symbol} | {'买入' if d.side == 'buy' else '卖出'} | {d.qty} | "
                         f"{_yuan(d.ref_price_fen)} | {_yuan(d.limit_up_fen)} | {_yuan(d.limit_down_fen)} | "
                         f"{_yuan(d.est_notional_fen)} | {d.reason} | {DECISION_LABEL[d.risk]}"
                         f"{'：' + notes if notes else ''} |")
    if targets:
        lines += ["", f"## 目标组合（{len(targets)} 只）", "", "| 排名 | 代码 | 权重 | 目标数量 | 得分 | 行业 |",
                  "|---:|---|---:|---:|---:|---|"]
        for t in targets:
            score = "" if t["score"] is None else f"{t['score']:.4f}"
            lines.append(f"| {t['rank'] or ''} | {t['symbol']} | {t['target_weight']:.4f} | {t['target_qty'] or ''} | "
                         f"{score} | {t['explanation'].get('industry', '')} |")
    lines += ["", "## 持仓提示", ""]
    lines += [f"- [{a.level}] {a.title}：{a.body}{'（' + a.hint + '）' if a.hint else ''}" for a in alerts] or ["- 无"]
    (directory / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")

    write_csv(pd.DataFrame([{"seq": d.seq, "symbol": d.symbol, "side": d.side, "qty": d.qty, "rank": d.rank,
                             "reason": d.reason, "ref_price_fen": d.ref_price_fen, "limit_up_fen": d.limit_up_fen,
                             "limit_down_fen": d.limit_down_fen, "est_notional_fen": d.est_notional_fen,
                             "est_fees_fen": d.est_fees_fen, "risk": d.risk,
                             "checks": ";".join(f"{c.rule_id}:{c.decision}" for c in d.checks)}
                            for d in intents],
                           columns=["seq", "symbol", "side", "qty", "rank", "reason", "ref_price_fen",
                                    "limit_up_fen", "limit_down_fen", "est_notional_fen", "est_fees_fen", "risk",
                                    "checks"]),
              directory / "intents.csv")
    write_csv(pd.DataFrame([{k: t[k] for k in ("rank", "symbol", "target_weight", "target_qty", "score")}
                            | {"industry": t["explanation"].get("industry")} for t in targets],
                           columns=["rank", "symbol", "target_weight", "target_qty", "score", "industry"]),
              directory / "targets.csv")
    json_dump(directory / "manifest.json", manifest)
