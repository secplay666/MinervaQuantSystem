"""Move the data package through Aliyun OSS (upload here, download on the server).

Credentials are read from OSS_ACCESS_KEY_ID / OSS_ACCESS_KEY_SECRET, either in
the process environment or in the Windows user environment (HKCU\\Environment,
set with [Environment]::SetEnvironmentVariable(..., "User")).  They are never
printed.

    python oss_transfer.py check
    python oss_transfer.py create  --bucket NAME --region cn-shanghai
    python oss_transfer.py upload  --bucket NAME --region cn-shanghai --dir PACKAGE_DIR --prefix transfer/20260927/
    python oss_transfer.py sign    --bucket NAME --region cn-shanghai --prefix transfer/20260927/ --hours 24 --out urls.txt
    python oss_transfer.py delete  --bucket NAME --region cn-shanghai --prefix transfer/20260927/
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import oss2

KEYS = ("OSS_ACCESS_KEY_ID", "OSS_ACCESS_KEY_SECRET")


def _credential(name: str) -> str | None:
    value = os.environ.get(name)
    if value:
        return value
    if sys.platform == "win32":
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
                return winreg.QueryValueEx(key, name)[0]
        except OSError:
            return None
    return None


def auth() -> oss2.Auth:
    key_id, secret = (_credential(name) for name in KEYS)
    if not key_id or not secret:
        raise SystemExit(f"missing credentials: set {KEYS[0]} and {KEYS[1]}")
    return oss2.Auth(key_id, secret)


def endpoint(region: str) -> str:
    return f"https://oss-{region}.aliyuncs.com"


def bucket_of(args) -> oss2.Bucket:
    return oss2.Bucket(auth(), endpoint(args.region), args.bucket)


def command_check(args) -> None:
    key_id = _credential(KEYS[0]) or ""
    print(f"access key id: {key_id[:4]}...{key_id[-3:]} (length {len(key_id)})")
    service = oss2.Service(auth(), endpoint(args.region or "cn-hangzhou"))
    buckets = [f"{b.name} ({b.location})" for b in oss2.BucketIterator(service)]
    print("buckets:", buckets or "none")


def command_create(args) -> None:
    bucket = bucket_of(args)
    try:
        bucket.get_bucket_info()
        print(f"bucket {args.bucket} already exists")
    except oss2.exceptions.NoSuchBucket:
        bucket.create_bucket(oss2.BUCKET_ACL_PRIVATE)
        print(f"created private bucket {args.bucket} in {args.region}")
    print("acl:", bucket.get_bucket_acl().acl)


def command_upload(args) -> None:
    bucket = bucket_of(args)
    files = sorted(p for p in Path(args.dir).iterdir() if p.is_file())
    checkpoint = Path(args.dir).parent / ".oss_upload_checkpoints"
    store = oss2.ResumableStore(root=str(checkpoint))
    total = sum(p.stat().st_size for p in files)
    done = 0
    started = time.time()
    for path in files:
        key = args.prefix + path.name
        size = path.stat().st_size
        last = [0.0]

        def progress(consumed: int, _total: int | None, base: int = done) -> None:
            now = time.time()
            if now - last[0] > 15 or consumed == size:
                last[0] = now
                rate = (base + consumed) / max(now - started, 1e-9) / 1e6
                print(f"  {path.name}: {consumed / 1e6:8.1f}/{size / 1e6:.1f} MB  "
                      f"(total {(base + consumed) / total:6.1%}, {rate:.1f} MB/s)", flush=True)

        oss2.resumable_upload(bucket, key, str(path), store=store, multipart_threshold=64 * 1024 * 1024,
                              part_size=16 * 1024 * 1024, num_threads=4, progress_callback=progress)
        head = bucket.head_object(key)
        if head.content_length != size:
            raise SystemExit(f"size mismatch for {key}: {head.content_length} != {size}")
        done += size
        print(f"uploaded {key} ({size} bytes, crc64 {head.server_crc})", flush=True)
    print(f"all {len(files)} files uploaded, {total / 1e9:.2f} GB in {time.time() - started:.0f} s")


def command_sign(args) -> None:
    bucket = bucket_of(args)
    lines = []
    for obj in oss2.ObjectIterator(bucket, prefix=args.prefix):
        url = bucket.sign_url("GET", obj.key, int(args.hours * 3600), slash_safe=True)
        lines.append(f"{obj.key.rsplit('/', 1)[-1]}\t{obj.size}\t{url}")
    Path(args.out).write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")  # read on Linux
    print(f"{len(lines)} signed URLs valid for {args.hours} h written to {args.out}")


def command_delete(args) -> None:
    bucket = bucket_of(args)
    keys = [obj.key for obj in oss2.ObjectIterator(bucket, prefix=args.prefix)]
    for start in range(0, len(keys), 1000):
        bucket.batch_delete_objects(keys[start:start + 1000])
    print(f"deleted {len(keys)} objects under {args.prefix}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("check", "create", "upload", "sign", "delete"))
    parser.add_argument("--bucket")
    parser.add_argument("--region", default="cn-shanghai")
    parser.add_argument("--dir")
    parser.add_argument("--prefix", default="transfer/")
    parser.add_argument("--hours", type=float, default=24)
    parser.add_argument("--out", default="oss_urls.txt")
    args = parser.parse_args()
    if args.command != "check" and not args.bucket:
        parser.error("--bucket is required")
    {"check": command_check, "create": command_create, "upload": command_upload, "sign": command_sign,
     "delete": command_delete}[args.command](args)


if __name__ == "__main__":
    main()
