"""ヤフオク!: 検索結果ページの HTML を解析する。

ヤフオクには現在一般公開の検索APIがないため、ブラウザで見られる検索結果ページを
取得して解析する（いわゆるスクレイピング）。その際のルール:

- 頻度を低く保つ（推奨: 1時間に1回程度。base.Source.throttle でリクエスト間隔も空ける）
- 取得するのは検索結果1ページ目（新着順）だけ。商品ページの巡回はしない
- 利用前に Yahoo! JAPAN の利用規約・robots.txt を自分で確認する

HTML 構造はサイト側の改修で予告なく変わる。壊れたら ``parse_search_html`` の
セレクタを直すだけで済むよう、取得（HTTP）と解析（パース）を分けてある。
解析は純粋関数なので、保存した HTML を使ってオフラインでテストできる。
"""

from __future__ import annotations

import re

import httpx
from bs4 import BeautifulSoup, Tag

from ..models import Listing, WatchQuery
from .base import Source, SourceError

SEARCH_URL = "https://auctions.yahoo.co.jp/search/search"


class YahooAuctionsSource(Source):
    name = "yahoo_auctions"

    def build_params(self, keyword: str, query: WatchQuery) -> dict[str, str]:
        params = {
            "p": keyword,
            "va": keyword,
            "exflg": "1",
            "b": "1",
            "n": "50",
            "s1": "new",  # 新着順
            "o1": "d",
        }
        # 円建てなのでサイト側の価格絞り込みも使う（取得件数を減らせる）
        if query.price_min is not None:
            params["aucminprice"] = str(int(query.price_min))
        if query.price_max is not None:
            params["aucmaxprice"] = str(int(query.price_max))
        return params

    def search(self, query: WatchQuery) -> list[Listing]:
        results: dict[str, Listing] = {}
        for keyword in query.keywords_for(self.name):
            self.throttle()
            try:
                resp = self.client.get(SEARCH_URL, params=self.build_params(keyword, query))
            except httpx.HTTPError as e:
                raise SourceError(f"ヤフオク取得失敗: {e}") from e
            if resp.status_code != 200:
                raise SourceError(f"ヤフオク取得失敗: HTTP {resp.status_code}")
            for listing in parse_search_html(resp.text):
                results.setdefault(listing.item_id, listing)
        return list(results.values())


_PRICE_RE = re.compile(r"[\d,]+")


def _parse_price(text: str | None) -> float | None:
    if not text:
        return None
    m = _PRICE_RE.search(text)
    return float(m.group().replace(",", "")) if m else None


def _text(node: Tag | None) -> str | None:
    return node.get_text(strip=True) if node else None


def parse_search_html(html: str) -> list[Listing]:
    """検索結果 HTML から出品一覧を抜き出す。

    ヤフオクの各商品カードは ``li.Product`` で、タイトルリンクに
    ``data-auction-id`` などの data 属性が付いている。data 属性を優先し、
    無ければ表示テキストから拾う、という二段構えで壊れにくくしている。
    """
    soup = BeautifulSoup(html, "html.parser")
    listings: list[Listing] = []
    for card in soup.select("li.Product"):
        link = card.select_one("a.Product__titleLink") or card.select_one("a[data-auction-id]")
        if link is None:
            continue
        item_id = link.get("data-auction-id")
        href = link.get("href", "")
        if not item_id:
            m = re.search(r"/auction/([A-Za-z0-9]+)", href)
            item_id = m.group(1) if m else None
        if not item_id:
            continue

        title = link.get("data-auction-title") or link.get("title") or _text(link) or ""
        price = _parse_price(link.get("data-auction-price")) or _parse_price(
            _text(card.select_one(".Product__priceValue"))
        )
        img = link.get("data-auction-img")
        if not img:
            img_tag = card.select_one("img.Product__imageData") or card.select_one("img")
            img = img_tag.get("src") if img_tag else None

        extra = {}
        remaining = _text(card.select_one(".Product__time"))
        if remaining:
            extra["remaining"] = remaining
        bids = _text(card.select_one(".Product__bid"))
        if bids:
            extra["bids"] = bids

        listings.append(
            Listing(
                source="yahoo_auctions",
                item_id=str(item_id),
                title=title,
                url=href or f"https://auctions.yahoo.co.jp/jp/auction/{item_id}",
                price=price,
                currency="JPY",
                image_urls=[img] if img else [],
                extra=extra,
            )
        )
    return listings
