"""Instagram: 公式 Graph API のハッシュタグ検索を使う。

古着屋さんは新入荷を Instagram に先に載せることが多いので、ハッシュタグ監視は有効。
Instagram はスクレイピングを規約で禁止しているため、公式 API のみ使う。

前提条件（README に手順あり）:
- 自分の Instagram をプロアカウント（ビジネス or クリエイター）にする
- Facebook ページと連携し、Meta for Developers でアプリを作成
- 長期アクセストークンと Instagram ユーザーID（IG_USER_ID）を取得

制約:
- 7日間で検索できるユニークなハッシュタグは30個まで → ハッシュタグID をDBにキャッシュ
- recent_media は直近24時間の投稿のみ返す → 1日に数回以上実行する必要がある
- 投稿には価格が無いことが多い → 価格フィルタは素通りし、Claude がキャプションから判断する
"""

from __future__ import annotations

import os
from datetime import datetime

import httpx

from ..models import Listing, WatchQuery
from ..store import Store
from .base import Source, SourceError

GRAPH_BASE = "https://graph.facebook.com/{version}"


class InstagramSource(Source):
    name = "instagram"

    def __init__(
        self,
        store: Store,
        access_token: str | None = None,
        ig_user_id: str | None = None,
        api_version: str | None = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.store = store
        self.access_token = access_token or os.environ.get("IG_ACCESS_TOKEN")
        self.ig_user_id = ig_user_id or os.environ.get("IG_USER_ID")
        self.base = GRAPH_BASE.format(version=api_version or os.environ.get("IG_API_VERSION", "v23.0"))

    def _get(self, path: str, params: dict[str, str]) -> dict:
        if not (self.access_token and self.ig_user_id):
            raise SourceError("IG_ACCESS_TOKEN / IG_USER_ID が未設定です")
        self.throttle()
        try:
            resp = self.client.get(
                f"{self.base}/{path}",
                params={**params, "user_id": self.ig_user_id, "access_token": self.access_token},
            )
        except httpx.HTTPError as e:
            raise SourceError(f"Instagram API 失敗: {e}") from e
        if resp.status_code != 200:
            raise SourceError(f"Instagram API 失敗: {resp.status_code} {resp.text[:200]}")
        return resp.json()

    def hashtag_id(self, tag: str) -> str:
        key = f"ig_hashtag:{tag.lower()}"
        cached = self.store.cache_get(key)
        if cached:
            return str(cached)
        data = self._get("ig_hashtag_search", {"q": tag}).get("data", [])
        if not data:
            raise SourceError(f"ハッシュタグ #{tag} が見つかりません")
        self.store.cache_set(key, data[0]["id"])
        return data[0]["id"]

    def search(self, query: WatchQuery) -> list[Listing]:
        results: dict[str, Listing] = {}
        for tag in query.instagram_hashtags:
            tag = tag.lstrip("#")
            body = self._get(
                f"{self.hashtag_id(tag)}/recent_media",
                {"fields": "id,caption,media_type,media_url,permalink,timestamp", "limit": "50"},
            )
            for post in body.get("data", []):
                listing = parse_post(post, tag)
                results.setdefault(listing.item_id, listing)
        return list(results.values())


def parse_post(post: dict, tag: str) -> Listing:
    caption = post.get("caption") or ""
    first_line = caption.strip().splitlines()[0] if caption.strip() else f"#{tag} の投稿"
    images = []
    # 動画の media_url は mp4 なので画像としては使わない
    if post.get("media_type") in ("IMAGE", "CAROUSEL_ALBUM") and post.get("media_url"):
        images.append(post["media_url"])
    listed_at = None
    if post.get("timestamp"):
        # Graph API は "2026-11-03T01:23:45+0000" 形式
        listed_at = datetime.strptime(post["timestamp"], "%Y-%m-%dT%H:%M:%S%z")
    return Listing(
        source="instagram",
        item_id=post["id"],
        title=first_line[:80],
        url=post.get("permalink", ""),
        image_urls=images,
        description=caption,
        listed_at=listed_at,
        extra={"hashtag": tag},
    )
