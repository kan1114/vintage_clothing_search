"""コマンドラインの入口。

    vintage-watcher run                 # 1回巡回して、見つかったら LINE 通知
    vintage-watcher run --dry-run       # 通知せず結果を画面に出す（動作確認用）
    vintage-watcher run --baseline      # 今ある出品を既読にするだけ（初回に1回）
    vintage-watcher watch --interval 60 # 60分ごとに巡回し続ける（常駐用）
    vintage-watcher history             # 最近の判定結果を表示
    vintage-watcher test-line           # LINE 通知のテスト送信
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time

from .agent import WatchAgent
from .config import Watchlist, load_dotenv, load_watchlist
from .judge import Judge
from .models import Judgement, Listing, WatchQuery
from .notifiers import ConsoleNotifier, Finding, LineNotifier, Notifier
from .sources import build_sources
from .store import Store


def _build_agent(args: argparse.Namespace, watchlist: Watchlist, store: Store) -> WatchAgent:
    names = {s for q in watchlist.queries for s in q.sources}
    sources = build_sources(names, watchlist.settings, store)
    judge = None if args.baseline else Judge(watchlist.settings.judge, watchlist.settings.currency_rates)
    notifier: Notifier = ConsoleNotifier() if args.dry_run else LineNotifier()
    return WatchAgent(watchlist, store, sources, judge, notifier)


def cmd_run(args: argparse.Namespace) -> int:
    watchlist = load_watchlist(args.config)
    store = Store(watchlist.settings.db_path)
    try:
        stats = _build_agent(args, watchlist, store).run_once(baseline=args.baseline)
    finally:
        store.close()
    print(stats)
    return 1 if stats.errors and stats.fetched == 0 else 0


def cmd_watch(args: argparse.Namespace) -> int:
    while True:
        try:
            cmd_run(args)
        except Exception:  # 常駐モードでは1回の失敗で落とさない
            logging.exception("巡回中にエラーが発生しました")
        time.sleep(args.interval * 60)


def cmd_history(args: argparse.Namespace) -> int:
    watchlist = load_watchlist(args.config)
    store = Store(watchlist.settings.db_path)
    for row in store.recent(limit=args.limit, status=args.status):
        score = f"{row['score']:>3}点" if row["score"] is not None else "  - "
        print(f"{row['first_seen'][:16]} [{row['status']:<8}] {score} ({row['query_name']}/{row['source']}) {row['title']}")
        if row["judgement"]:
            print(f"    {json.loads(row['judgement'])['summary']}")
        elif row["reason"]:
            print(f"    {row['reason']}")
        print(f"    {row['url']}")
    store.close()
    return 0


def cmd_test_line(args: argparse.Namespace) -> int:
    finding = Finding(
        query=WatchQuery(name="test", description="テスト"),
        listing=Listing(source="ebay", item_id="test", title="テスト通知: Levi's 501 66前期", url="https://www.ebay.com/", price=42000, currency="JPY"),
        judgement=Judgement(is_match=True, score=95, summary="これはテスト通知です。届いていれば設定は完了です。"),
    )
    LineNotifier().send([finding])
    print("送信しました")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vintage-watcher", description="ヴィンテージ古着ウォッチャー")
    parser.add_argument("-c", "--config", default="watchlist.yaml", help="ウォッチリストのパス")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    for name, help_text in [("run", "1回巡回する"), ("watch", "一定間隔で巡回し続ける")]:
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--dry-run", action="store_true", help="LINE に送らず画面に表示")
        p.add_argument("--baseline", action="store_true", help="判定せず現在の出品を既読にする")
        if name == "watch":
            p.add_argument("--interval", type=int, default=60, help="巡回間隔（分）")

    p = sub.add_parser("history", help="最近の記録を表示")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--status", choices=["filtered", "judged", "notified", "skipped"])

    sub.add_parser("test-line", help="LINE にテスト通知を送る")

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    load_dotenv()
    handlers = {"run": cmd_run, "watch": cmd_watch, "history": cmd_history, "test-line": cmd_test_line}
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
