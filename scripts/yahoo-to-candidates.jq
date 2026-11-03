# ヤフオク検索結果 HTML → Candidate（query 以外）
# scripts/to-candidates.sh から呼ばれる
split("<li class=\"Product\"")[1:][]
| gsub("\\s+"; " ")
| {
    item_id: (
      (capture("data-auction-id=\"(?<v>[^\"]+)\"").v)
      // (capture("/auction/(?<v>[A-Za-z0-9]+)").v)
      // null),
    title: (
      (capture("data-auction-title=\"(?<v>[^\"]+)\"").v)
      // (capture("Product__titleLink[^>]*> ?(?<v>[^<]+?) ?</a>").v)
      // null),
    url: ((capture("Product__titleLink\" href=\"(?<v>[^\"]+)\"").v) // null),
    price: (
      ((capture("data-auction-price=\"(?<v>[0-9]+)\"").v)
       // (capture("Product__priceValue\">(?<v>[0-9,]+)").v | gsub(","; "")))
      | tonumber? // null),
    img: (
      (capture("data-auction-img=\"(?<v>[^\"]+)\"").v)
      // (capture("<img[^>]* src=\"(?<v>https?://[^\"]+)\"").v)
      // null),
    remaining: ((capture("Product__time\">(?<v>[^<]+)<").v) // null),
    bids: ((capture("Product__bid\">(?<v>[^<]+)<").v) // null)
  }
| select(.item_id != null)
| {
    key: ("yahoo_auctions:" + .item_id),
    source: "yahoo_auctions",
    item_id,
    title: (.title // ""),
    url: (.url // ("https://auctions.yahoo.co.jp/jp/auction/" + .item_id)),
    price,
    currency: "JPY",
    image_urls: ([.img] | map(select(. != null))),
    description: null,
    extra: ({remaining, bids} | with_entries(select(.value != null)))
  }
