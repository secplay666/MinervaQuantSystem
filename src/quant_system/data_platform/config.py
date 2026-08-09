from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path


@dataclass(frozen=True)
class SymbolConfig:
    symbol: str
    name: str
    group: str


@dataclass(frozen=True)
class IndexConfig:
    symbol: str
    name: str


@dataclass(frozen=True)
class DataPlatformConfig:
    provider: str
    provider_version: str
    market: str
    start_date: str
    end_date: str
    request_pause_seconds: float
    max_retries: int
    symbols: tuple[SymbolConfig, ...]
    indices: tuple[IndexConfig, ...]

    @classmethod
    def load(cls, path: Path) -> "DataPlatformConfig":
        payload = json.loads(path.read_text(encoding="utf-8"))
        end_date = payload.get("end_date") or date.today().strftime("%Y%m%d")
        return cls(
            provider=payload["provider"],
            provider_version=payload["provider_version"],
            market=payload["market"],
            start_date=payload["start_date"],
            end_date=end_date,
            request_pause_seconds=float(payload.get("request_pause_seconds", 0.3)),
            max_retries=int(payload.get("max_retries", 3)),
            symbols=tuple(SymbolConfig(**item) for item in payload["symbols"]),
            indices=tuple(IndexConfig(**item) for item in payload.get("indices", [])),
        )

