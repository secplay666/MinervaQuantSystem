#!/bin/bash
# Download the data package from signed OSS URLs (rate-limited), then verify.
set -u
cd "$(dirname "$0")"
echo "start $(date -Is)"
tr -d '\r' < .urls.tsv > .urls.clean && mv .urls.clean .urls.tsv
while IFS=$'\t' read -r name size url; do
  for attempt in 1 2 3; do
    curl -fsS --limit-rate 5M --retry 3 --retry-delay 5 -C - -o "$name" "$url" && break
    echo "retry $name ($attempt)"
    sleep 10
  done
  actual=$(stat -c %s "$name" 2>/dev/null || echo 0)
  if [ "$actual" = "$size" ]; then echo "ok   $name $actual"; else echo "BAD  $name $actual != $size"; fi
done < .urls.tsv
echo "== sha256"
sha256sum -c SHA256SUMS
echo "done $(date -Is)"
