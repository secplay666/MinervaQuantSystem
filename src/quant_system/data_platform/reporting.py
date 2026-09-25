from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from .quality import QualityIssue, issues_frame, quality_summary
from .storage import write_parquet_atomic
from .utils import json_dump

EXAMPLES_PER_RULE = 5


def write_quality_report(
    root: Path,
    run_id: str,
    issues: list[QualityIssue],
    datasets: dict[str, dict[str, object]],
    counters: dict[str, dict[str, int]],
    summaries: dict[str, Any],
) -> tuple[Path, Path, Path]:
    """Write a compact report: issues grouped by rule, full list as parquet."""
    directory = root / "data" / "reports" / f"run_id={run_id}"
    frame = issues_frame(issues)
    issues_path = write_parquet_atomic(frame, directory / "issues.parquet")
    grouped = _group(frame)
    summary = quality_summary(issues)
    payload = {
        "run_id": run_id,
        "summary": summary,
        "datasets": datasets,
        "counters": counters,
        "summaries": summaries,
        "issue_groups": grouped,
    }
    json_path = directory / "quality_report.json"
    json_dump(json_path, payload)

    lines = [
        f"# 数据质量报告 {run_id}",
        "",
        f"- 阻断问题：{summary['blocking']}",
        f"- 警告问题：{summary['warning']}",
        f"- 受影响记录：{summary['affected_rows']}",
        "",
        "## 数据集",
        "",
        "| 数据集 | 文件数 | 记录数 | 最早日期 | 最新日期 |",
        "|---|---:|---:|---|---|",
    ]
    for dataset, stats in sorted(datasets.items()):
        lines.append(
            f"| {dataset} | {stats.get('files', '')} | {stats.get('rows', 0)} | "
            f"{stats.get('min_date') or ''} | {stats.get('max_date') or ''} |"
        )
    lines.extend(["", "## 运行计数", ""])
    for step, values in sorted(counters.items()):
        rendered = ", ".join(f"{key}={value}" for key, value in sorted(values.items()))
        lines.append(f"- {step}: {rendered}")
    audit = summaries.get("audit") or {}
    if audit:
        lines.extend(["", "## 全量审计", ""])
        for key, value in audit.items():
            lines.append(f"- {key}: {value}")
    lines.extend(["", "## 问题（按规则汇总）", ""])
    if not grouped:
        lines.append("未发现数据质量问题。")
    else:
        lines.extend(["| 级别 | 数据集 | 规则 | 数量 | 影响记录 | 示例 |", "|---|---|---|---:|---:|---|"])
        for group in grouped:
            examples = "<br>".join(item.replace("|", "\\|") for item in group["examples"])
            lines.append(
                f"| {group['severity']} | {group['dataset']} | {group['rule']} | "
                f"{group['count']} | {group['affected_rows']} | {examples} |"
            )
        lines.extend(["", f"完整问题列表：`{issues_path.relative_to(root).as_posix()}`"])
    markdown_path = directory / "quality_report.md"
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, markdown_path, issues_path


def _group(frame: pd.DataFrame) -> list[dict[str, object]]:
    if frame.empty:
        return []
    groups = []
    order = {"blocking": 0, "warning": 1}
    for (severity, dataset, rule), group in frame.groupby(["severity", "dataset", "rule"], sort=False):
        groups.append(
            {
                "severity": severity,
                "dataset": dataset,
                "rule": rule,
                "count": int(len(group)),
                "affected_rows": int(group["affected_rows"].fillna(0).sum()),
                "examples": [str(message)[:300] for message in group["message"].head(EXAMPLES_PER_RULE)],
            }
        )
    return sorted(groups, key=lambda item: (order.get(str(item["severity"]), 9), str(item["dataset"]),
                                            str(item["rule"])))
