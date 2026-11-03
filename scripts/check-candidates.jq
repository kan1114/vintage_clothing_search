# Candidate ファイルの検証。データ契約に違反する行だけを理由付きで出力する（何も出なければ OK）
# scripts/check-candidates.sh から呼ばれる
{
  key: .key,
  problems: [
    (if (.query | type) != "string" or .query == "" then "query がない" else empty end),
    (if (.key | type) != "string" or ((.key | tostring) | test("^(yahoo_auctions|ebay|instagram):.+") | not) then "key が不正" else empty end),
    (if (.title | type) != "string" then "title がない" else empty end),
    (if (.url | type) != "string" or ((.url | tostring) | startswith("http") | not) then "url が不正" else empty end),
    (if .price != null and (.price | type) != "number" then "price が数値でない" else empty end),
    (if (.image_urls | type) != "array" then "image_urls が配列でない" else empty end)
  ]
}
| select(.problems | length > 0)
