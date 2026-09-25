from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def utc_now() -> datetime:
    return datetime.now(UTC)


def utc_now_iso() -> str:
    return utc_now().isoformat()


def make_run_id() -> str:
    return utc_now().strftime("%Y%m%dT%H%M%SZ")


def run_id_to_iso(run_id: str) -> str:
    return datetime.strptime(run_id, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC).isoformat()


def ensure_directories(root: Path) -> None:
    for relative in (
        "data/raw",
        "data/canonical",
        "data/manifests",
        "data/reports",
        "data/checkpoints",
    ):
        (root / relative).mkdir(parents=True, exist_ok=True)


def json_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def json_dump(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    temp_path.replace(path)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def code_version(root: Path) -> dict[str, object]:
    """Git commit of the running code, and whether the tree had local edits."""
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain", "--untracked-files=no"],
                cwd=root,
                capture_output=True,
                text=True,
                check=True,
                timeout=10,
            ).stdout.strip()
        )
    except (OSError, subprocess.SubprocessError):
        return {"git_sha": "unknown", "dirty": None}
    return {"git_sha": sha, "dirty": dirty}
