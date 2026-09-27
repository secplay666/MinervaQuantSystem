"""Pack the local data store for transfer or backup (ADR-005).

Writes an uncompressed POSIX tar (parquet is already compressed) split into
fixed-size parts, plus:

* MANIFEST.tsv  every packed file with its size and sha256,
* SHA256SUMS    sha256 of every part (``sha256sum -c SHA256SUMS`` on Linux),
* README.txt    source commit, data version and restore steps.

Only what cannot be derived is packed: the raw layer, manifests, reports,
checkpoints, the canonical layer (so a restore can be checked against a
rebuild) and the research artifacts including the experiment registry.
Rebuild archives, staging, the feature cache and market.duckdb are left out.

    .venv/Scripts/python.exe scripts/pack_data.py --output ../quant_transfer_20260927
    .venv/Scripts/python.exe scripts/pack_data.py --verify ../quant_transfer_20260927
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
import tarfile
import time
from datetime import UTC, datetime
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
INCLUDE = ("data/raw", "data/manifests", "data/reports", "data/checkpoints", "data/canonical", "artifacts")
SKIP_SUFFIXES = (".tmp", "-wal", "-shm", ".building")
PART_PREFIX = "quant_data.tar.part"


class SplitWriter:
    """File-like sink that rolls over to a new part every ``part_size`` bytes."""

    def __init__(self, directory: Path, part_size: int) -> None:
        self.directory, self.part_size = directory, part_size
        self.parts: list[Path] = []
        self._file = None
        self._written = 0

    def _roll(self) -> None:
        if self._file:
            self._file.close()
        path = self.directory / f"{PART_PREFIX}{len(self.parts):03d}"
        self.parts.append(path)
        self._file = open(path, "wb")
        self._written = 0

    def write(self, data: bytes) -> int:
        view = memoryview(data)
        while view:
            if self._file is None or self._written >= self.part_size:
                self._roll()
            chunk = view[: self.part_size - self._written]
            self._file.write(chunk)
            self._written += len(chunk)
            view = view[len(chunk):]
        return len(data)

    def close(self) -> None:
        if self._file:
            self._file.close()


class HashingReader:
    def __init__(self, handle) -> None:
        self.handle, self.digest = handle, hashlib.sha256()

    def read(self, size: int = -1) -> bytes:
        data = self.handle.read(size)
        self.digest.update(data)
        return data


def collect(root: Path) -> list[Path]:
    files = []
    for top in INCLUDE:
        base = root / top
        if base.exists():
            files += [p for p in base.rglob("*") if p.is_file() and not p.name.endswith(SKIP_SUFFIXES)]
    return sorted(files, key=lambda p: p.relative_to(root).as_posix())


def checkpoint_registry(root: Path) -> None:
    registry = root / "artifacts" / "registry.sqlite"
    if registry.exists():
        with sqlite3.connect(registry) as con:
            con.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def data_version(root: Path) -> dict:
    database = root / "data" / "market.duckdb"
    if not database.exists():
        return {}
    with duckdb.connect(str(database), read_only=True) as con:
        row = con.execute("SELECT run_id, data_version FROM ingestion_runs WHERE status = 'complete' "
                          "AND data_version IS NOT NULL ORDER BY run_id DESC LIMIT 1").fetchone()
    return {"catalog_run_id": row[0], "data_version": row[1]} if row else {}


def git_commit(root: Path) -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True,
                              check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def pack(root: Path, output: Path, part_size_mb: int) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    checkpoint_registry(root)
    files = collect(root)
    started = time.perf_counter()
    writer = SplitWriter(output, part_size_mb * 1024 * 1024)
    manifest = []
    with tarfile.open(fileobj=writer, mode="w|", format=tarfile.PAX_FORMAT) as archive:
        for path in files:
            name = path.relative_to(root).as_posix()
            stat = path.stat()
            info = tarfile.TarInfo(name)
            info.size, info.mtime, info.mode = stat.st_size, int(stat.st_mtime), 0o644
            with open(path, "rb") as handle:
                reader = HashingReader(handle)
                archive.addfile(info, reader)
            manifest.append((name, stat.st_size, reader.digest.hexdigest()))
    writer.close()
    write_lf(output / "MANIFEST.tsv",
             "path\tsize\tsha256\n" + "".join(f"{n}\t{s}\t{h}\n" for n, s, h in manifest))
    sums = [(sha256_file(part), part.name) for part in writer.parts]
    write_lf(output / "SHA256SUMS", "".join(f"{h}  {n}\n" for h, n in sums))
    info = {
        "created_at": datetime.now(UTC).isoformat(), "source_commit": git_commit(root),
        "catalog_run_id": None, "data_version": None, **data_version(root),
        "files": len(manifest), "bytes": sum(s for _, s, _ in manifest), "parts": len(writer.parts),
        "part_size_mb": part_size_mb, "included": list(INCLUDE), "seconds": round(time.perf_counter() - started, 1),
    }
    write_lf(output / "PACK.json", json.dumps(info, indent=2) + "\n")
    write_lf(output / "README.txt", README.format(**info, first=writer.parts[0].name))
    return info


def write_lf(path: Path, text: str) -> None:
    """LF line endings on every platform, so `sha256sum -c` works on Linux."""
    path.write_text(text, encoding="utf-8", newline="\n")


def verify(output: Path) -> dict:
    """Re-read every part, check part checksums, then stream the tar and
    check every member against MANIFEST.tsv."""
    sums = [line.split("  ", 1) for line in (output / "SHA256SUMS").read_text(encoding="utf-8").splitlines()]
    bad_parts = [name for digest, name in sums if sha256_file(output / name) != digest]
    expected = {}
    for line in (output / "MANIFEST.tsv").read_text(encoding="utf-8").splitlines()[1:]:
        name, size, digest = line.split("\t")
        expected[name] = (int(size), digest)

    class Joined:
        """The parts read back to back as one stream."""

        def __init__(self, paths: list[Path]) -> None:
            self.paths, self.handle = list(paths), None
            self._next()

        def _next(self) -> None:
            if self.handle:
                self.handle.close()
            self.handle = open(self.paths.pop(0), "rb") if self.paths else None

        def read(self, size: int = -1) -> bytes:
            out = bytearray()
            while self.handle is not None and (size < 0 or len(out) < size):
                data = self.handle.read(-1 if size < 0 else size - len(out))
                if not data:
                    self._next()
                    continue
                out += data
            return bytes(out)

    mismatched, seen = [], 0
    with tarfile.open(fileobj=Joined([output / name for _, name in sums]), mode="r|") as archive:
        for member in archive:
            digest = hashlib.sha256(archive.extractfile(member).read()).hexdigest()
            seen += 1
            if expected.get(member.name) != (member.size, digest):
                mismatched.append(member.name)
    return {"parts": len(sums), "bad_parts": bad_parts, "members": seen, "expected": len(expected),
            "mismatched": mismatched[:20]}


README = """Quant data package
==================

