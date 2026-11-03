"""パイプライン全体のテスト。外部 API は全部偽物に差し替える。"""

import json
from types import SimpleNamespace

import anthropic
import httpx
import httpx2  # anthropic SDK 2.x が内部で使う HTTP クライアント
import pytest
import respx

from vintage_watcher.agent import WatchAgent
from vintage_watcher.config import JudgeSettings, Settings, Watchlist
from vintage_watcher.filters import rule_filter
from vintage_watcher.judge import Judge
from vintage_watcher.models import Judgement, Listing, WatchQuery
from vintage_watcher.notifiers import PUSH_URL, Finding, LineNotifier, Notifier, build_messages
from vintage_watcher.sources.base import Source, SourceError
from vintage_watcher.store import Store


def make_listing(item_id: str, title: str = "Levi's 501 66前期", price: float | None = 30000, **kw) -> Listing:
    return Listing(
        source="fake",
        item_id=item_id,
        title=title,
        url=f"https://example.com/{item_id}",
        price=price,
        currency="JPY",
        image_urls=["https://example.com/a.jpg"],
        **kw,
    )


QUERY = WatchQuery(
    name="levis",
    description="66前期を探しています",
    keywords=["501"],
    exclude_words=["ＬＶＣ"],
    price_max=100000,
    sources=["fake"],
)


class FakeSource(Source):
    name = "fake"

    def __init__(self, listings):
        self.listings = listings

    def search(self, query):
        return self.listings


class FakeJudge:
    def __init__(self, scores):
        self.scores = scores
        self.calls = []

    def judge(self, listing, query):
        self.calls.append(listing.item_id)
        score = self.scores[listing.item_id]
        return Judgement(is_match=score >= 50, score=score, summary=f"score {score}")


class RecordingNotifier(Notifier):
    def __init__(self, fail=False):
        self.sent: list[list[Finding]] = []
        self.fail = fail

    def send(self, findings):
        if self.fail:
            raise RuntimeError("LINE down")
        self.sent.append(findings)


def make_agent(listings, scores, notifier=None, **settings):
    store = Store(":memory:")
    wl = Watchlist(settings=Settings(**settings), queries=[QUERY])
    judge = FakeJudge(scores)
    agent = WatchAgent(wl, store, {"fake": FakeSource(listings)}, judge, notifier or RecordingNotifier())
    return agent, store, judge


# --- フィルタ ---------------------------------------------------------------


@pytest.mark.parametrize(
    "listing, expected",
    [
        (make_listing("1", title="Levi's 501 LVC 復刻"), "除外ワード"),  # 全角の除外語でも半角にヒット
        (make_listing("2", price=200000), "上限超過"),
        (make_listing("3", price=None), None),  # 価格不明は通す
        (make_listing("4"), None),
    ],
)
def test_rule_filter(listing, expected):
    reason = rule_filter(listing, QUERY, {})
    assert (reason is None) if expected is None else (expected in reason)


def test_usd_price_is_converted_before_filter():
    listing = make_listing("1", price=800).model_copy(update={"currency": "USD"})
    assert "上限超過" in rule_filter(listing, QUERY, {"USD": 150})


# --- パイプライン ----------------------------------------------------------


def test_pipeline_filters_judges_notifies_and_dedupes():
    listings = [make_listing("good"), make_listing("meh"), make_listing("lvc", title="LVC 501")]
    agent, store, judge = make_agent(listings, {"good": 90, "meh": 40})

    stats = agent.run_once()
    assert (stats.new, stats.filtered, stats.judged, stats.matched) == (3, 1, 2, 1)
    assert [f.listing.item_id for f in agent.notifier.sent[0]] == ["good"]
    assert {r["item_id"]: r["status"] for r in store.recent()} == {"good": "notified", "meh": "judged", "lvc": "filtered"}

    # 2回目は全部既読なので何もしない
    stats = agent.run_once()
    assert stats.new == 0 and len(judge.calls) == 2 and len(agent.notifier.sent) == 1


def test_baseline_marks_seen_without_judging():
    agent, store, judge = make_agent([make_listing("a")], {"a": 99})
    agent.run_once(baseline=True)
    assert judge.calls == [] and agent.notifier.sent == []
    assert agent.run_once().new == 0


def test_judgement_budget_defers_to_next_run():
    listings = [make_listing(str(i)) for i in range(3)]
    agent, store, judge = make_agent(
        listings, {str(i): 90 for i in range(3)}, judge=JudgeSettings(max_judgements_per_run=2)
    )
    stats = agent.run_once()
    assert stats.judged == 2 and stats.deferred == 1
    stats = agent.run_once()
    assert stats.judged == 1 and stats.deferred == 0


def test_notification_failure_allows_retry():
    agent, store, judge = make_agent([make_listing("good")], {"good": 90}, notifier=RecordingNotifier(fail=True))
    stats = agent.run_once()
    assert stats.errors and store.recent() == []
    agent.notifier.fail = False
    assert agent.run_once().matched == 1


