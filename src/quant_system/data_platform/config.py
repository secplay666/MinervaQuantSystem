from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .utils import json_hash


@dataclass(frozen=True)
class SymbolConfig:
    symbol: str
    name: str
    group: str


@dataclass(frozen=True)
class IndexConfig:
    symbol: str
    name: str
    source: str = "tencent"  # tencent (price indices) | csindex (e.g. total-return H00300)
    kind: str = "price"      # price | total_return

    def __post_init__(self) -> None:
        if self.source not in {"tencent", "csindex"}:
            raise ValueError(f"index {self.symbol}: unknown source {self.source!r}")
        if self.kind not in {"price", "total_return"}:
            raise ValueError(f"index {self.symbol}: unknown kind {self.kind!r}")


@dataclass(frozen=True)
class ManualDelisting:
    """A delisting that no exchange endpoint reports (e.g. BSE)."""

    symbol: str
    name: str
    list_date: str | None
    delist_date: str
    source: str


@dataclass(frozen=True)
class DataPlatformConfig:
    provider: str
    provider_version: str
    market: str
    start_date: str
    index_start_date: str
    end_date: str | None
    request_pause_seconds: float
    max_retries: int
    max_workers: int
    daily_universe: str
    download_adjustment_factors: bool
    symbols: tuple[SymbolConfig, ...]
    indices: tuple[IndexConfig, ...]
    http_timeout_seconds: float = 30.0
    session_final_time: str = "16:00"
    overlap_sessions: int = 3
    min_latest_coverage: float = 0.98
    min_factor_success_ratio: float = 0.99
    max_listing_shrink_ratio: float = 0.02
    download_status_history: bool = True
    suspension_backfill_start: str = "20230103"
    manual_delistings: tuple[ManualDelisting, ...] = field(default_factory=tuple)
    download_corporate: bool = True
    download_classification: bool = True
    download_fundamentals: bool = True
    download_etf: bool = True
    etf_groups_path: str = "configs/etf/broad_groups.json"
    etf_sse_start: str = "20150101"
    etf_szse_start: str = "20160101"
    etf_sse_max_dates_per_run: int = 30
    etf_holder_reports_per_run: int = 200
    sw_mapping_path: str = "configs/industry/sw2014_to_sw2021_l1.json"
    index_weight_symbols: tuple[str, ...] = ("000300", "000905", "000852")
    download_intraday: bool = True
    download_sw_indices: bool = True  # the 31 SW L1 industry indices (sw_index.py), with the index step
    download_company_actions: bool = True  # buybacks and holder increases/decreases (company_actions.py)
    sw_index_start: str = "20050101"
    # 1-minute bars of these codes, plus 3-second trades of the funds among them (intraday.py)
    intraday_codes: tuple[str, ...] = ("sh510300", "sh510310", "sh510330", "sz159919", "sh515330", "sh510360",
                                       "sh515380", "sh510350", "sh000300", "IF0")
    config_hash: str = ""

    @classmethod
    def load(cls, path: Path) -> "DataPlatformConfig":
        payload = json.loads(path.read_text(encoding="utf-8"))
        daily_universe = str(payload.get("daily_universe", "configured"))
        if daily_universe not in {"configured", "all_a_share"}:
            raise ValueError(
                "daily_universe must be one of: configured, all_a_share"
            )
        return cls(
            provider=payload["provider"],
            provider_version=payload["provider_version"],
            market=payload["market"],
            start_date=payload["start_date"],
            index_start_date=payload.get(
                "index_start_date", payload["start_date"]
            ),
            # None means "latest final session"; resolved against the calendar
            # and the Asia/Shanghai clock at run time, not at load time.
            end_date=payload.get("end_date") or None,
            request_pause_seconds=float(payload.get("request_pause_seconds", 0.3)),
            max_retries=int(payload.get("max_retries", 3)),
            max_workers=max(1, int(payload.get("max_workers", 4))),
            daily_universe=daily_universe,
            download_adjustment_factors=bool(
                payload.get("download_adjustment_factors", False)
            ),
            symbols=tuple(SymbolConfig(**item) for item in payload["symbols"]),
            indices=tuple(IndexConfig(**item) for item in payload.get("indices", [])),
            http_timeout_seconds=float(payload.get("http_timeout_seconds", 30.0)),
            session_final_time=str(payload.get("session_final_time", "16:00")),
            overlap_sessions=max(0, int(payload.get("overlap_sessions", 3))),
            min_latest_coverage=float(payload.get("min_latest_coverage", 0.98)),
            min_factor_success_ratio=float(
                payload.get("min_factor_success_ratio", 0.99)
            ),
            max_listing_shrink_ratio=float(
                payload.get("max_listing_shrink_ratio", 0.02)
            ),
            download_status_history=bool(
                payload.get("download_status_history", True)
            ),
            suspension_backfill_start=str(
                payload.get("suspension_backfill_start", "20230103")
            ),
            manual_delistings=tuple(
                ManualDelisting(
                    symbol=str(item["symbol"]),
                    name=str(item["name"]),
                    list_date=item.get("list_date"),
                    delist_date=str(item["delist_date"]),
                    source=str(item.get("source", "manual")),
                )
                for item in payload.get("manual_delistings", [])
            ),
            download_corporate=bool(payload.get("download_corporate", True)),
            download_classification=bool(payload.get("download_classification", True)),
            download_fundamentals=bool(payload.get("download_fundamentals", True)),
            download_etf=bool(payload.get("download_etf", True)),
            etf_groups_path=str(payload.get("etf_groups_path", "configs/etf/broad_groups.json")),
            etf_sse_start=str(payload.get("etf_sse_start", "20150101")),
            etf_szse_start=str(payload.get("etf_szse_start", "20160101")),
            etf_sse_max_dates_per_run=max(1, int(payload.get("etf_sse_max_dates_per_run", 30))),
            etf_holder_reports_per_run=max(0, int(payload.get("etf_holder_reports_per_run", 200))),
            sw_mapping_path=str(payload.get("sw_mapping_path", "configs/industry/sw2014_to_sw2021_l1.json")),
            index_weight_symbols=tuple(str(item) for item in payload.get("index_weight_symbols",
                                                                         ("000300", "000905", "000852"))),
            download_intraday=bool(payload.get("download_intraday", True)),
            download_sw_indices=bool(payload.get("download_sw_indices", True)),
            download_company_actions=bool(payload.get("download_company_actions", True)),
            sw_index_start=str(payload.get("sw_index_start", "20050101")),
            intraday_codes=tuple(str(item) for item in payload.get("intraday_codes", cls.intraday_codes)),
            # Hash the file payload, not runtime-resolved values, so an
            # unchanged config keeps the same hash across days.
            config_hash=json_hash(payload),
        )
