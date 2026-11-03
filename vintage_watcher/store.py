"""SQLite による状態管理。

ウォッチ系エージェントで一番大事なのは「一度見たものを二度通知しない」こと。
そのために (クエリ名, ソース, 出品ID) を主キーにして既読管理する。

クエリ単位で既読を持つのは、同じ出品が別クエリでは「欲しいもの」になり得るから。

SQLite を選んだ理由:
- ファイル1つで完結し、サーバー不要（GitHub Actions のキャッシュにも載せやすい）
- 判定結果も残るので、後から「何点で何が来たか」を SQL で振り返れる
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .models import Judgement, Listing

SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_listings (
    query_name   TEXT NOT NULL,
    source       TEXT NOT NULL,
    item_id      TEXT NOT NULL,
    title        TEXT NOT NULL,
    url          TEXT NOT NULL,
    price        REAL,
    currency     TEXT,
    first_seen   TEXT NOT NULL,
    status       TEXT NOT NULL,          -- filtered / judged / notified / skipped
    reason       TEXT,                   -- フィルタで落ちた理由など
    score        INTEGER,
    judgement    TEXT,                   -- Judgement の JSON
    PRIMARY KEY (query_name, source, item_id)
);

-- Instagram のハッシュタグID解決は API 回数制限（7日で30種類）があるのでキャッシュする
CREATE TABLE IF NOT EXISTS kv_cache (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class Store:
    def __init__(self, path: str | Path):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    # --- 既読管理 -------------------------------------------------------

    def is_seen(self, query_name: str, listing: Listing) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM seen_listings WHERE query_name=? AND source=? AND item_id=?",
            (query_name, listing.source, listing.item_id),
        ).fetchone()
        return row is not None

    def record(
        self,
        query_name: str,
        listing: Listing,
        status: str,
        *,
        reason: str | None = None,
        judgement: Judgement | None = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO seen_listings
                (query_name, source, item_id, title, url, price, currency,
                 first_seen, status, reason, score, judgement)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(query_name, source, item_id) DO UPDATE SET
                status=excluded.status,
                reason=excluded.reason,
                score=excluded.score,
                judgement=excluded.judgement
            """,
            (
                query_name,
                listing.source,
                listing.item_id,
                listing.title,
                listing.url,
                listing.price,
                listing.currency,
                datetime.now(timezone.utc).isoformat(),
                status,
                reason,
                judgement.score if judgement else None,
                judgement.model_dump_json() if judgement else None,
            ),
        )
        self.conn.commit()

    def forget(self, query_name: str, listing: Listing) -> None:
        self.conn.execute(
            "DELETE FROM seen_listings WHERE query_name=? AND source=? AND item_id=?",
            (query_name, listing.source, listing.item_id),
        )
        self.conn.commit()

    def recent(self, limit: int = 20, status: str | None = None) -> list[sqlite3.Row]:
        sql = "SELECT * FROM seen_listings"
        params: list[object] = []
        if status:
            sql += " WHERE status=?"
            params.append(status)
        sql += " ORDER BY first_seen DESC LIMIT ?"
        params.append(limit)
        return list(self.conn.execute(sql, params))

    # --- 汎用キャッシュ ---------------------------------------------------

    def cache_get(self, key: str) -> object | None:
        row = self.conn.execute("SELECT value FROM kv_cache WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else None

    def cache_set(self, key: str, value: object) -> None:
        self.conn.execute(
            "INSERT INTO kv_cache (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )
        self.conn.commit()
