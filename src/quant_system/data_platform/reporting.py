from __future__ import annotations

from pathlib import Path

from .quality import QualityIssue, quality_summary
from .utils import json_dump


def write_quality_report(
    root: Path,
    run_id: str,
    issues: list[QualityIssue],
    dataset_stats: dict[str, dict[str, object]],
) -> tuple[Path, Path]:
    directory = root / "data" / "reports" / f"run_id={run_id}"
    payload = {
        "run_id": run_id,
        "summary": quality_summary(issues),
        "datasets": dataset_stats,
        "issues": [issue.to_dict() for issue in issues],
    }
    json_path = directory / "quality_report.json"
    json_dump(json_path, payload)
    summary = payload["summary"]
    lines = [
        f"# 数据质量报告 {run_id}",
        "",
        f"- 阻断问题：{summary['blocking']}",
        f"- 警告问题：{summary['warning']}",
        f"- 受影响记录：{summary['affected_rows']}",
        "",
        "## 数据集统计",
        "",
        "| 数据集 | 记录数 | 最早日期 | 最新日期 |",
        "|---|---:|---|---|",
    ]
    for dataset, stats in sorted(dataset_stats.items()):
        lines.append(
            f"| {dataset} | {stats.get('rows', 0)} | "
            f"{stats.get('min_date', '')} | {stats.get('max_date', '')} |"
        )
    lines.extend(["", "## 问题", ""])
    if not issues:
        lines.append("未发现数据质量问题。")
    else:
        lines.extend(
            [
                "| 级别 | 数据集 | 规则 | 说明 | 影响记录 |",
                "|---|---|---|---|---:|",
            ]
        )
        for issue in issues:
            lines.append(
                f"| {issue.severity} | {issue.dataset} | {issue.rule} | "
                f"{issue.message} | {issue.affected_rows} |"
            )
    markdown_path = directory / "quality_report.md"
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, markdown_path

