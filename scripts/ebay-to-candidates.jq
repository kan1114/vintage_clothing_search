# eBay Browse API の検索レスポンス → Candidate（query 以外）
# scripts/to-candidates.sh から呼ばれる
.itemSummaries[]?
| {
    key: ("ebay:" + .itemId),
    source: "ebay",
    item_id: .itemId,
    title: (.title // ""),
    url: .itemWebUrl,
    price: (.price.value | tonumber? // null),
    currency: (.price.currency // null),
    image_urls: ([.image.imageUrl] + [.additionalImages[]?.imageUrl] | map(select(. != null))),
    description: (.shortDescription // null),
    extra: {
      condition: (.condition // ""),
      buying_options: ((.buyingOptions // []) | join(",")),
      listed_at: (.itemCreationDate // "")
    }
  }
