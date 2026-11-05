"""Run on the server: cd ~/L1/MinervaQuantSystem && .venv/bin/python <this file>  (question of 2026-11-06).

A VIX-style 30-day volatility index of the 300ETF (510300) options from Sina real-time quotes (CBOE's model-free
variance on mid quotes of out-of-the-money options, the two nearest expiries interpolated to 30 days), checked
against the exchange's implied volatility of the near at-the-money contracts.  Proof of concept, 2026-11-06."""

import math
from datetime import datetime

import duckdb
import numpy as np
import pandas as pd
import requests

HEAD = {"Referer": "https://finance.sina.com.cn/", "User-Agent": "Mozilla/5.0"}
R = 0.015                    # risk-free rate, flat (its effect on the index is small)
UNDERLYING = "510300"
con = duckdb.connect("/home/van/L1/MinervaQuantSystem/data/market.duckdb", read_only=True)
chain = con.execute("""SELECT contract, kind, strike, expiry_date FROM option_contracts
                       WHERE underlying = ? AND expiry_date >= current_date AND NOT adjusted
                       ORDER BY expiry_date, strike, kind""", [UNDERLYING]).fetchdf()
expiries = sorted(chain["expiry_date"].unique())[:2]
chain = chain[chain["expiry_date"].isin(expiries)]
text = requests.get("https://hq.sinajs.cn/list=" + ",".join(f"CON_OP_{c}" for c in chain["contract"]),
                    headers=HEAD, timeout=15).content.decode("gbk", "replace")
quotes = {}
for part in text.split(";"):
    if "CON_OP_" not in part or '="' not in part:
        continue
    code = part.split("CON_OP_")[1].split("=")[0]
    f = part.split('"')[1].split(",")
    bid, ask, stamp = float(f[1] or 0), float(f[3] or 0), f[32]
    quotes[code] = (bid, ask, stamp)
chain["bid"] = chain["contract"].map(lambda c: quotes.get(c, (0, 0, ""))[0])
chain["ask"] = chain["contract"].map(lambda c: quotes.get(c, (0, 0, ""))[1])
stamp = next(iter(quotes.values()))[2]
now = datetime.fromisoformat(stamp)
chain["mid"] = np.where((chain["bid"] > 0) & (chain["ask"] > 0), (chain["bid"] + chain["ask"]) / 2, np.nan)


def variance(group: pd.DataFrame, expiry: pd.Timestamp) -> tuple[float, float, float]:
    t = (datetime.combine(expiry.date(), datetime.min.time()).replace(hour=15) - now).total_seconds() / (365 * 86400)
    calls = group[group["kind"] == "call"].set_index("strike")["mid"]
    puts = group[group["kind"] == "put"].set_index("strike")["mid"]
    both = pd.DataFrame({"c": calls, "p": puts}).dropna()
    k_star = (both["c"] - both["p"]).abs().idxmin()
    forward = k_star + math.exp(R * t) * (both.loc[k_star, "c"] - both.loc[k_star, "p"])
    strikes = sorted(set(calls.dropna().index) | set(puts.dropna().index))
    k0 = max(k for k in strikes if k <= forward)
    total = 0.0
    for i, k in enumerate(strikes):
        q = puts.get(k) if k < k0 else calls.get(k) if k > k0 else (calls.get(k) + puts.get(k)) / 2
        if q is None or np.isnan(q):
            continue
        dk = (strikes[i + 1] - strikes[i - 1]) / 2 if 0 < i < len(strikes) - 1 else \
            (strikes[1] - strikes[0] if i == 0 else strikes[-1] - strikes[-2])
        total += dk / k ** 2 * math.exp(R * t) * q
    return 2 / t * total - (forward / k0 - 1) ** 2 / t, t, forward


(v1, t1, f1), (v2, t2, f2) = (variance(chain[chain["expiry_date"] == e], pd.Timestamp(e)) for e in expiries)
n30, n365 = 30 / 365, 1.0
w1 = (t2 - n30) / (t2 - t1)
vix = 100 * math.sqrt((t1 * v1 * w1 + t2 * v2 * (1 - w1)) / n30)
print(f"quotes at {stamp}; expiries {[str(pd.Timestamp(e).date()) for e in expiries]}, T = {t1 * 365:.1f} / {t2 * 365:.1f} days")
print(f"forwards {f1:.4f} / {f2:.4f}; term variances → vol {100 * math.sqrt(v1):.2f} / {100 * math.sqrt(v2):.2f}")
print(f"300ETF 30-day volatility index (VIX method): {vix:.2f}")
risk = con.execute("""SELECT r.trade_date, c.kind, c.strike, r.iv FROM option_risk r JOIN option_contracts c USING (contract)
                      WHERE c.underlying = ? AND c.expiry_date = ? AND r.trade_date = (SELECT max(trade_date) FROM option_risk)
                      ORDER BY abs(c.strike - ?) LIMIT 4""", [UNDERLYING, expiries[0], f1]).fetchdf()
print("exchange IV of the near at-the-money contracts:", risk.to_dict("records"))
hist = con.execute("""SELECT r.trade_date, avg(r.iv) iv FROM option_risk r JOIN option_contracts c USING (contract)
                      WHERE c.underlying = ? AND r.trade_date >= DATE '2026-10-01'
                        AND c.expiry_date = (SELECT min(expiry_date) FROM option_contracts c2 WHERE c2.underlying = ? AND c2.expiry_date > r.trade_date + 7)
                        AND abs(c.strike / (SELECT close FROM etf_bars b WHERE b.symbol = ? AND b.trade_date = r.trade_date) - 1) < 0.03
                      GROUP BY 1 ORDER BY 1""", [UNDERLYING, UNDERLYING, UNDERLYING]).fetchdf()
print("near at-the-money exchange IV by day (Oct-Nov):", " ".join(f"{d:%m-%d}:{v:.3f}" for d, v in zip(hist["trade_date"], hist["iv"])))
