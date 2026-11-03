"""エージェント本体（パイプラインのオーケストレーション）。

1回の実行 (run_once) でやること:

    for 各クエリ:
        for 各ソース:
            取得 ──► 既読なら skip
                 ──► ルールで除外      → DB に filtered として記録
                 ──► Claude で判定     → DB に judged として記録
                 ──► 基準点以上なら通知候補へ
    通知候補をまとめて LINE に1通で送信 → DB を notified に更新

「エージェント」と聞くと LLM が自分で考えてツールを呼ぶループを想像しがちだが、
ここでは手順が決まっているので、流れはコードで固定し「判断が必要な1点」だけ LLM に任せている。
こうすると挙動が予測しやすく、コストも上限を決めやすく、テストも書きやすい。
（LLM に自律的にツールを選ばせる設計は、手順を事前に決められない問題で使うのが向いている）
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import anthropic

from .config import Watchlist
from .filters import rule_filter
from .judge import Judge
from .notifiers import Finding, Notifier
from .sources import Source, SourceError
from .store import Store

log = logging.getLogger(__name__)


@dataclass
class RunStats:
    fetched: int = 0
    new: int = 0
    filtered: int = 0
    judged: int = 0
    matched: int = 0
    deferred: int = 0  # 判定上限に達して次回に回した件数
    errors: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        s = (
            f"取得 {self.fetched} / 新着 {self.new} / ルール除外 {self.filtered} / "
            f"AI判定 {self.judged} / 通知 {self.matched}"
        )
        if self.deferred:
            s += f" / 次回へ繰越 {self.deferred}"
        if self.errors:
            s += f" / エラー {len(self.errors)}"
        return s


class WatchAgent:
    def __init__(
        self,
        watchlist: Watchlist,
        store: Store,
        sources: dict[str, Source],
        judge: Judge | None,
        notifier: Notifier,
    ):
        self.watchlist = watchlist
        self.settings = watchlist.settings
        self.store = store
        self.sources = sources
        self.judge = judge
        self.notifier = notifier

    def run_once(self, *, baseline: bool = False) -> RunStats:
        """1回分の巡回を実行する。

        baseline=True のときは判定・通知をせず、今出ているものを全部「既読」にするだけ。
        初回実行で既存の出品が大量に通知されるのを防ぎ、「これ以降の新着」だけを拾うために使う。
        """
        stats = RunStats()
        findings: list[Finding] = []
        budget = self.settings.judge.max_judgements_per_run
        rates = self.settings.currency_rates

        for query in self.watchlist.queries:
            for source_name in query.sources:
                source = self.sources.get(source_name)
                if source is None:
                    continue
                if source_name == "instagram" and not query.instagram_hashtags:
                    continue
                try:
                    listings = source.search(query)
                except SourceError as e:
                    # 1サイトが落ちていても他のサイトの巡回は続ける
                    msg = f"{query.name}/{source_name}: {e}"
                    log.error(msg)
                    stats.errors.append(msg)
                    continue

                stats.fetched += len(listings)
                for listing in listings:
                    if self.store.is_seen(query.name, listing):
                        continue
                    stats.new += 1

                    if baseline:
                        self.store.record(query.name, listing, "skipped", reason="baseline")
                        continue

                    reason = rule_filter(listing, query, rates)
                    if reason:
                        stats.filtered += 1
                        self.store.record(query.name, listing, "filtered", reason=reason)
                        continue

                    if self.judge is None or budget <= 0:
                        # DB に記録しないので、次回の実行で改めて判定対象になる
                        stats.deferred += 1
                        continue

                    budget -= 1
                    try:
                        judgement = self.judge.judge(listing, query)
                    except anthropic.APIError as e:
                        msg = f"判定エラー {listing.url}: {e}"
                        log.error(msg)
                        stats.errors.append(msg)
                        continue  # 記録しない → 次回リトライ

                    if judgement is None:
                        self.store.record(query.name, listing, "filtered", reason="判定不能")
                        continue

                    stats.judged += 1
                    self.store.record(query.name, listing, "judged", judgement=judgement)
                    min_score = query.min_score if query.min_score is not None else self.settings.min_score
                    if judgement.is_match and judgement.score >= min_score:
                        findings.append(Finding(query=query, listing=listing, judgement=judgement))

        if findings:
            try:
                self.notifier.send(findings)
            except Exception as e:
                # 通知に失敗したら既読を取り消し、次回の実行で拾い直せるようにする
                msg = f"通知エラー: {e}"
                log.error(msg)
                stats.errors.append(msg)
                for f in findings:
                    self.store.forget(f.query.name, f.listing)
                return stats
            for f in findings:
                self.store.record(f.query.name, f.listing, "notified", judgement=f.judgement)
        stats.matched = len(findings)
        return stats
