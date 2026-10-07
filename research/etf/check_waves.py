"""Check 资金波段 on the dev env's catalog (read-only)."""

from pathlib import Path

from quant_system.app.etf import EtfQueries

root = Path.cwd()
queries = EtfQueries(root / "data" / "market.duckdb", root / "configs" / "etf" / "broad_groups.json")
for group in ("csi300", "all"):
    result = queries.waves(group)
    print(f"== {result['group']['name']}: {len(result['series'])} days, current {result['current']}")
    for wave in result["waves"]:
        print(f"  {wave['trade_date']} {wave['wave']:10s} counter={wave['counter']!s:5s} "
              f"pct {wave['wave_pct']:+.1%} window {wave['index_window'] or 0:+.1%} "
              f"after 5/20/60 {wave['after_5'] or 0:+.1%} {wave['after_20'] or 0:+.1%} {wave['after_60'] or 0:+.1%}")
    print("  base:", result["base"])
