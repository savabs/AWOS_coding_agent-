#!/bin/bash
# Seal a CI output folder before actions/upload-artifact: this repo is PUBLIC, so artifacts are downloadable by any
# GitHub user, and Online-Mind2Web task texts / screenshots (gated dataset) must not be in them in plaintext.
#   scripts/ci_seal.sh <dir>     -> <dir>/sealed.tar.gz.enc (everything else in <dir> is removed)
#   scripts/ci_unseal.sh <dir>   reverses it (after download-artifact / gh run download)
# Cipher = ladder.yml's bench data: openssl aes-256-cbc -pbkdf2 -iter 200000 -salt, key in the BENCH_DATA_KEY secret.
# Fails closed: with no key the plaintext is deleted, so the upload step finds nothing to publish.
set -euo pipefail
D="$1"
[ -d "$D" ] || { echo "ci_seal: no folder $D (nothing to seal)"; exit 0; }
if [ -z "${BENCH_DATA_KEY:-}" ]; then
  rm -rf "$D"
  echo "::error::ci_seal: BENCH_DATA_KEY secret not set; deleted $D instead of uploading it in plaintext"
  exit 1
fi
T=$(mktemp "${RUNNER_TEMP:-/tmp}/seal.XXXXXX")
tar -C "$D" -czf - . | openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -pass env:BENCH_DATA_KEY -out "$T"
find "$D" -mindepth 1 -delete
mv "$T" "$D/sealed.tar.gz.enc"
echo "ci_seal: $D -> sealed.tar.gz.enc ($(du -h "$D/sealed.tar.gz.enc" | cut -f1))"
