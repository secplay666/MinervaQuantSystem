"""``quant-research``: factor evaluation and experiments (ADR-009)."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from ..features.registry import all_factors
from .experiments import ROOT, ResearchConfig, run_factor_evaluation
from .registry import Registry


def command_factors_list(args: argparse.Namespace) -> int:
    for spec in all_factors():
        direction = "+" if spec.direction > 0 else "-"
        print(f"{spec.family:<10} {spec.id:<16} {direction} window={spec.window:<4} {spec.description}")
    return 0


def command_factors_evaluate(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    config = ResearchConfig.load(Path(args.config).resolve(), root)
    directory, results = run_factor_evaluation(config, args.sample, confirm_oos=args.confirm_oos,
                                               reason=args.reason or "", force=args.force, root=root)
    summary = results["summary"].sort_values("rank_ic_t", ascending=False)
    print(f"Factor evaluation {config.name} [{args.sample.upper()}]: {len(summary)} factors")
    for row in summary.itertuples(index=False):
        mark = "*" if row.significant else " "
        print(f" {mark} {row.factor:<16} rank IC {row.rank_ic_mean:+.4f}  t {row.rank_ic_t:+.2f}  "
              f"top-minus-universe {getattr(row, 'top_minus_universe_ann', float('nan')):+.2%}/yr")
    print(f"Artifacts: {directory}")
    return 0


def command_runs(args: argparse.Namespace) -> int:
    registry = Registry(Path(args.root).resolve() / "artifacts" / "registry.sqlite")
    runs = registry.runs()
    columns = ["run_id", "experiment_id", "kind", "sample", "status", "config_hash", "started_at"]
    print(runs[columns].tail(args.limit).to_string(index=False) if not runs.empty else "no runs")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Quant research CLI (factors, experiments)")
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--verbose", action="store_true")
    commands = parser.add_subparsers(dest="command", required=True)
    factors = commands.add_parser("factors", help="factor library")
    factor_commands = factors.add_subparsers(dest="factor_command", required=True)
    factor_commands.add_parser("list", help="list registered factors").set_defaults(handler=command_factors_list)
    evaluate = factor_commands.add_parser("evaluate", help="evaluate factors on one sample window")
    evaluate.add_argument("--config", required=True)
    evaluate.add_argument("--sample", default="is", choices=("is", "oos", "IS", "OOS"))
    evaluate.add_argument("--confirm-oos", action="store_true",
                          help="required for the out-of-sample window (allowed once per config)")
    evaluate.add_argument("--reason", help="why the out-of-sample window is opened (recorded)")
    evaluate.add_argument("--force", action="store_true", help="repeat an out-of-sample run (needs --reason)")
    evaluate.set_defaults(handler=command_factors_evaluate)
    runs = commands.add_parser("runs", help="list registered research runs")
    runs.add_argument("--limit", type=int, default=30)
    runs.set_defaults(handler=command_runs)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s - %(message)s")
    raise SystemExit(args.handler(args))


if __name__ == "__main__":
    main()
