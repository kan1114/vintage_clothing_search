"""ルールベースの事前フィルタ。

LLM 判定は1件ごとにお金と時間がかかる。そこで「明らかに違う」ものは
安くて速いルールで先に落とす。典型的なファネル構造:

    取得 (数百件) → 既読除外 → ルールフィルタ → LLM 判定 (数十件) → 通知 (数件)

ルールで落とすのは「確実に不要」と言えるものだけにする。迷うものは LLM に任せる。
"""

from __future__ import annotations

import unicodedata

from .models import Listing, WatchQuery


def normalize(text: str) -> str:
    """全角/半角・大文字/小文字の揺れを吸収する（'ＬＶＣ' も 'lvc' も同じ扱いにする）。"""
    return unicodedata.normalize("NFKC", text).casefold()


def rule_filter(listing: Listing, query: WatchQuery, rates: dict[str, float]) -> str | None:
    """除外すべきならその理由を返し、通過なら None を返す。"""
    haystack = normalize(" ".join(filter(None, [listing.title, listing.description])))
    for word in query.exclude_words:
        if normalize(word) in haystack:
            return f"除外ワード「{word}」を含む"

    price = listing.price_in_jpy(rates)
    # 価格不明（Instagram 等）は落とさず LLM に判断を委ねる
    if price is not None:
        if query.price_min is not None and price < query.price_min:
            return f"価格 {price:,.0f}円 が下限未満"
        if query.price_max is not None and price > query.price_max:
            return f"価格 {price:,.0f}円 が上限超過"
    return None
