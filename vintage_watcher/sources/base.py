"""ソース（取得元サイト）の共通インターフェース。

新しいサイトに対応したいときは ``Source`` を継承して ``search()`` を実装し、
``sources/__init__.py`` の ``build_sources()`` に登録するだけでよい。
エージェント本体のコードは一切触らずに済む（Open/Closed 原則）。
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod

import httpx

from ..models import Listing, WatchQuery

USER_AGENT = "vintage-watcher/0.1 (personal hobby watcher; low frequency)"


class SourceError(Exception):
    """取得に失敗したことを表す。1ソースの失敗で全体を止めないために使う。"""


class Source(ABC):
    name: str

    def __init__(self, client: httpx.Client | None = None, request_interval_sec: float = 3.0):
        self.client = client or httpx.Client(
            timeout=20.0, headers={"User-Agent": USER_AGENT}, follow_redirects=True
        )
        self.request_interval_sec = request_interval_sec
        self._last_request_at = 0.0

    def throttle(self) -> None:
        """同じサイトへ連続アクセスしないよう間隔を空ける（相手への配慮 + BAN 回避）。"""
        wait = self._last_request_at + self.request_interval_sec - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_request_at = time.monotonic()

    @abstractmethod
    def search(self, query: WatchQuery) -> list[Listing]:
        """クエリに対する新着順の出品一覧を返す。"""
