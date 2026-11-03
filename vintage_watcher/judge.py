"""Claude による「目利き」。このエージェントの頭脳にあたる部分。

キーワード検索だけだと「リーバイス 501」で復刻品・現行品・別サイズが大量に引っかかる。
人間が一覧を見て「これはタグが違う」「この写真はレプリカっぽい」と選別している作業を
Claude に代行させるのがこのモジュール。

設計のポイント:

1. **Structured Outputs**
   ``client.beta.messages.parse(output_format=Judgement)`` で、Claude の返答を
   Pydantic モデルとして受け取る。JSON のパース失敗や項目抜けを気にしなくてよい。

2. **画像も渡す（Vision）**
   古着の判定はタグ・ステッチ・色落ちなど写真の情報が大きい。画像URLを
   そのまま content に入れると Anthropic 側で取得してくれる。

3. **effort で思考量を調整**
   1件ずつの合否判定は難問ではないので effort=low でコストと速度を優先。
   精度が足りなければ watchlist.yaml の settings.judge.effort を上げる。

4. **安全側のフォールバック**
   - モデルが拒否した場合は server-side fallback で別モデルが自動で引き継ぐ
   - 画像URLが取得できず 400 になった場合は画像なしで再試行する
"""

from __future__ import annotations

import logging
import anthropic

from .config import JudgeSettings
from .models import Judgement, Listing, WatchQuery

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
あなたはヴィンテージ古着に精通したバイヤーです。ユーザーは自分の欲しいアイテムを探しており、
あなたはフリマ・オークション・SNS に出てきた出品を1件ずつ見て、それがユーザーの探しているものかを判定します。

判定のしかた:
- ユーザーの「欲しいもの」の条件（年代・モデル・ディテール・サイズ・状態・NG条件）と出品情報を突き合わせる
- タイトルや説明文の表記だけでなく、写真から読み取れるタグ・ディテール・ダメージも根拠にする
- 復刻品・レプリカ・現行品・別年代の可能性があれば red_flags に具体的に書く
- 情報が足りず断定できないときは、可能性に応じて中間のスコアを付け、何が不明かを summary に書く

score の目安:
- 90-100: 条件をほぼすべて満たし、すぐ見るべき
- 70-89 : 主要条件は満たすが、確認すべき点がある
- 40-69 : 一部条件は合うが、外れている可能性が高い
- 0-39  : 探しているものではない

summary は LINE 通知にそのまま載るので、日本語で簡潔に（2文以内）。"""


def format_listing(listing: Listing, rates: dict[str, float]) -> str:
    lines = [
        f"出品元: {listing.source}",
        f"タイトル: {listing.title}",
    ]
    if listing.price is not None:
        jpy = listing.price_in_jpy(rates)
        price = f"{listing.price:,.0f} {listing.currency or ''}".strip()
        if jpy is not None and listing.currency not in (None, "JPY"):
            price += f"（約{jpy:,.0f}円）"
        lines.append(f"価格: {price}")
    else:
        lines.append("価格: 不明")
    if listing.condition:
        lines.append(f"状態: {listing.condition}")
    for k, v in listing.extra.items():
        lines.append(f"{k}: {v}")
    if listing.description:
        # 長すぎる本文はトークン節約のため切り詰める（判定に必要なのは冒頭が大半）
        lines.append(f"説明文:\n{listing.description[:2000]}")
    lines.append(f"URL: {listing.url}")
    return "\n".join(lines)


class Judge:
    def __init__(
        self,
        settings: JudgeSettings,
        rates: dict[str, float],
        client: anthropic.Anthropic | None = None,
    ):
        self.settings = settings
        self.rates = rates
        # 引数なしの Anthropic() は環境変数 ANTHROPIC_API_KEY などから認証情報を読む
        self.client = client or anthropic.Anthropic()

    def _build_content(self, listing: Listing, query: WatchQuery, with_images: bool) -> list[dict]:
        content: list[dict] = []
        if with_images:
            for url in listing.image_urls[: self.settings.max_images]:
                content.append({"type": "image", "source": {"type": "url", "url": url}})
        content.append(
            {
                "type": "text",
                "text": (
                    f"## ユーザーが探しているもの\n{query.description.strip()}\n\n"
                    f"## 出品情報\n{format_listing(listing, self.rates)}\n\n"
                    "この出品を判定してください。"
                ),
            }
        )
        return content

    def _call(self, content: list[dict]):
        return self.client.beta.messages.parse(
            model=self.settings.model,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": content}],
            thinking={"type": "adaptive"},
            output_config={"effort": self.settings.effort},
            output_format=Judgement,
            # モデルが安全上の理由で応答を拒否した場合、サーバー側で推奨モデルに自動で切り替える
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )

    def judge(self, listing: Listing, query: WatchQuery) -> Judgement | None:
        """判定結果を返す。判定できなかった場合は None。"""
        with_images = self.settings.use_images and bool(listing.image_urls)
        try:
            response = self._call(self._build_content(listing, query, with_images))
        except anthropic.BadRequestError as e:
            # 画像URLが期限切れ・非公開などで取得できないと 400 になる。画像なしで再挑戦。
            if not with_images:
                raise
            log.warning("画像付き判定に失敗したため画像なしで再試行: %s (%s)", listing.url, e.message)
            response = self._call(self._build_content(listing, query, with_images=False))

        if response.stop_reason == "refusal":
            log.warning("判定が拒否されました: %s", listing.url)
            return None
        if response.stop_reason == "max_tokens" or response.parsed_output is None:
            log.warning("判定結果を取得できませんでした (stop_reason=%s): %s", response.stop_reason, listing.url)
            return None
        return response.parsed_output
