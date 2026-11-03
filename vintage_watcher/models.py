"""ドメインモデル。

エージェント全体で流れるデータは3種類だけ:

- ``WatchQuery``  : ユーザーが「何が欲しいか」を書いたもの（watchlist.yaml の1エントリ）
- ``Listing``     : 各サイトから取ってきた出品を、サイト非依存の形に正規化したもの
- ``Judgement``   : Claude が Listing を見て「欲しいものか」を判定した結果

サイトごとの差異は sources/ の中に閉じ込め、それ以降の処理（重複排除・フィルタ・
判定・通知）はすべてこの共通モデルだけを扱う。これがアダプタパターンの肝。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class Listing(BaseModel):
    """サイト横断で正規化した1件の出品（または投稿）。"""

    source: str = Field(description="取得元の識別子。例: 'ebay', 'yahoo_auctions', 'instagram'")
    item_id: str = Field(description="取得元サイト内で一意なID。重複排除のキーになる")
    title: str
    url: str
    price: float | None = Field(default=None, description="価格。Instagram 投稿などは不明なので None")
    currency: str | None = None
    image_urls: list[str] = Field(default_factory=list)
    description: str | None = Field(default=None, description="本文・キャプションなど判定の材料になるテキスト")
    condition: str | None = None
    listed_at: datetime | None = None
    extra: dict[str, str] = Field(default_factory=dict, description="サイト固有の補足情報（残り時間など）")

    def price_in_jpy(self, rates: dict[str, float]) -> float | None:
        """価格を円換算する。通貨不明・レート未設定なら None。"""
        if self.price is None:
            return None
        if self.currency in (None, "JPY"):
            return self.price
        rate = rates.get(self.currency)
        return self.price * rate if rate else None


class SourceOverride(BaseModel):
    """特定ソースだけ検索キーワードを変えたいとき用（例: eBay は英語で検索したい）。"""

    keywords: list[str] | None = None


class WatchQuery(BaseModel):
    """ユーザーの「欲しいもの」定義。"""

    name: str = Field(description="クエリの識別子（英数字推奨）。DB のキーにも使う")
    description: str = Field(
        description="欲しいものを自然言語で。年代・サイズ・状態・NG条件などを具体的に書くほど判定精度が上がる"
    )
    keywords: list[str] = Field(default_factory=list, description="各サイトの検索窓に入れるキーワード")
    exclude_words: list[str] = Field(default_factory=list, description="タイトル/本文に含まれていたら即除外する語")
    price_min: float | None = Field(default=None, description="円換算での下限")
    price_max: float | None = Field(default=None, description="円換算での上限")
    sources: list[str] = Field(default_factory=lambda: ["ebay", "yahoo_auctions"])
    instagram_hashtags: list[str] = Field(default_factory=list, description="Instagram で監視するハッシュタグ（#なし）")
    min_score: int | None = Field(default=None, description="通知する最低スコア。未指定なら全体設定を使う")
    overrides: dict[str, SourceOverride] = Field(default_factory=dict)

    def keywords_for(self, source: str) -> list[str]:
        override = self.overrides.get(source)
        if override and override.keywords:
            return override.keywords
        return self.keywords


# このクラスはそのまま Structured Outputs の JSON スキーマとして Claude に渡される。
# docstring や Field の description も Claude への指示として読まれるので、丁寧に書いておく。
class Judgement(BaseModel):
    """出品1件に対する目利きの結果。"""

    is_match: bool = Field(description="ユーザーの探しているアイテムに該当すると判断したか")
    score: int = Field(description="0〜100。欲しいものへの合致度。100=まさに探しているもの")
    summary: str = Field(description="通知に載せる1〜2文の日本語の要約（なぜ良い/悪いか）")
    estimated_era: str | None = Field(default=None, description="推定年代。例: '1970年代前半'。判断できなければ null")
    estimated_size: str | None = Field(default=None, description="読み取れたサイズ表記。不明なら null")
    red_flags: list[str] = Field(default_factory=list, description="レプリカ疑い・ダメージ・サイズ違いなど懸念点")
