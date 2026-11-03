"""ソースのレジストリ。新しいサイトを足すときはここに1行追加する。"""

from __future__ import annotations

from ..config import Settings
from ..store import Store
from .base import Source, SourceError
from .ebay import EbaySource
from .instagram import InstagramSource
from .yahoo_auctions import YahooAuctionsSource

__all__ = ["Source", "SourceError", "build_sources"]


def build_sources(names: set[str], settings: Settings, store: Store) -> dict[str, Source]:
    interval = settings.request_interval_sec
    factories = {
        "ebay": lambda: EbaySource(request_interval_sec=interval),
        "yahoo_auctions": lambda: YahooAuctionsSource(request_interval_sec=interval),
        "instagram": lambda: InstagramSource(store=store, request_interval_sec=interval),
    }
    unknown = names - factories.keys()
    if unknown:
        raise ValueError(
            f"未対応のソース: {sorted(unknown)}（対応: {sorted(factories)}）。"
            "メルカリ/ラクマについては README の「メルカリ・ラクマについて」を参照"
        )
    return {name: factories[name]() for name in sorted(names)}
