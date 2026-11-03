"""eBay: 公式 Browse API を使う。

公式APIがあるサイトはスクレイピングより必ずAPIを使う。
規約的に安全で、HTML の変更で壊れることもない。

認証は OAuth 2.0 の Client Credentials フロー:
  1. developer.ebay.com でアプリを作り Client ID / Client Secret を取得
  2. それを Basic 認証で token エンドポイントに送ると「アプリケーショントークン」が返る
  3. トークン（約2時間有効）を Bearer ヘッダーに付けて検索APIを叩く
ユーザーのログインは不要（公開されている出品を読むだけなので）。
"""

from __future__ import annotations

import os
import time
from datetime import datetime

import httpx

from ..models import Listing, WatchQuery
from .base import Source, SourceError

TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"
SCOPE = "https://api.ebay.com/oauth/api_scope"


class EbaySource(Source):
    name = "ebay"

    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        marketplace: str | None = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.client_id = client_id or os.environ.get("EBAY_CLIENT_ID")
        self.client_secret = client_secret or os.environ.get("EBAY_CLIENT_SECRET")
        self.marketplace = marketplace or os.environ.get("EBAY_MARKETPLACE", "EBAY_US")
        self._token: str | None = None
        self._token_expires_at = 0.0

    def _get_token(self) -> str:
        if self._token and time.time() < self._token_expires_at - 60:
            return self._token
        if not (self.client_id and self.client_secret):
            raise SourceError("EBAY_CLIENT_ID / EBAY_CLIENT_SECRET が未設定です")
        resp = self.client.post(
            TOKEN_URL,
            auth=(self.client_id, self.client_secret),
            data={"grant_type": "client_credentials", "scope": SCOPE},
        )
        if resp.status_code != 200:
            raise SourceError(f"eBay トークン取得失敗: {resp.status_code} {resp.text[:200]}")
        body = resp.json()
        self._token = body["access_token"]
        self._token_expires_at = time.time() + int(body.get("expires_in", 7200))
        return self._token

    def search(self, query: WatchQuery) -> list[Listing]:
        results: dict[str, Listing] = {}
        for keyword in query.keywords_for(self.name):
            self.throttle()
            params = {"q": keyword, "sort": "newlyListed", "limit": "50"}
            try:
                resp = self.client.get(
                    SEARCH_URL,
                    params=params,
                    headers={
                        "Authorization": f"Bearer {self._get_token()}",
                        "X-EBAY-C-MARKETPLACE-ID": self.marketplace,
                    },
                )
            except httpx.HTTPError as e:
                raise SourceError(f"eBay 検索失敗: {e}") from e
            if resp.status_code != 200:
                raise SourceError(f"eBay 検索失敗: {resp.status_code} {resp.text[:200]}")
            for item in resp.json().get("itemSummaries", []):
                listing = parse_item(item)
                results.setdefault(listing.item_id, listing)
        return list(results.values())


def parse_item(item: dict) -> Listing:
    price = item.get("price") or {}
    images = []
    if item.get("image", {}).get("imageUrl"):
        images.append(item["image"]["imageUrl"])
    images += [img["imageUrl"] for img in item.get("additionalImages", []) if img.get("imageUrl")]

    listed_at = None
    if item.get("itemCreationDate"):
        listed_at = datetime.fromisoformat(item["itemCreationDate"].replace("Z", "+00:00"))

    return Listing(
        source="ebay",
        item_id=item["itemId"],
        title=item.get("title", ""),
        url=item.get("itemWebUrl", ""),
        price=float(price["value"]) if price.get("value") else None,
        currency=price.get("currency"),
        image_urls=images,
        description=item.get("shortDescription"),
        condition=item.get("condition"),
        listed_at=listed_at,
        extra={"buying_options": ",".join(item.get("buyingOptions", []))},
    )
