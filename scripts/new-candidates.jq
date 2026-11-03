# 台帳 + 候補 → 台帳に無い新着だけ（query|key 単位で重複除去）
# scripts/new-candidates.sh から呼ばれる
group_by(.query + "|" + .key)
| map(select(all(.[]; has("status") | not)))
| map(.[0])
| .[]
