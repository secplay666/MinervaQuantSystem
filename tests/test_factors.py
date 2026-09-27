"""Stage-3 factor library: rolling helpers, research panels, factors,
universe, processing and evaluation; point-in-time properties."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from research_fakes import build, synthetic_frames, truncate
from quant_system.backtest.view import LookAheadError, PanelView
from quant_system.features.context import FactorContext
from quant_system.features.processing import ProcessingSpec, neutralize, process_row, winsorize
from quant_system.features.registry import all_factors, compute, get
from quant_system.features.rolling import rolling_cov_corr, rolling_max_at, rolling_mean, rolling_std
from quant_system.features.store import FactorCache, build_factor_panel
from quant_system.universe.liquidity import UniverseSpec, pct_rank, universe_masks

SMALL_UNIVERSE = UniverseSpec(size=20, min_listing_sessions=60, coverage_window=20)


def _panel(seed: int = 0, n: int = 80, k: int = 5) -> np.ndarray:
    rng = np.random.default_rng(seed)
    x = rng.normal(size=(n, k))
    x[rng.random((n, k)) < 0.15] = np.nan
    return x


def test_rolling_helpers_match_pandas() -> None:
    x, y = _panel(1), _panel(2)
    frame_x, frame_y = pd.DataFrame(x), pd.DataFrame(y)
    # Before a full window our helpers return NaN by design (pandas returns
    # partial-window values once min_periods is met).
    assert np.isnan(rolling_mean(x, 10, 6)[:9]).all()
    np.testing.assert_allclose(rolling_mean(x, 10, 6)[9:], frame_x.rolling(10, min_periods=6).mean()[9:],
                               equal_nan=True)
    np.testing.assert_allclose(rolling_std(x, 10, 6)[9:], frame_x.rolling(10, min_periods=6).std()[9:],
                               equal_nan=True, atol=1e-12)
    corr = rolling_cov_corr(x, y, 12, 8)[1]
    expected = np.column_stack([frame_x[c].rolling(12, min_periods=8).corr(frame_y[c]) for c in frame_x])
    np.testing.assert_allclose(corr[11:], expected[11:], equal_nan=True, atol=1e-10)
    rows = np.array([3, 20, 79])
    maxima = rolling_max_at(x, rows, 10, 6)
    np.testing.assert_allclose(maxima, frame_x.rolling(10, min_periods=6).max().to_numpy()[rows], equal_nan=True)


def test_research_panels_are_point_in_time() -> None:
    market, research = synthetic_frames(3)
    sessions = sorted(market["calendar"]["trade_date"])
    symbol = sorted(market["master"]["symbol"])[0]
    shares = research["shares"]
    base = shares[(shares["symbol"] == symbol)].iloc[:1]
    changes = pd.DataFrame([
        # effective on its change date (announced before)
        {"symbol": symbol, "change_date": sessions[100], "notice_date": sessions[95], "total_shares": 2e9,
         "float_a_shares": 1e9, "record_key": "b"},
        # announced three sessions after it took effect: usable the session after the notice
        {"symbol": symbol, "change_date": sessions[150], "notice_date": sessions[153], "total_shares": 3e9,
         "float_a_shares": 2e9, "record_key": "c"},
    ])
    research = {**research, "shares": pd.concat([shares[shares["symbol"] != symbol], base, changes])}
    industry = research["industry"]
    research["industry"] = pd.concat([industry[industry["symbol"] != symbol], pd.DataFrame([
        {"symbol": symbol, "start_date": date(2014, 1, 2), "end_date": sessions[200], "l1_code": "110000",
         "l1_name": "甲"},
        {"symbol": symbol, "start_date": sessions[200], "end_date": None, "l1_code": "220000", "l1_name": "乙"}])])
    dividends = research["dividends"]
    research["dividends"] = pd.concat([dividends[dividends["symbol"] != symbol], pd.DataFrame([
        {"symbol": symbol, "report_date": date(2019, 12, 31), "ex_date": sessions[20], "record_date": sessions[19],
         "cash_per_10": 5.0, "total_shares": 1e9}])])
    data = build(market, research)
    j = list(data.symbols).index(symbol)
    assert data.total_shares[99, j] == base["total_shares"].item() and data.total_shares[100, j] == 2e9
    assert data.total_shares[153, j] == 2e9 and data.total_shares[154, j] == 3e9
    codes = data.industry_codes
    assert codes[data.industry[199, j]] == "110000" and codes[data.industry[200, j]] == "220000"
    # 0.5 CNY per share x shares at the ex session, for the following 365 days only
    assert data.dividend_ttm_cny[19, j] == 0 and data.dividend_ttm_cny[20, j] == pytest.approx(0.5 * data.total_shares[20, j])
    after_a_year = next(i for i, day in enumerate(data.sessions) if (day - data.sessions[20]).days >= 365)
    assert data.dividend_ttm_cny[after_a_year, j] == 0


def _aligned(full, part, rows_full, rows_part, values_full, values_part):
    columns = {s: j for j, s in enumerate(full.symbols)}
    mapping = np.array([columns[s] for s in part.symbols])
    return values_full[:, mapping], values_part


@settings(max_examples=6, deadline=None)
@given(seed=st.integers(0, 10_000), cut=st.integers(260, 310))
def test_factors_and_universe_ignore_the_future(seed: int, cut: int) -> None:
    market, research = synthetic_frames(seed)
    full = build(market, research)
    cut_day = full.sessions[cut]
    part = build(*truncate(market, research, cut_day))
    rows = np.array([cut - 40, cut - 20, cut])
    ctx_full, ctx_part = FactorContext(full), FactorContext(part)
    columns = {s: j for j, s in enumerate(full.symbols)}
    mapping = np.array([columns[s] for s in part.symbols])
    for spec in all_factors():
        if spec.requires:
            continue
        a = compute(spec, ctx_full, rows)[:, mapping]
        b = compute(spec, ctx_part, rows)
        np.testing.assert_allclose(a, b, equal_nan=True, rtol=1e-12, atol=1e-12, err_msg=spec.id)
    mask_full, _ = universe_masks(ctx_full, rows, SMALL_UNIVERSE)
    mask_part, _ = universe_masks(ctx_part, rows, SMALL_UNIVERSE)
    np.testing.assert_array_equal(mask_full[:, mapping], mask_part)


def test_every_factor_is_registered_with_a_known_family_and_computes() -> None:
    specs = all_factors()
    assert len(specs) >= 14 and len({s.id for s in specs}) == len(specs)
    data = build(*synthetic_frames(5))
    ctx = FactorContext(data)
    rows = np.array([250, 300])
    for spec in specs:
        if spec.requires:
            continue
        values = compute(spec, ctx, rows)
        assert np.isfinite(values).mean() > 0.3, spec.id


def test_momentum_factor_matches_the_definition() -> None:
    data = build(*synthetic_frames(7))
    view = PanelView(data.market, 300)
    expected = view.adjusted_price(20) / view.adjusted_price(240) - 1
    got = compute(get("mom_12_1"), FactorContext(data), np.array([300]))[0]
    finite = np.isfinite(got)
    np.testing.assert_allclose(got[finite], expected[finite])


def test_processing_winsorizes_neutralizes_and_standardizes() -> None:
    rng = np.random.default_rng(0)
    raw = rng.normal(size=200)
    raw[:3] = [50, -40, 30]
    industry = np.repeat(np.arange(4), 50).astype(np.int16)
    size = rng.normal(size=200)
    raw += industry * 2.0 + size * 0.5
    spec = get("vol_60")  # direction -1, neutralized on industry and size
    out = process_row(raw, np.ones(200, dtype=bool), industry, size, spec, ProcessingSpec())
    assert abs(out.mean()) < 1e-9 and abs(out.std() - 1) < 1e-9
    residual = -out  # direction flipped back
    for g in range(4):
        assert abs(residual[industry == g].mean()) < 1e-9
    assert abs(np.corrcoef(residual, size)[0, 1]) < 1e-9
    assert winsorize(raw, 3.0).max() < 50


def test_names_the_neutralization_fits_exactly_get_an_exact_zero_residual() -> None:
    # Rounding noise left there differs between CPUs (BLAS kernels) and would
    # decide the rank of those names, so rank IC would depend on the machine.
    rng = np.random.default_rng(1)
    x = rng.normal(size=60) * 1e-9  # tiny units (Amihud-like): the tolerance is relative
    industry = np.repeat(np.arange(6), 10).astype(np.int16)
    industry[[0, 1]] = [7, 8]       # alone in their industries
    x[[20, 21]] = x[20]             # a two-name industry with identical values
    industry[[20, 21]] = 9
    with_size = neutralize(x, industry, rng.normal(size=60))
    assert (with_size[[0, 1]] == 0.0).all() and (with_size[2:] != 0.0).all()
    industry_only = neutralize(x, industry, None)
    assert (industry_only[[0, 1, 20, 21]] == 0.0).all()
    assert np.count_nonzero(industry_only) == 56


def test_pct_rank_uses_average_ranks() -> None:
    np.testing.assert_allclose(pct_rank(np.array([3.0, 1.0, 1.0, np.nan, 2.0])), [1.0, 0.375, 0.375, np.nan, 0.75],
                               equal_nan=True)


def test_universe_takes_the_top_names_by_liquidity_and_size() -> None:
    data = build(*synthetic_frames(11))
    ctx = FactorContext(data)
    rows = np.array([200, 300])
    mask, diagnostics = universe_masks(ctx, rows, SMALL_UNIVERSE)
    assert (mask.sum(axis=1) <= 20).all() and (diagnostics["universe"] == mask.sum(axis=1)).all()
    st_column = list(data.symbols).index(data.symbols[1])
    assert not mask[0, st_column]  # the synthetic ST interval covers row 200


def test_factor_panel_serves_only_the_view_row(tmp_path) -> None:
    data = build(*synthetic_frames(13))
    ctx = FactorContext(data)
    rows = np.array([260, 280])
    universe, _ = universe_masks(ctx, rows, SMALL_UNIVERSE)
    cache = FactorCache(tmp_path)
    panel = build_factor_panel(data, rows, ["vol_60", "rev_1m"], universe, ProcessingSpec(min_names=5), cache, ctx)
    assert cache.misses == 2
    again = build_factor_panel(data, rows, ["vol_60", "rev_1m"], universe, ProcessingSpec(min_names=5),
                               FactorCache(tmp_path), FactorContext(data))
    np.testing.assert_array_equal(panel.values["vol_60"], again.values["vol_60"])
    values = panel.at(PanelView(data.market, 280))
    np.testing.assert_array_equal(values["vol_60"], panel.values["vol_60"][1])
    with pytest.raises(LookAheadError):
        panel.at(PanelView(data.market, 279))
    with pytest.raises(ValueError):
        values["vol_60"][0] = 1.0  # read-only


def _write_parquet_dir(directory, market: dict, research: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name, frame in {**market, **research}.items():
        frame.to_parquet(directory / f"{name}.parquet", index=False)


def test_factor_evaluation_run_records_artifacts_and_guards_the_oos_window(tmp_path) -> None:
    from quant_system.evaluation.factor_eval import EvaluationSpec, forward_returns
    from quant_system.research.experiments import ResearchConfig, run_factor_evaluation, schedule_rows
    from quant_system.research.registry import OutOfSampleAlreadyUsed, Registry
    from quant_system.domain.rules import MarketRules

    market, research = synthetic_frames(21)
    _write_parquet_dir(tmp_path / "fixture", market, research)
    payload = {
        "name": "synthetic", "experiment": "synthetic_v1",
        "data": {"source": "parquet_dir", "path": str(tmp_path / "fixture")},
        "market_rules": "configs/market_rules/cn_a_share.json",
        "universe": {"size": 20, "min_listing_sessions": 60, "coverage_window": 20},
        "processing": {"min_names": 5},
        "factors": ["vol_60", "rev_1m", "turn_20"],
        "samples": {"is": ["2020-04-01", "2020-12-31"], "oos": ["2021-01-01", "2021-03-31"]},
        "evaluation": {"horizons": [1, 2], "quantiles": 5, "min_names": 10},
    }
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    config = ResearchConfig.from_payload(payload, root)
    registry = Registry(tmp_path / "registry.sqlite")
    directory, results = run_factor_evaluation(config, "is", registry=registry, root=tmp_path,
                                               cache=FactorCache(None))
    assert {"summary.parquet", "series.parquet", "report.md", "manifest.json"} <= {p.name for p in directory.iterdir()}
    summary = results["summary"].set_index("factor")
    assert list(summary.index) == ["vol_60", "rev_1m", "turn_20"] and (summary["periods"] > 0).all()
    runs = registry.runs()
    assert runs["status"].tolist() == ["complete"] and runs["sample"].tolist() == ["IS"]
    # Every in-sample forward window ends inside the in-sample window.
    data = build(market, research)
    rows = schedule_rows(data.sessions, {"type": "month_end"})
    last_is = max(i for i, d in enumerate(data.sessions) if d <= config.samples["IS"][1])
    series = results["series"]
    for h in (1, 2):
        evaluated = series[series[f"n_h{h}"] > 0]["row"].unique()
        for t in evaluated:
            k = list(rows).index(t)
            assert rows[k + h] + 1 <= last_is
    with pytest.raises(PermissionError):
        run_factor_evaluation(config, "oos", registry=registry, root=tmp_path, cache=FactorCache(None))
    run_factor_evaluation(config, "oos", confirm_oos=True, reason="final", registry=registry, root=tmp_path,
                          cache=FactorCache(None))
    with pytest.raises(OutOfSampleAlreadyUsed):
        run_factor_evaluation(config, "oos", confirm_oos=True, registry=registry, root=tmp_path,
                              cache=FactorCache(None))
    # "all" and an explicit list hash differently once they differ.
    assert ResearchConfig.from_payload({**payload, "factors": "all"}, root).config_hash != config.config_hash


def test_perfect_foresight_factor_has_rank_ic_one() -> None:
    from quant_system.evaluation.factor_eval import EvaluationSpec, evaluate_factors, forward_returns
    from quant_system.features.store import FactorPanel
    from quant_system.research.experiments import schedule_rows
    from quant_system.domain.rules import MarketRules
    from pathlib import Path

    data = build(*synthetic_frames(23))
    ctx = FactorContext(data)
    rows = schedule_rows(data.sessions, {"type": "month_end"})
    spec = EvaluationSpec("IS", data.sessions[0], data.sessions[-1], horizons=(1,), quantiles=5, min_names=10)
    signals = rows[(rows > 60) & (rows < rows[-2])]
    universe, _ = universe_masks(ctx, signals, SMALL_UNIVERSE)
    rules = MarketRules.load(Path(__file__).resolve().parents[1] / "configs" / "market_rules" / "cn_a_share.json")
    forward = forward_returns(ctx, rules, rows, signals, universe, spec)
    oracle = np.where(universe, forward.returns[1], np.nan)
    panel = FactorPanel(rows=signals, symbols=data.symbols, values={"oracle": oracle}, universe=universe)
    results = evaluate_factors(panel, forward, spec, 12.0)
    series = results["series"].dropna(subset=["rank_ic_h1"])
    assert len(series) > 0 and np.allclose(series["rank_ic_h1"], 1.0)
    quantiles = results["quantiles"]
    assert (quantiles["q5"] >= quantiles["q1"]).all()


def test_context_prices_and_returns_have_the_right_units() -> None:
    market, research = synthetic_frames(29)
    data = build(market, research)
    ctx = FactorContext(data)
    bars = market["bars"].copy()
    factors = market["factors"].sort_values(["symbol", "effective_date"])
    symbol = sorted(bars["symbol"].unique())[0]  # k = 0: an ex-rights event in the window
    j = list(data.symbols).index(symbol)
    rows = bars[bars["symbol"] == symbol].sort_values("trade_date")
    hfq = pd.merge_asof(rows[["trade_date"]].assign(d=pd.to_datetime(rows["trade_date"])),
                        factors[factors["symbol"] == symbol].assign(d=pd.to_datetime(factors["effective_date"]))
                        [["d", "hfq_factor"]], on="d")["hfq_factor"].to_numpy()
    adjusted = rows["close"].to_numpy() * hfq
    expected = adjusted[1:] / adjusted[:-1] - 1
    index = [data.sessions.index(d) for d in rows["trade_date"]]
    got = ctx.returns()[index[1:], j]
    np.testing.assert_allclose(got, expected, rtol=1e-12)
    assert np.nanmax(np.abs(ctx.returns())) < 0.5  # daily returns, not price ratios
    # Previous close in today's price terms (yuan).
    np.testing.assert_allclose(ctx.reference_close()[index[1:], j], adjusted[:-1] / hfq[1:], rtol=1e-12)
    np.testing.assert_allclose(ctx.adj_last()[index, j], adjusted, rtol=1e-12)


def test_forward_returns_match_a_manual_open_to_open_calculation() -> None:
    from pathlib import Path as _Path

    from quant_system.domain.rules import MarketRules
    from quant_system.evaluation.factor_eval import EvaluationSpec, forward_returns
    from quant_system.research.experiments import schedule_rows

    market, research = synthetic_frames(31)
    data = build(market, research)
    ctx = FactorContext(data)
    rows = schedule_rows(data.sessions, {"type": "month_end"})
    signals = rows[(rows > 60) & (rows < rows[-2])][:3]
    universe = np.ones((len(signals), data.market.shape[1]), dtype=bool)
    rules = MarketRules.load(_Path(__file__).resolve().parents[1] / "configs" / "market_rules" / "cn_a_share.json")
    spec = EvaluationSpec("IS", data.sessions[0], data.sessions[-1], horizons=(1,), min_names=1)
    forward = forward_returns(ctx, rules, rows, signals, universe, spec)
    m = data.market
    k, t = 0, signals[0]
    entry, exit_ = t + 1, int(rows[list(rows).index(t) + 1]) + 1
    for j in np.flatnonzero(forward.investable[k] & m.has_bar[exit_])[:5]:
        manual = (m.open[exit_, j] / 100 * m.hfq[exit_, j]) / (m.open[entry, j] / 100 * m.hfq[entry, j]) - 1
        assert forward.returns[1][k, j] == pytest.approx(manual, rel=1e-12)
