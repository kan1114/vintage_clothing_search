from pathlib import Path

import httpx
import respx

from vintage_watcher.models import WatchQuery
from vintage_watcher.sources.ebay import SEARCH_URL, TOKEN_URL, EbaySource
from vintage_watcher.sources.instagram import InstagramSource, parse_post
from vintage_watcher.sources.yahoo_auctions import YahooAuctionsSource, parse_search_html
from vintage_watcher.store import Store

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_yahoo_search_html():
    listings = parse_search_html((FIXTURES / "yahoo_search.html").read_text(encoding="utf-8"))
    assert [l.item_id for l in listings] == ["x1234567890", "k987654321"]

    first = listings[0]
    assert first.title == "70s Levi's 501 66前期 W33 シングル"
    assert first.price == 38000
    assert first.currency == "JPY"
    assert first.image_urls == ["https://auc-pctr.c.yimg.jp/i/sample1.jpg"]
    assert first.extra == {"remaining": "2日", "bids": "3"}

    # data 属性が無いカードは表示テキスト・href から拾う
    second = listings[1]
    assert second.title == "LVC 501 66モデル 復刻"
    assert second.price == 12800
    assert second.image_urls == ["https://auc-pctr.c.yimg.jp/i/sample2.jpg"]


def test_yahoo_params_include_price_range():
    q = WatchQuery(name="q", description="d", keywords=["501"], price_min=10000, price_max=50000)
    params = YahooAuctionsSource(request_interval_sec=0).build_params("501", q)
    assert params["s1"] == "new"
    assert params["aucminprice"] == "10000"
    assert params["aucmaxprice"] == "50000"


@respx.mock
def test_ebay_search_uses_token_and_override_keywords():
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 7200}))
    search = respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "itemSummaries": [
                    {
                        "itemId": "v1|123|0",
                        "title": "Vintage 70s Levis 501 66 Single Stitch W33",
                        "price": {"value": "250.00", "currency": "USD"},
                        "image": {"imageUrl": "https://i.ebayimg.com/a.jpg"},
                        "itemWebUrl": "https://www.ebay.com/itm/123",
                        "condition": "Pre-owned",
                        "itemCreationDate": "2026-11-01T10:00:00.000Z",
                        "buyingOptions": ["FIXED_PRICE"],
                    }
                ]
            },
        )
    )
    q = WatchQuery(
        name="q",
        description="d",
        keywords=["リーバイス 501"],
        overrides={"ebay": {"keywords": ["levis 501 66"]}},
    )
    src = EbaySource(client_id="id", client_secret="secret", request_interval_sec=0)
    listings = src.search(q)

    assert search.calls.last.request.url.params["q"] == "levis 501 66"
    assert search.calls.last.request.headers["Authorization"] == "Bearer tok"
    assert len(listings) == 1
    item = listings[0]
    assert item.price == 250.0 and item.currency == "USD"
    assert item.price_in_jpy({"USD": 150}) == 37500
    assert item.listed_at.year == 2026


@respx.mock
def test_instagram_caches_hashtag_id():
    store = Store(":memory:")
    hashtag = respx.get("https://graph.facebook.com/v23.0/ig_hashtag_search").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "17841"}]})
    )
    respx.get("https://graph.facebook.com/v23.0/17841/recent_media").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "p1",
                        "caption": "新入荷！70s チャンピオン リバースウィーブ 単色タグ XL\n#reverseweave",
                        "media_type": "IMAGE",
                        "media_url": "https://scontent.cdninstagram.com/p1.jpg",
                        "permalink": "https://www.instagram.com/p/p1/",
                        "timestamp": "2026-11-03T01:23:45+0000",
                    }
                ]
            },
        )
    )
    src = InstagramSource(store=store, access_token="t", ig_user_id="u", request_interval_sec=0)
    q = WatchQuery(name="q", description="d", instagram_hashtags=["#reverseweave"], sources=["instagram"])

    assert len(src.search(q)) == 1
    assert len(src.search(q)) == 1
    assert hashtag.call_count == 1  # 2回目はキャッシュを使う


def test_instagram_video_has_no_image():
    listing = parse_post({"id": "v", "media_type": "VIDEO", "media_url": "https://x/v.mp4", "caption": ""}, "tag")
    assert listing.image_urls == []
    assert listing.title == "#tag の投稿"
