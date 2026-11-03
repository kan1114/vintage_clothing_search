"""設定の読み込み。

設定は2種類に分けている:

- watchlist.yaml : 「何を探すか」。リポジトリにコミットしてよい内容
- 環境変数       : APIキーやトークンなどの秘密情報。絶対にコミットしない

秘密情報を YAML に混ぜないことで、GitHub Actions の Secrets や .env にそのまま載せられる。
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from .models import WatchQuery


class JudgeSettings(BaseModel):
    model: str = "claude-opus-5-5"
    # effort は思考の深さとトークン消費のつまみ。1件ずつの合否判定なら low で十分なことが多い。
    effort: str = "low"
    use_images: bool = True
    max_images: int = 3
    # 1回の実行で Claude に判定させる最大件数。APIコストの安全弁。
    max_judgements_per_run: int = 40


class Settings(BaseModel):
    db_path: str = "data/watcher.db"
    min_score: int = 70
    # 通貨換算レート（1単位あたりの円）。価格フィルタを円で統一するため。
    currency_rates: dict[str, float] = Field(default_factory=lambda: {"USD": 150.0})
    # 同じサイトへのリクエスト間隔（秒）。相手サーバーへの負荷を抑えるための礼儀。
    request_interval_sec: float = 3.0
    judge: JudgeSettings = Field(default_factory=JudgeSettings)


class Watchlist(BaseModel):
    settings: Settings = Field(default_factory=Settings)
    queries: list[WatchQuery]


def load_watchlist(path: str | Path) -> Watchlist:
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return Watchlist.model_validate(raw)


def load_dotenv(path: str | Path = ".env") -> None:
    """依存を増やさないための最小限の .env ローダー（既存の環境変数は上書きしない）。"""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
