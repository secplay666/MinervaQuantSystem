"""Summarize ``probe_publication.py`` rounds: per source, when the day's data first appeared and whether its
numbers changed afterwards (compared with the last round).

    python timing_report.py ~/L1/research/timing/probes_2026-10-09.jsonl
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SKIP = {"seconds", "present"}


def numbers(entry: dict) -> dict:
    row = entry.get("row") or {k: v for k, v in entry.items() if k not in SKIP and k != "error"}
    return {k: v for k, v in row.items() if isinstance(v, (int, float))}


def main() -> None:
    rounds = [json.loads(line) for line in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines() if line.strip()]
    rounds.sort(key=lambda r: r["probe_at"])
    times = [r["probe_at"][5:16].replace("T", " ") for r in rounds]
    print(f"{len(rounds)} 轮：{times[0]} 至 {times[-1]}\n")
    groups: dict[str, set[str]] = {}
    for r in rounds:
        for group, items in r["items"].items():
            groups.setdefault(group, set()).update(items)
    print(f"{'数据源':<34}{'首次出现':<14}{'之后有变化':<10}说明")
    for group in groups:
        group_first = []
        for item in sorted(groups[group]):
            seen = [(t, r["items"].get(group, {}).get(item, {})) for t, r in zip(times, rounds)]
            first = next((t for t, e in seen if e.get("present")), None)
            errors = sum(1 for _, e in seen if e.get("error"))
            last = next((e for _, e in reversed(seen) if e.get("present")), None)
            changed = []
            if first is not None and last is not None:
                final = numbers(last)
                for t, e in seen:
                    if e.get("present") and numbers(e) != final:
                        diff = [k for k in final if numbers(e).get(k) != final[k]]
                        changed.append(f"{t[6:]}:{','.join(diff[:3])}")
            group_first.append(first)
            note = (f"错误 {errors} 次 " if errors else "") + ("；".join(changed[:3]) if changed else "")
            print(f"{group + ' ' + item:<34}{(first or '未出现'):<14}{'是' if changed else '否':<10}{note}")
        known = [f for f in group_first if f]
        print(f"  → {group}：全部出齐 {max(known) if len(known) == len(group_first) else '未全部出现'}\n")


if __name__ == "__main__":
    main()
