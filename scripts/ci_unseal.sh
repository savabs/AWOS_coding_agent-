#!/bin/bash
# Reverse scripts/ci_seal.sh: decrypt every sealed.tar.gz.enc under <dir> into its own folder (BENCH_DATA_KEY).
# Folders without one (artifacts made before sealing) are left as they are.
set -euo pipefail
D="$1"
find "$D" -name sealed.tar.gz.enc | while read -r f; do
  [ -n "${BENCH_DATA_KEY:-}" ] || { echo "::error::ci_unseal: BENCH_DATA_KEY secret not set"; exit 1; }
  openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -salt -pass env:BENCH_DATA_KEY -in "$f" | tar -C "$(dirname "$f")" -xzf -
  rm -f "$f"
  echo "ci_unseal: $(dirname "$f")"
done
