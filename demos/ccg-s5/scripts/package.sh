#!/usr/bin/env bash
# Build the two Lambda zips and upload them: scripts/package.sh <bucket> [version]
set -euo pipefail
BUCKET=$1; VER=${2:-v1}; ROOT=$(cd "$(dirname "$0")/.." && pwd); OUT=$(mktemp -d)
for fn in payment acxd_api; do
  mkdir -p "$OUT/$fn"; cp "$ROOT/lambdas/common.py" "$ROOT/lambdas/$fn/handler.py" "$OUT/$fn/"
  if [ "$fn" = payment ]; then
    python3 -m pip install --quiet --target "$OUT/$fn" --platform manylinux2014_x86_64 --only-binary=:all: \
      --python-version 3.13 aws-encryption-sdk
  fi
  (cd "$OUT/$fn" && zip -qr "$OUT/$fn.zip" .)
  aws s3 cp "$OUT/$fn.zip" "s3://$BUCKET/$VER/$fn.zip"
done
echo "Uploaded to s3://$BUCKET/$VER/"
