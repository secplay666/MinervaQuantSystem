"""Structure rules of the position manager (docs/design/position-manager.md §5-7).

A pure function of daily closes, the confirmed levels and the direction label
over time: ``replay`` walks the sessions from the levels' effective date and
returns every event (activation, entry, ladder reductions, exits, prompts)
with the simulated position after it, plus the state at the end.  The same
walk serves the daily evaluation (events on the latest session become
signals) and the history replay.

Prices are hfq-adjusted (stable across ex-dates); the API converts what the
user drew or typed.  Nothing here reads the clock or intraday data.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields, replace
from datetime import date

import numpy as np

# Direction labels (the user's; the stage view only suggests them).
RIGHT, TOP, BASE, LEFT, UNDECIDED = "right", "top", "base", "left", "undecided"
LABEL_NAMES = {RIGHT: "右侧", TOP: "顶部", BASE: "筑底", LEFT: "左侧", UNDECIDED: "未定"}
# Structure phases.
PENDING, ACTIVE, REALIZED, EXHAUSTED = "pending", "active", "realized", "exhausted"
PHASE_NAMES = {PENDING: "待突破", ACTIVE: "进行中", REALIZED: "已兑现", EXHAUSTED: "已衰竭"}


@dataclass(frozen=True)
class RuleParams:
    ladder: tuple[tuple[float, float], ...] = ((0.7, 0.7), (0.8, 0.5), (0.9, 0.25))  # completion -> remaining
    final_index_high: float = 0.10       # at the target, main index >= 90% or realized
    final_index_mid: float = 0.15        # main index 70-90%
    final_index_low: float = 0.20        # main index < 70% (or unknown)
    final_index_exhausted: float = 0.10  # main index structure exhausted
    trailing: float = 0.20               # exit on this drawdown from the highest close since entry
    trailing_after: float = 0.70         # ... once completion has reached this
    false_break: float = 0.95            # exit when a close falls below neckline x this (while holding)
    late_index: float = 0.90             # main index completion from which entries are half size
    late_weight: float = 0.5
    realized: float = 0.90               # peak completion that marks the structure realized
    exhausted: float = 0.15              # drawdown from the peak after that: exhausted (final)
    confirm_closes: int = 1              # closes above the neckline needed for an entry
    top_watch: float = 0.90              # completion from which to look for a top

    def to_dict(self) -> dict:
        values = asdict(self)
        values["ladder"] = [list(step) for step in self.ladder]
        return values

    @classmethod
    def from_dict(cls, values: dict | None) -> RuleParams:
        base = cls()
        clean: dict = {}
        for f in fields(cls):
            if not values or values.get(f.name) is None:
                continue
            if f.name == "ladder":
                clean["ladder"] = tuple((float(a), float(b)) for a, b in values["ladder"])
            else:
                clean[f.name] = type(getattr(base, f.name))(values[f.name])
        params = replace(base, **clean)
        params.validate()
        return params

    def validate(self) -> None:
        steps = list(self.ladder)
        if not steps or any(not (0 < c < 1.5 and 0 <= r <= 1) for c, r in steps):
            raise ValueError("减仓阶梯：完成度须在 0-150% 之间，剩余仓位须在 0-100% 之间")
        if [c for c, _ in steps] != sorted(c for c, _ in steps) or [r for _, r in steps] != sorted(
                (r for _, r in steps), reverse=True):
            raise ValueError("减仓阶梯须按完成度递增、剩余仓位递减")
        for name in ("final_index_high", "final_index_mid", "final_index_low", "final_index_exhausted",
                     "trailing", "trailing_after", "late_weight", "realized", "exhausted", "top_watch"):
            if not 0 <= getattr(self, name) <= 1.5:
                raise ValueError(f"参数 {name} 超出范围")
        if not (0.5 <= self.false_break <= 1 and 0.5 <= self.late_index <= 1.5 and 1 <= self.confirm_closes <= 5):
            raise ValueError("假突破系数 0.5-1、指数晚期阈值 50%-150%、确认根数 1-5")


@dataclass(frozen=True)
class Structure:
    """One round: the confirmed neckline and measured target (hfq prices)."""
    neckline: float
    target: float
    effective: date                 # the first session the rules scan
    version: int = 1                # changes when either level is re-confirmed
    top_neckline: float | None = None
    top_mode: str | None = None     # "observe" (alert on a close below) | "confirmed" (exit on top_confirmed)
    top_confirmed: date | None = None

    def completion(self, close: float) -> float:
        return (close - self.neckline) / (self.target - self.neckline)

    def price_at(self, completion: float) -> float:
        return self.neckline + completion * (self.target - self.neckline)


@dataclass(frozen=True)
class IndexDay:
    """The main index on one session, as the entry gate sees it."""
    label: str                      # the user's label for the index, else its stage view mapped to a label
    completion: float | None = None  # of the index's own structure, if the user drew one
    phase: str | None = None


@dataclass
class Event:
    trade_date: date
    rule: str
    priority: int
    message: str
    close: float
    weight: float                   # simulated position after the event (share of a full position)
    completion: float | None
    key: str                        # dedupe key within a structure version

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Outcome:
    events: list[Event]
    phase: str
    completion: float | None
    peak_close: float | None
    activated_on: date | None
    weight: float
    entry_weight: float
    waiting_for: str
    next_price: float | None
    next_step: str | None
    segments: list[dict] = field(default_factory=list)
    round_return: float | None = None
    script_return: float | None = None  # neckline -> target

    def to_dict(self) -> dict:
        values = asdict(self)
        values["events"] = [e.to_dict() for e in self.events]
        return values


def label_on(labels: list[tuple[date, str]], day: date) -> str:
    """The label in force on ``day`` from dated changes (sorted)."""
    current = UNDECIDED
    for since, label in labels:
        if since > day:
            break
        current = label
    return current


def final_remaining(index: IndexDay | None, p: RuleParams) -> float:
    if index is None or index.completion is None:
        return p.final_index_low
    if index.phase == EXHAUSTED:
        return p.final_index_exhausted
    if index.completion >= 0.9 or index.phase == REALIZED:
        return p.final_index_high
    if index.completion >= 0.7:
        return p.final_index_mid
    return p.final_index_low


def replay(dates: list[date], closes: np.ndarray, structure: Structure, labels: list[tuple[date, str]],
           index: dict[date, IndexDay] | None, p: RuleParams) -> Outcome:
    """Walk the sessions on or after ``structure.effective``."""
    events: list[Event] = []
    phase, peak, activated = PENDING, None, None
    weight = entry_weight = 0.0
    held_peak, held_max_completion = None, 0.0
    rungs_done: set[int] = set()
    target_done = watch_done = waiting_done = False
    can_enter = True  # a round ends with a trailing-stop or top exit; a false break may be re-entered
    above = 0
    segments: list[dict] = []
    segment: dict | None = None
    index = index or {}

    def emit(day, rule, priority, message, close, completion, key):
        events.append(Event(day, rule, priority, message, round(float(close), 4), round(weight, 4),
                            None if completion is None else round(float(completion), 4),
                            f"v{structure.version}:{key}"))

    def trade(day, close, new_weight, reason):
        nonlocal weight, segment
        if segment is None:
            return
        sold = weight - new_weight
        segment["proceeds"] += sold * close
        segment["steps"].append({"date": day, "close": close, "weight": new_weight, "reason": reason})
        weight = new_weight
        if weight <= 1e-9:
            segment["exit_date"], segment["exit_close"] = day, close
            segment["return"] = segment["proceeds"] / (segment["entry_weight"] * segment["entry_close"]) - 1
            segments.append(segment)
            segment = None

    for day, close in zip(dates, closes):
        if day < structure.effective or not np.isfinite(close):
            continue
        close = float(close)
        completion = structure.completion(close)
        # -- the structure's life (closes only, whether held or not) --------------------------------
        if phase == PENDING and close > structure.neckline:
            phase, activated, peak = ACTIVE, day, close
            emit(day, "activated", 4, "收盘站上颈线，结构激活", close, completion, "activated")
        elif phase in (ACTIVE, REALIZED):
            peak = max(peak, close)
            if phase == ACTIVE and structure.completion(peak) >= p.realized:
                phase = REALIZED
                emit(day, "realized", 4, f"峰值完成度达到 {p.realized:.0%}，结构已兑现", close, completion, "realized")
            elif phase == REALIZED and close <= peak * (1 - p.exhausted):
                phase = EXHAUSTED
                emit(day, "exhausted", 4, f"兑现后自峰值回撤 {p.exhausted:.0%}，结构已衰竭", close, completion,
                     "exhausted")
        label = label_on(labels, day)
        above = above + 1 if close > structure.neckline else 0
        if label in (LEFT, UNDECIDED, BASE):
            continue  # the label silences the rules (base criteria are evaluated separately)
        # -- exits (P1), then reductions (P2), while held ------------------------------------------
        if weight > 0:
            held_peak = max(held_peak, close)
            held_max_completion = max(held_max_completion, completion)
            if close < structure.neckline * p.false_break:
                trade(day, close, 0.0, "false_break")
                emit(day, "false_break", 1, f"收盘跌破颈线的 {p.false_break:.0%}（假突破），全部清仓", close, completion,
                     f"false_break:{day}")
                rungs_done, target_done, waiting_done = set(), False, False
                continue
            if structure.top_neckline is not None and structure.top_mode == "observe" \
                    and close < structure.top_neckline:
                can_enter = False
                trade(day, close, 0.0, "top_break")
                emit(day, "top_break", 1, "收盘跌破头部颈线，确认清仓", close, completion, "top_break")
                continue
            if structure.top_confirmed is not None and day >= structure.top_confirmed:
                can_enter = False
                trade(day, close, 0.0, "top_confirmed")
                emit(day, "top_confirmed", 1, "已确认头部，全部清仓", close, completion, "top_confirmed")
                continue
            if held_max_completion >= p.trailing_after and close <= held_peak * (1 - p.trailing):
                can_enter = False
                trade(day, close, 0.0, "trailing")
                emit(day, "trailing", 1, f"自持有期最高收盘回撤 {p.trailing:.0%}，移动止盈，全部清仓", close,
                     completion, "trailing")
                continue
            for k, (threshold, remaining) in enumerate(p.ladder):
                target_weight = remaining * entry_weight
                if completion >= threshold and k not in rungs_done and weight > target_weight + 1e-9:
                    rungs_done.add(k)
                    trade(day, close, target_weight, f"ladder_{k}")
                    emit(day, "ladder", 2, f"完成度 {completion:.0%}，减仓到 {remaining:.0%}", close, completion,
                         f"ladder:{k}")
            if completion >= 1.0 and not target_done:
                target_done = True
                remaining = final_remaining(index.get(day), p) * entry_weight
                if weight > remaining + 1e-9:
                    trade(day, close, remaining, "target")
                    emit(day, "target", 2, f"到达量度目标，减仓到 {remaining / entry_weight:.0%}（按主指数位置）", close,
                         completion, "target")
            if completion >= p.top_watch and not watch_done:
                watch_done = True
                emit(day, "top_watch", 4, "进入量度满足区，开始留意头部形态", close, completion, "top_watch")
            continue
        # -- entry (P3) ------------------------------------------------------------------------------
        if label != RIGHT or phase == EXHAUSTED or not can_enter or above < p.confirm_closes:
            continue
        gate = index.get(day)
        if gate is not None and gate.label != RIGHT:
            if not waiting_done:
                waiting_done = True
                emit(day, "waiting_index", 3, "个股已突破，主指数还不是右侧，等待共振", close, completion, "waiting_index")
            continue
        late = gate is not None and gate.completion is not None and (
            gate.completion >= p.late_index or gate.phase in (REALIZED, EXHAUSTED))
        entry_weight = p.late_weight if late else 1.0
        weight = entry_weight
        held_peak, held_max_completion = close, completion
        segment = {"entry_date": day, "entry_close": close, "entry_weight": entry_weight, "proceeds": 0.0,
                   "steps": []}
        note = "指数晚期·半仓" if late else ("指数完成度未知·满仓" if gate is None or gate.completion is None else "满仓")
        emit(day, "entry", 3, f"突破颈线且主指数右侧，入场（{note}）", close, completion, f"entry:{day}")

    last_close = next((float(c) for c in reversed(closes) if np.isfinite(c)), None)
    completion_now = structure.completion(last_close) if last_close is not None else None
    if segment is not None and last_close is not None:  # still open: value it at the last close
        open_seg = dict(segment, exit_date=None, exit_close=last_close)
        open_seg["return"] = (segment["proceeds"] + weight * last_close) / (
            segment["entry_weight"] * segment["entry_close"]) - 1
        segments.append(open_seg)
    round_return = float(np.prod([1 + s["return"] for s in segments]) - 1) if segments else None
    waiting, next_price, next_step = _waiting(phase, weight, entry_weight, completion_now, rungs_done, structure,
                                              label_on(labels, dates[-1]) if dates else UNDECIDED, p, last_close,
                                              waiting_done, can_enter)
    return Outcome(events, phase, completion_now, peak, activated, round(weight, 4), entry_weight, waiting,
                   next_price, next_step, segments, round_return, structure.target / structure.neckline - 1)


def _waiting(phase, weight, entry_weight, completion, rungs_done, s: Structure, label, p: RuleParams, close,
             waited, can_enter=True) -> tuple[str, float | None, str | None]:
    """What the item waits for, with the next trigger price."""
    if label == LEFT:
        return "左侧：规则休眠", None, None
    if label == UNDECIDED:
        return "未定：先确认方向标签", None, None
    if label == BASE:
        return "筑底：等形态和筑底判据", s.neckline, "站上颈线"
    if (phase == EXHAUSTED or not can_enter) and weight <= 0:
        return ("本轮结构已衰竭，等待下一轮" if phase == EXHAUSTED else "本轮已离场，等待下一轮结构"), None, None
    if weight <= 0:
        if close is not None and close > s.neckline:
            return ("已突破，等主指数右侧" if waited else "已突破，等入场条件"), s.neckline, "颈线"
        gap = None if close is None else s.neckline / close - 1
        return (f"等突破颈线（距 {gap:+.1%}）" if gap is not None else "等突破颈线"), s.neckline, "颈线"
    for k, (threshold, remaining) in enumerate(p.ladder):
        if k not in rungs_done:
            return f"持有 {weight / entry_weight:.0%}，下一档 {threshold:.0%} 减到 {remaining:.0%}", \
                s.price_at(threshold), f"完成度 {threshold:.0%}"
    return f"持有 {weight / entry_weight:.0%}，等目标或止盈", s.target, "量度目标"


def base_prompts(dates: list[date], closes: np.ndarray, opens: np.ndarray, volumes: np.ndarray,
                 labels: list[tuple[date, str]], neckline: float | None, stage_params) -> list[Event]:
    """Base criteria while the label is 筑底 (design §4): ① a close above the
    user's new neckline, ② a volume surge after a dry spell, ③ a retest that
    holds the low.  Each criterion prompts once per base period."""
    from .stages import retest_holds, volume_surge

    surge = volume_surge(closes, opens, volumes, stage_params)
    events: list[Event] = []
    period_start, done = None, set()
    for t, day in enumerate(dates):
        if label_on(labels, day) != BASE or not np.isfinite(closes[t]):
            period_start, done = None, set()
            continue
        if period_start is None:
            period_start = t
        close = float(closes[t])
        hits = []
        if neckline is not None and close > neckline:
            hits.append(("base_neckline", "收盘站上新画的颈线"))
        if surge[t]:
            hits.append(("base_volume", "缩量企稳后出现放量阳线"))
        if retest_holds(closes, period_start, t, stage_params):
            hits.append(("base_retest", "二次探底不破前低"))
        for rule, text in hits:
            if rule not in done:
                done.add(rule)
                events.append(Event(day, rule, 4, f"疑似筑底完成（{text}），请复核后再转右侧", round(close, 4), 0.0,
                                    None, f"{rule}:{dates[period_start]}"))
    return events


def phases(dates: list[date], closes: np.ndarray, structure: Structure, p: RuleParams) -> dict[date, tuple[str, float]]:
    """(phase, completion) of a structure on each session from its effective date:
    the main index's position for the entry gate and the final reduction."""
    phase, peak = PENDING, None
    out: dict[date, tuple[str, float]] = {}
    for day, close in zip(dates, closes):
        if day < structure.effective or not np.isfinite(close):
            continue
        close = float(close)
        if phase == PENDING and close > structure.neckline:
            phase, peak = ACTIVE, close
        elif phase in (ACTIVE, REALIZED):
            peak = max(peak, close)
            if phase == ACTIVE and structure.completion(peak) >= p.realized:
                phase = REALIZED
            elif phase == REALIZED and close <= peak * (1 - p.exhausted):
                phase = EXHAUSTED
        out[day] = (phase, structure.completion(close))
    return out
