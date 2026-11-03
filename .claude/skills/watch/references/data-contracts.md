# データ契約（エージェント間のファイル形式）

エージェント同士は**会話ではなくファイルで**データを受け渡す。
オーケストレーターが受け取るのは各エージェントの短い報告だけで、大量の出品データは
`data/runs/<RUN_ID>/` 配下のファイルに置く。全エージェントがこの形式に従うこと。

すべて JSON Lines（1行1オブジェクト、UTF-8）。

## ディレクトリ

```
data/
├── ledger/<RUN_ID>.jsonl          # 台帳（既読管理）。1回の実行につき1ファイル、追記しない
├── cache/ig_hashtags.json         # Instagram ハッシュタグ名 → ID のキャッシュ
└── runs/<RUN_ID>/
    ├── raw/                       # 取得した生データ（HTML / JSON）
    ├── candidates-<source>.jsonl  # collector の出力
    ├── new.jsonl                  # curator: 台帳にない新着
    ├── triage.jsonl               # curator: 全新着の振り分け結果
    ├── to_appraise-<query>.jsonl  # curator: 鑑定に回すもの（クエリ別）
    ├── images/                    # curator がダウンロードした出品画像
    ├── appraisals-<query>.jsonl   # appraiser の出力
    ├── verdicts.jsonl             # authenticator の出力
    ├── line_payload.json          # notifier が送った（dry-run では送るはずだった）内容
    └── summary.md                 # オーケストレーターの実行まとめ
```

`<source>` は `yahoo_auctions` / `ebay` / `instagram`。

## キー

- `key` = `"<source>:<item_id>"`（例: `"yahoo_auctions:x1234567890"`）
- 既読判定は **`query` と `key` の組** で行う（同じ出品が別クエリでは欲しいものになり得るため）

## Candidate（candidates-<source>.jsonl / new.jsonl）

```json
{"key": "ebay:v1|123|0", "source": "ebay", "item_id": "v1|123|0", "query": "levis-501-66-early",
 "title": "Vintage 70s Levis 501 66", "url": "https://www.ebay.com/itm/123",
 "price": 250.0, "currency": "USD", "image_urls": ["https://i.ebayimg.com/a.jpg"],
 "description": null, "extra": {"condition": "Pre-owned"}}
```

- `price` が不明なら `null`（Instagram など）
- `description` は本文・キャプション。無ければ `null`
- `extra` はサイト固有情報（残り時間・入札数・ハッシュタグなど）。値はすべて文字列

## Triage（triage.jsonl）

```json
{"query": "levis-501-66-early", "key": "yahoo_auctions:k987", "decision": "filtered", "reason": "除外ワード「LVC」"}
```

`decision`: `appraise`（鑑定へ） / `filtered`（ルールで除外） / `deferred`（上限超過で次回へ）

## ToAppraise（to_appraise-<query>.jsonl）

Candidate に `images`（ダウンロード済みローカル画像パスの配列）を加えたもの。

## Appraisal（appraisals-<query>.jsonl）

```json
{"query": "levis-501-66-early", "key": "yahoo_auctions:x123", "is_match": true, "score": 88,
 "summary": "スモールe・シングルステッチが写真で確認できW33。条件に合致。",
 "estimated_era": "1970年代前半", "estimated_size": "W33", "red_flags": []}
```

## Verdict（verdicts.jsonl）

```json
{"query": "levis-501-66-early", "key": "yahoo_auctions:x123", "verdict": "confirmed",
 "final_score": 86, "note": "ボタン裏刻印は写真で確認できないが他の特徴は一致"}
```

`verdict`: `confirmed`（通知してよい） / `downgraded`（懸念あり、final_score を下げた）

## Ledger（data/ledger/<RUN_ID>.jsonl）

```json
{"query": "levis-501-66-early", "key": "yahoo_auctions:x123", "status": "notified", "score": 86,
 "reason": null, "title": "...", "url": "...", "run_id": "20261103T101500Z"}
```

`status`: `filtered` / `judged`（鑑定したが基準点未満） / `notified` / `skipped`（baseline）

`deferred` の出品と、エラーで処理できなかった出品は**台帳に書かない**（次回の実行で再挑戦される）。
