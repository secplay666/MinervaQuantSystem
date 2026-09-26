from __future__ import annotations

import argparse
import logging
from pathlib import Path

from .config import BacktestConfig
from .runner import run_backtest, write_artifacts


def _root_from_file() -> Path:
    return Path(__file__).resolve().parents[3]


def command_run(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    config = BacktestConfig.load(Path(args.config).resolve(), root)
    result, summary, values = run_backtest(config)
    directory = write_artifacts(root, result, summary, values)
    strategy = summary["performance"]["策略"]
    print(f"Backtest {config.name}: {strategy.get('start')} .. {strategy.get('end')}")
    for name, stats in summary["performance"].items():
        print(f"  {name}: total {stats.get('total_return', float('nan')):.2%}  cagr {stats.get('cagr', float('nan')):.2%}"
              f"  maxdd {stats.get('max_drawdown', float('nan')):.2%}  sharpe {stats.get('sharpe', float('nan')):.2f}")
    print(f"  fills {result.counters.get('fills', 0)}, orders {result.counters.get('orders', 0)}, "
          f"timings {summary['timings']}")
    print(f"Artifacts: {directory}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Quant backtest CLI")
    parser.add_argument("--root", default=str(_root_from_file()))
    parser.add_argument("--verbose", action="store_true")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run", help="run a backtest from a strategy config")
    run.add_argument("--config", required=True)
    run.set_defaults(handler=command_run)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s - %(message)s")
    raise SystemExit(args.handler(args))


if __name__ == "__main__":
    main()