created_at      {created_at}
source_commit   {source_commit}
catalog_run_id  {catalog_run_id}
data_version    {data_version}
files           {files}
bytes           {bytes}
parts           {parts} x {part_size_mb} MB  (first: {first})

Restore on Linux (e.g. into /home/van/quant, the repository checkout):

  sha256sum -c SHA256SUMS                       # every part must be OK
  cat quant_data.tar.part* | tar -x -C /home/van/quant
  python scripts/pack_data.py --check-tree MANIFEST.tsv   # optional: verify extracted files
  quant-data catalog --rebuild                  # rebuild market.duckdb from data/canonical
  quant-data audit                              # whole-store audit
  quant-data rebuild                            # dry run: rebuilt canonical must equal the restored one
"""


def check_tree(manifest: Path, root: Path) -> dict:
    missing, mismatched, total = [], [], 0
    for line in manifest.read_text(encoding="utf-8").splitlines()[1:]:
        name, size, digest = line.split("\t")
        total += 1
        path = root / name
        if not path.exists():
            missing.append(name)
        elif path.stat().st_size != int(size) or sha256_file(path) != digest:
            mismatched.append(name)
    return {"files": total, "missing": missing[:20], "mismatched": mismatched[:20]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--output", help="new directory for the package")
    parser.add_argument("--part-size-mb", type=int, default=500)
    parser.add_argument("--verify", help="verify a package directory")
    parser.add_argument("--check-tree", help="verify extracted files under --root against a MANIFEST.tsv")
    args = parser.parse_args()
    if args.verify:
        print(json.dumps(verify(Path(args.verify)), indent=2))
    elif args.check_tree:
        print(json.dumps(check_tree(Path(args.check_tree), Path(args.root)), indent=2))
    elif args.output:
        print(json.dumps(pack(Path(args.root), Path(args.output), args.part_size_mb), indent=2))
    else:
        parser.error("give --output, --verify or --check-tree")


if __name__ == "__main__":
    main()
