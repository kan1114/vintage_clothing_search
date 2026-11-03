"""通知。

LINE は「LINE Notify」が 2025年3月に終了したため、現在は **LINE Messaging API** を使う。
自分専用の公式アカウント（Bot）を作り、そこから自分にプッシュメッセージを送る形になる。

料金の考え方（重要）:
LINE 公式アカウントの無料プランは「月200通」まで。この「通数」は
**送信先の人数 × リクエスト回数** で数えられ、1リクエストに吹き出しを何個入れても1通。
そこで、1回の実行で見つかった候補をまとめて「カルーセル（横スクロールのカード）」
1通にして送る。こうすると1時間ごとに実行しても、見つかった回だけ1通消費で済む。
"""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass

import httpx

from .models import Judgement, Listing, WatchQuery

log = logging.getLogger(__name__)

PUSH_URL = "https://api.line.me/v2/bot/message/push"
MAX_BUBBLES = 12  # Flex カルーセルに入れられる最大カード数
SOURCE_LABELS = {"ebay": "eBay", "yahoo_auctions": "ヤフオク", "instagram": "Instagram"}


@dataclass
class Finding:
    """通知対象（クエリ・出品・判定結果のセット）。"""

    query: WatchQuery
    listing: Listing
    judgement: Judgement


class Notifier(ABC):
    @abstractmethod
    def send(self, findings: list[Finding]) -> None: ...


class ConsoleNotifier(Notifier):
    """--dry-run 用。通知せず標準出力に表示するだけ。"""

    def send(self, findings: list[Finding]) -> None:
        for f in findings:
            j = f.judgement
            print(f"[{j.score:>3}点] ({f.query.name}) {f.listing.title}")
            print(f"       {j.summary}")
            if j.red_flags:
                print(f"       ⚠ {' / '.join(j.red_flags)}")
            print(f"       {f.listing.url}")


def _price_text(listing: Listing) -> str:
    if listing.price is None:
        return "価格不明"
    if listing.currency in (None, "JPY"):
        return f"¥{listing.price:,.0f}"
    return f"{listing.price:,.2f} {listing.currency}"


def build_bubble(f: Finding) -> dict:
    """1件分の Flex Message バブル（カード）を組み立てる。"""
    j, item = f.judgement, f.listing
    body: list[dict] = [
        {
            "type": "text",
            "text": f"{SOURCE_LABELS.get(item.source, item.source)} ・ {f.query.name}",
            "size": "xs",
            "color": "#888888",
        },
        {"type": "text", "text": item.title or "(無題)", "weight": "bold", "wrap": True, "maxLines": 3},
        {
            "type": "box",
            "layout": "baseline",
            "contents": [
                {"type": "text", "text": _price_text(item), "size": "md", "flex": 0},
                {"type": "text", "text": f"{j.score}点", "size": "md", "align": "end", "color": "#D9534F"},
            ],
        },
        {"type": "text", "text": j.summary or "-", "size": "sm", "wrap": True, "color": "#444444"},
    ]
    if j.red_flags:
        body.append(
            {"type": "text", "text": "⚠ " + " / ".join(j.red_flags), "size": "xs", "wrap": True, "color": "#B8860B"}
        )

    bubble: dict = {
        "type": "bubble",
        "size": "kilo",
        "body": {"type": "box", "layout": "vertical", "spacing": "sm", "contents": body},
        "footer": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "button",
                    "style": "primary",
                    "height": "sm",
                    "action": {"type": "uri", "label": "出品を見る", "uri": item.url},
                }
            ],
        },
    }
    # LINE の画像は HTTPS 必須
    image = next((u for u in item.image_urls if u.startswith("https://")), None)
    if image:
        bubble["hero"] = {
            "type": "image",
            "url": image,
            "size": "full",
            "aspectRatio": "1:1",
            "aspectMode": "cover",
            "action": {"type": "uri", "uri": item.url},
        }
    return bubble


def build_messages(findings: list[Finding]) -> list[dict]:
    """スコアの高い順に最大12件を1つのカルーセルにまとめる。"""
    top = sorted(findings, key=lambda f: f.judgement.score, reverse=True)[:MAX_BUBBLES]
    alt = f"欲しいものが{len(findings)}件見つかりました: " + " / ".join(f.listing.title for f in top)
    messages = [
        {
            "type": "flex",
            "altText": alt[:400],  # 通知プレビューに出る文字列（上限400文字）
            "contents": {"type": "carousel", "contents": [build_bubble(f) for f in top]},
        }
    ]
    if len(findings) > MAX_BUBBLES:
        messages.append({"type": "text", "text": f"ほか {len(findings) - MAX_BUBBLES} 件は `vintage-watcher history` で確認できます"})
    return messages


class LineNotifier(Notifier):
    def __init__(
        self,
        channel_access_token: str | None = None,
        to: str | None = None,
        client: httpx.Client | None = None,
    ):
        self.token = channel_access_token or os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
        self.to = to or os.environ.get("LINE_TO_USER_ID")
        if not (self.token and self.to):
            raise ValueError("LINE_CHANNEL_ACCESS_TOKEN / LINE_TO_USER_ID が未設定です")
        self.client = client or httpx.Client(timeout=20.0)

    def send(self, findings: list[Finding]) -> None:
        if not findings:
            return
        resp = self.client.post(
            PUSH_URL,
            headers={"Authorization": f"Bearer {self.token}"},
            json={"to": self.to, "messages": build_messages(findings)},
        )
        if resp.status_code != 200:
            raise RuntimeError(f"LINE 送信失敗: {resp.status_code} {resp.text[:300]}")
        log.info("LINE に %d 件通知しました", len(findings))
