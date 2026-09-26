from __future__ import annotations

import hashlib
import json
import secrets
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def utc_now() -> datetime:
    return datetime.now(UTC)


def utc_now_iso() -> str:
    return utc_now().isoformat()


def make_run_id() -> str:
    return utc_now().strftime("%Y%m%dT%H%M%SZ")


def unique_run_id(root: Path) -> str:
    """A run id not used by any earlier run under ``root``.

    Ids have one-second resolution and name the immutable raw directories,
    so two runs in the same second would overwrite each other's raw files.
    """
    while True:
        run_id = make_run_id()
        taken = (root / "data" / "manifests" / f"{run_id}.json").exists() or any(
            (root / "data" / "raw").glob(f"*/*/run_id={run_id}")
        )
        if not taken:
            return run_id
        time.sleep(0.2)


def artifact_run_id() -> str:
    """Id for a research artifact (backtest, factor evaluation, sweep trial).

    Microseconds plus a random suffix, so runs started in the same second
    never collide; data-ingestion run ids keep their one-second format.
    """
    return f"{utc_now().strftime('%Y%m%dT%H%M%S%fZ')}-{secrets.token_hex(2)}"


def create_artifact_dir(parent: Path) -> tuple[str, Path]:
    """Create a fresh ``parent/<artifact_run_id>`` directory (never reused)."""
    parent.mkdir(parents=True, exist_ok=True)
    while True:
        run_id = artifact_run_id()
        try:
            (parent / run_id).mkdir()
        except FileExistsError:
            continue
        return run_id, parent / run_id


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