def test_source_error_does_not_stop_run():
    class Broken(Source):
        name = "fake"

        def __init__(self):
            pass

        def search(self, query):
            raise SourceError("boom")

    store = Store(":memory:")
    agent = WatchAgent(Watchlist(queries=[QUERY]), store, {"fake": Broken()}, FakeJudge({}), RecordingNotifier())
    stats = agent.run_once()
    assert stats.errors == ["levis/fake: boom"]


# --- Judge（Anthropic クライアントを偽物に） ---------------------------------


class FakeMessages:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def parse(self, **kwargs):
        self.requests.append(kwargs)
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def fake_client(*responses):
    return SimpleNamespace(beta=SimpleNamespace(messages=FakeMessages(responses)))


def ok_response(score=88):
    return SimpleNamespace(
        stop_reason="end_turn",
        parsed_output=Judgement(is_match=True, score=score, summary="良さそう"),
    )


def test_judge_sends_images_and_structured_output():
    client = fake_client(ok_response())
    judge = Judge(JudgeSettings(), {"USD": 150}, client=client)
    result = judge.judge(make_listing("1"), QUERY)

    assert result.score == 88
    req = client.beta.messages.requests[0]
    assert req["model"] == "claude-opus-5-5"
    assert req["output_format"] is Judgement
    assert req["fallbacks"] == "default"
    content = req["messages"][0]["content"]
    assert content[0] == {"type": "image", "source": {"type": "url", "url": "https://example.com/a.jpg"}}
    assert "66前期を探しています" in content[-1]["text"]


def test_judge_retries_without_images_on_bad_request():
    bad = anthropic.BadRequestError(
        "image fetch failed",
        response=httpx2.Response(400, request=httpx2.Request("POST", "https://api.anthropic.com")),
        body=None,
    )
    client = fake_client(bad, ok_response())
    judge = Judge(JudgeSettings(), {}, client=client)
    assert judge.judge(make_listing("1"), QUERY).score == 88
    second = client.beta.messages.requests[1]["messages"][0]["content"]
    assert all(block["type"] == "text" for block in second)


def test_judge_request_body_with_real_sdk():
    """本物の SDK を通し、API に送られる JSON の形を確認する（通信はモック）。"""
    captured = {}

    def handler(request):
        captured["request"] = request
        body = {"is_match": True, "score": 91, "summary": "ok", "estimated_era": None, "estimated_size": "W33", "red_flags": []}
        return httpx2.Response(
            200,
            json={
                "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
                "stop_reason": "end_turn", "stop_sequence": None,
                "content": [{"type": "text", "text": json.dumps(body)}],
                "usage": {"input_tokens": 10, "output_tokens": 10},
            },
        )

    client = anthropic.Anthropic(
        api_key="test", base_url="https://mock.invalid", http_client=httpx2.Client(transport=httpx2.MockTransport(handler))
    )
    result = Judge(JudgeSettings(), {}, client=client).judge(make_listing("1"), QUERY)
    assert result.estimated_size == "W33"

    req = captured["request"]
    assert req.headers["anthropic-beta"] == "server-side-fallback-2026-07-01"
    body = json.loads(req.content)
    assert body["thinking"] == {"type": "adaptive"}
    assert body["output_config"]["effort"] == "low"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["fallbacks"] == "default"


def test_judge_returns_none_on_refusal():
    client = fake_client(SimpleNamespace(stop_reason="refusal", parsed_output=None))
    assert Judge(JudgeSettings(), {}, client=client).judge(make_listing("1"), QUERY) is None


# --- LINE 通知 -------------------------------------------------------------


def _finding(i, score):
    return Finding(QUERY, make_listing(str(i)), Judgement(is_match=True, score=score, summary="s"))


def test_line_messages_are_one_carousel_sorted_by_score():
    messages = build_messages([_finding(1, 70), _finding(2, 95)])
    assert len(messages) == 1
    carousel = messages[0]["contents"]
    assert carousel["type"] == "carousel"
    assert carousel["contents"][0]["footer"]["contents"][0]["action"]["uri"] == "https://example.com/2"
    assert carousel["contents"][0]["hero"]["url"] == "https://example.com/a.jpg"


def test_line_carousel_caps_at_12_bubbles():
    messages = build_messages([_finding(i, 80) for i in range(15)])
    assert len(messages[0]["contents"]["contents"]) == 12
    assert "ほか 3 件" in messages[1]["text"]


@respx.mock
def test_line_notifier_posts_push():
    route = respx.post(PUSH_URL).mock(return_value=httpx.Response(200, json={}))
    LineNotifier(channel_access_token="tok", to="U123").send([_finding(1, 90)])
    req = route.calls.last.request
    assert req.headers["Authorization"] == "Bearer tok"
    assert json.loads(req.content)["to"] == "U123"
