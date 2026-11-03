# Instagram Graph API recent_media レスポンス → Candidate（query と hashtag 以外）
# scripts/to-candidates.sh から呼ばれる
.data[]?
| {
    key: ("instagram:" + .id),
    source: "instagram",
    item_id: .id,
    title: ((.caption // "") | split("\n")[0] | .[0:80] | if . == "" then "(キャプションなし)" else . end),
    url: (.permalink // ""),
    price: null,
    currency: null,
    image_urls: (if (.media_type == "IMAGE" or .media_type == "CAROUSEL_ALBUM") and (.media_url != null) then [.media_url] else [] end),
    description: (.caption // null),
    extra: {posted_at: (.timestamp // "")}
  }
