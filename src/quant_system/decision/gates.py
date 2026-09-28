"""Quality gates of a decision run (design §2.1, ADR-008 §10).

Any failed gate blocks the run: no order intents are produced, only an event
that says why and what to do.  Each gate returns a structured result that is
stored with the run.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from statistics import median
from typing import Any

from .inputs import IngestRecord

SIGNAL_TOLERANCE = 0.20      # G3: allowed deviation from the recent median
SIGNAL_HISTORY = 6           # G3: rebalance rows in the median
WEIGHT_SUM_RANGE = (0.90, 1.0 + 1e-9)  # G5
HOLDINGS_TOLERANCE = 0.10    # G5: |names - n_holdings| / n_holdings


@dataclass(frozen=True)
class GateResult:
    gate: str
    passed: bool
    message: str
    detail: dict[str, Any] = field(default_factory=dict)
    hint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def g1_ingest(record: IngestRecord | None, session: date) -> GateResult:
    if record is None:
        return GateResult("G1", False, "没有找到采集运行记录", hint="先运行每日采集")
    detail = {"run_id": record.run_id, "status": record.status,
              "expected_latest": record.expected_latest.isoformat() if record.expected_latest else None,
              "session": session.isoformat()}
    if record.status != "complete":
        return GateResult("G1", False, f"最近一次采集 {record.run_id} 的状态为 {record.status}", detail,
                          "数据未更新或采集不完整，请查看数据健康页和质量报告")
    if record.expected_latest != session:
        return GateResult("G1", False, f"最近一次采集的最新交易日 {record.expected_latest} 不等于决策日 {session}",
                          detail, "数据未更新到决策日，等待采集完成或补跑")
    return GateResult("G1", True, "采集完整，交易日一致", detail)


def g2_data_version(record: IngestRecord, market_version: str | None) -> GateResult:
    detail = {"ingest": record.data_version, "market": market_version}
    if not market_version or market_version != record.data_version:
        return GateResult("G2", False, "行情库的数据版本与采集记录不一致", detail,
                          "采集后数据被改动（例如重建），确认后重新运行 catalog 与决策")
    return GateResult("G2", True, "数据版本一致", detail)


def g3_signal_stability(now: dict[str, int], history: list[dict[str, int]]) -> GateResult:
    """``now``/``history``: counts such as {"universe": n, "scored": m} per rebalance row."""
    recent = history[-SIGNAL_HISTORY:]
    detail: dict[str, Any] = {"now": now, "history_rows": len(recent)}
    if len(recent) < 3:
        return GateResult("G3", True, "历史调仓期不足，跳过突变检查", detail)
    failures = []
    for key, value in now.items():
        base = median(row[key] for row in recent)
        detail[f"median_{key}"] = base
        if base > 0 and abs(value - base) / base > SIGNAL_TOLERANCE:
            failures.append(f"{key} {value} 偏离近期中位数 {base:g} 超过 {SIGNAL_TOLERANCE:.0%}")
    if failures:
        return GateResult("G3", False, "信号数量突变：" + "；".join(failures), detail,
                          "检查当日数据是否缺失（停牌、采集失败）后再决定是否强制运行")
    return GateResult("G3", True, "信号数量稳定", detail)


def g4_holdings(mode: str, confirmed: date | None, unfilled: list[str], session: date) -> GateResult:
    """Manual accounts: holdings must be confirmed after the last approved
    execution, otherwise the next target is built on stale holdings."""
    detail = {"mode": mode, "confirmed": confirmed.isoformat() if confirmed else None, "unfilled": unfilled[:20]}
    if mode != "manual":
        return GateResult("G4", True, "模拟账户的持仓由系统维护", detail)
    if confirmed is None:
        return GateResult("G4", False, "手工账户还没有录入过持仓", detail, "先录入当前持仓和现金")
    if unfilled:
        return GateResult("G4", False, f"{len(unfilled)} 条已批准的交易意图没有回填成交，且之后没有确认过持仓",
                          detail, "回填成交或重新录入最新持仓")
    return GateResult("G4", True, "持仓已确认", detail)


def g5_target(weights: dict[str, float], n_holdings: int | None) -> GateResult:
    total = sum(weights.values())
    names = len(weights)
    detail = {"weight_sum": round(total, 6), "names": names, "n_holdings": n_holdings}
    low, high = WEIGHT_SUM_RANGE
    problems = []
    if not low <= total <= high:
        problems.append(f"权重之和 {total:.4f} 不在 [{low:.2f}, 1.00]")
    if n_holdings and abs(names - n_holdings) / n_holdings > HOLDINGS_TOLERANCE:
        problems.append(f"持股数 {names} 与目标 {n_holdings} 偏离超过 {HOLDINGS_TOLERANCE:.0%}")
    if problems:
        return GateResult("G5", False, "目标组合异常：" + "；".join(problems), detail,
                          "检查股票池和组合约束是否因数据问题收缩")
    return GateResult("G5", True, "目标组合正常", detail)
