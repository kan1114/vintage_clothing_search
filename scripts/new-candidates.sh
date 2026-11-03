#!/usr/bin/env bash
# 台帳（data/ledger/*.jsonl）に無い新着だけを data/runs/<RUN_ID>/new.jsonl に書き、
# 画面には「query / key / 価格 / 通貨 / タイトル」の TSV だけを出す。curator エージェントが使う。
#
# 使い方: scripts/new-candidates.sh <RUN_ID>
set -euo pipefail
shopt -s nullglob
cd "$(dirname "$0")/.."

run=${1:?RUN_ID}
dir="data/runs/$run"
files=(data/ledger/*.jsonl "$dir"/candidates-*.jsonl)
if [ ${#files[@]} -eq 0 ]; then : > "$dir/new.jsonl"; else
  cat "${files[@]}" | jq -sc -f scripts/new-candidates.jq > "$dir/new.jsonl"
fi
jq -r '[.query, .key, (.price // "-" | tostring), (.currency // ""), .title] | @tsv' "$dir/new.jsonl"
echo "new: $(wc -l < "$dir/new.jsonl")"
