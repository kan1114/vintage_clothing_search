#!/usr/bin/env bash
# 取得した生データ（HTML / JSON）を Candidate 形式の JSONL に変換して標準出力に出す。
# collector エージェントが使う「ツール」。変換は決定的な処理なので LLM にはやらせない。
#
# 使い方: scripts/to-candidates.sh <source> <query> <input-file> [hashtag]
#   source: yahoo_auctions | ebay | instagram
set -euo pipefail
cd "$(dirname "$0")/.."

source=${1:?source}; query=${2:?query}; input=${3:?input file}; hashtag=${4:-}

case "$source" in
  yahoo_auctions) jq -Rsc -f scripts/yahoo-to-candidates.jq "$input" ;;
  ebay)           jq -c -f scripts/ebay-to-candidates.jq "$input" ;;
  instagram)      jq -c -f scripts/instagram-to-candidates.jq "$input" ;;
  *) echo "unknown source: $source" >&2; exit 2 ;;
esac | jq -c --arg q "$query" --arg tag "$hashtag" \
  '. + {query: $q} | if $tag != "" then .extra.hashtag = $tag else . end'
