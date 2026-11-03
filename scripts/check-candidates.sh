#!/usr/bin/env bash
# Candidate ファイルがデータ契約を満たすか検査する。違反行を出力し、違反があれば終了コード1。
# 使い方: scripts/check-candidates.sh <candidates.jsonl>
set -euo pipefail
cd "$(dirname "$0")/.."
out=$(jq -c -f scripts/check-candidates.jq "${1:?file}")
if [ -n "$out" ]; then echo "$out"; exit 1; fi
echo "ok: $(wc -l < "$1") lines"
