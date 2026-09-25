"""Security code conventions shared by providers, normalization and quality rules."""

from __future__ import annotations

import re
import unicodedata

SSE_MAIN = "SSE_MAIN"
STAR = "STAR"
SZSE_MAIN = "SZSE_MAIN"
CHINEXT = "CHINEXT"
BSE = "BSE"

_SSE_A_PREFIXES = ("600", "601", "603", "605", "688", "689")
_SZSE_A_PREFIXES = ("000", "001", "002", "003", "300", "301", "302")
_BSE_PREFIXES = ("920", "43", "83", "87", "88")


def infer_exchange(symbol: str) -> str:
    if symbol.startswith(("900", "5", "6")):
        return "SSE"
    if symbol.startswith(("0", "1", "2", "3")):
        return "SZSE"
    if symbol.startswith(("4", "8", "9")):
        return "BSE"
    return "UNKNOWN"


def market_prefix(symbol: str) -> str:
    """Return the sh/sz/bj-prefixed code used by Sina and Tencent endpoints."""
    if symbol.startswith(("sh", "sz", "bj")):
        return symbol
    exchange = infer_exchange(symbol)
    return {"SSE": "sh", "SZSE": "sz", "BSE": "bj"}.get(exchange, "sz") + symbol


def is_a_share(symbol: str) -> bool:
    return symbol.startswith(_SSE_A_PREFIXES + _SZSE_A_PREFIXES + _BSE_PREFIXES)


def infer_board(symbol: str) -> str:
    if symbol.startswith(("688", "689")):
        return STAR
    if symbol.startswith(_SSE_A_PREFIXES):
        return SSE_MAIN
    if symbol.startswith(("300", "301", "302")):
        return CHINEXT
    if symbol.startswith(_SZSE_A_PREFIXES):
        return SZSE_MAIN
    if symbol.startswith(_BSE_PREFIXES):
        return BSE
    return "UNKNOWN"


def security_type(symbol: str) -> str:
    return "CDR" if symbol.startswith("689") else "A_SHARE"


def instrument_id(symbol: str) -> str:
    return f"CN.{infer_exchange(symbol)}.{symbol}"


def clean_name(name: object) -> str:
    """Normalize exchange names: full-width letters, embedded spaces."""
    text = unicodedata.normalize("NFKC", str(name))
    return re.sub(r"\s+", "", text)


# Day-scoped prefixes: ex-dividend (XD/XR/DR) and new-listing (N/C) markers.
_TRANSIENT_PREFIX = re.compile(r"^(?:XD|XR|DR|N|C)")


def risk_status_from_name(name: object) -> str | None:
    """Infer the risk-warning state encoded in a security short name.

    Returns ``"*ST"`` (delisting-risk warning), ``"ST"`` (other risk warning),
    ``"DELISTING"`` (delisting consolidation period) or ``None``.  Share-reform
    variants (``S*ST``/``SST``) map to their base state.
    """
    text = clean_name(name).upper()
    if text.endswith("退") or text.startswith("退市"):
        return "DELISTING"
    for candidate in (text, _TRANSIENT_PREFIX.sub("", text, count=1)):
        if re.match(r"^S?\*ST", candidate):
            return "*ST"
        if re.match(r"^S?ST", candidate):
            return "ST"
    return None
