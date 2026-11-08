# -*- coding: utf-8 -*-
"""핫뉴스(hotnews.py) 선정·검증 + 브리핑 파이프라인 연동 테스트."""
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import auto_post
import config
import hotnews
import notion_api
import threads_api

NOW = 1_800_000_000


def art(title, source, age_h=3):
    return {"title": title, "source": source, "published_ts": NOW - age_h * 3600}


# ── 소식 판별 ─────────────────────────────────────────────────────

@pytest.mark.parametrize("title,ok", [
    ("Anthropic Launches Claude Personal Finance Ahead of Nasdaq Listing", True),
    ("Google adds prompt-based video revisions to Gemini", True),
    ("ChatGPT rolls out free voice mode update", True),
    ("Man sues OpenAI and claims chatbot plotted against him", False),
    ("Suit alleges ChatGPT 'manipulated and encouraged' wife in plot to poison husband", False),  # 실제로 뽑혔던 오분류
    ("OpenAI bans accounts using ChatGPT for scams", True),
    ("Husband tells ChatGPT about family trip", False),                                        # husband의 "ban" 오탐 방지
    ("OpenAI Secures $11B In Funding As Valuation Reaches $1.25T", False),
    ("Meta CSO jumps to AI startup as CFO", False),
    ("Local bakery launches new sourdough menu", False),            # AI와 무관
    ("OpenAI chief talks about the weather", False),                 # AI지만 쓸모 신호 없음
])
def test_audience_filter(title, ok):
    assert hotnews.audience_ok(title) is ok


def test_similar_headlines_cluster_into_one_story():
    items = [art("Anthropic Launches Claude Personal Finance Ahead of Nasdaq Listing", "A"),
             art("Anthropic launches Claude Personal Finance tool", "B"),
             art("Claude Personal Finance launches from Anthropic", "C"),
             art("Google adds prompt-based video revisions to Gemini", "D")]
    sizes = sorted(len(c["items"]) for c in hotnews.cluster(items))
    assert sizes == [1, 3]


# ── 가장 인기 있는 소식 고르기 ─────────────────────────────────────

def fetcher_with(items):
    return lambda q: items


BIG = [art("Anthropic Launches Claude Personal Finance tool", s) for s in ("TechCrunch", "The Verge", "Wired", "CNET", "ZDNet")]
SMALL = [art("Google adds prompt-based video revisions to Gemini app", s) for s in ("Engadget", "9to5Google")]
LAWSUIT = [art("Man sues OpenAI after chatbot launches harmful advice", f"Outlet{i}") for i in range(8)]


def test_picks_the_story_covered_by_most_outlets_and_skips_unwanted_ones():
    item = hotnews.pick_hot_item(fetcher=fetcher_with(BIG + SMALL + LAWSUIT))
    assert item["outlets"] == 5 and item["lang"] == "en"
    assert "Claude Personal Finance" in item["title"]
    assert item["source"].startswith("TechCrunch") and "외" in item["source"]       # 상위 매체 + 외 N곳
    assert item["published_ts"] == NOW - 3 * 3600


def test_needs_enough_outlets():
    assert hotnews.pick_hot_item(fetcher=fetcher_with(SMALL)) is None                 # 2곳뿐이면 '인기'가 아니다
    assert hotnews.pick_hot_item(fetcher=fetcher_with(SMALL), min_outlets=2) is not None


def test_already_covered_story_is_skipped():
    first = hotnews.pick_hot_item(fetcher=fetcher_with(BIG))
    assert hotnews.pick_hot_item(used_titles=[first["title"]], fetcher=fetcher_with(BIG)) is None


def test_one_failing_query_does_not_stop_the_others():
    calls = {"n": 0}

    def flaky(q):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("네트워크 오류")
        return BIG

    assert hotnews.pick_hot_item(fetcher=flaky) is not None


# ── 영어 근거 검증 ────────────────────────────────────────────────

SRC = "Anthropic launches Claude Personal Finance 5 outlets"


def test_fabrication_check_for_english_sources():
    assert hotnews.find_fabrications_en("Claude에 개인 재무 기능이 생겼어요. AI가 도와줘요", SRC) == []
    assert "99" in hotnews.find_fabrications_en("가격은 99달러예요", SRC)               # 근거에 없는 숫자
    assert "GPT" in hotnews.find_fabrications_en("GPT와 비교해 보세요", SRC)            # 근거에 없는 영문 이름
    assert "5" not in hotnews.find_fabrications_en("5곳이 다뤘어요", SRC)               # 근거에 있는 숫자는 허용


def test_news_due_spacing():
    today = datetime.date(2026, 11, 10)
    assert hotnews.news_due({}, today)
    assert not hotnews.news_due({"last_news_date": "2026-11-09"}, today)
    assert hotnews.news_due({"last_news_date": "2026-11-08"}, today)


# ── main(): 핫뉴스가 아침 글로 끼어드는지 ──────────────────────────

BRIEFING_OK = """[분류]
신규 출시
[카드제목]
Claude에 개인 재무 기능이 생겼어요
[포인트1]
무엇이 나왔나 | Claude에 개인 재무 도우미 기능이 나왔어요
[포인트2]
누가 쓰나 | 제공 범위는 계정·국가에 따라 달라요
[포인트3]
알아둘 점 | 중요한 돈 문제는 직접 한 번 더 확인하세요
[행동라벨]
바로 해보기
[행동]
"이번 달 지출을 표로 정리해줘"라고 부탁해 보세요
[본문]
Claude에 개인 재무 기능이 나왔어요

여러 매체가 같은 소식을 전했어요

제공 범위는 계정·국가에 따라 달라요

이런 소식, 매주 한 번씩 정리해서 알려드려요"""

OPEN_OK = "[후킹]\n회의 메모가 엉망이신가요\n[프롬프트]\n아래를 정리해줘 [여기에 붙여넣기] 없는 내용은 지어내지 마\n[예시]\n이렇게 정리돼요"


@pytest.fixture
def run_env(tmp_path, monkeypatch):
    for name in ("THREADS_ACCESS_TOKEN", "NOTION_TOKEN", "NOTION_DATABASE_ID", "GEMINI_API_KEY"):
        monkeypatch.setattr(config, name, "x")
    monkeypatch.setattr(notion_api, "get_pages", lambda: [])
    monkeypatch.setattr(auto_post, "fetch_news", lambda *a, **k: [])
    monkeypatch.setattr(auto_post.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(auto_post, "_card_dir", lambda: str(tmp_path / "cards"))
    monkeypatch.setattr(auto_post, "_publish_card_files", lambda paths: [f"https://img/{os.path.basename(p)}" for p in paths])
    monkeypatch.setenv("FORCE_WINDOW", "force")
    monkeypatch.delenv("POST_TYPE", raising=False)
    monkeypatch.delenv("DRY_RUN", raising=False)
    monkeypatch.setattr(hotnews, "fetch_global", lambda q, **k: BIG)
    sent = {"image": [], "text": []}
    monkeypatch.setattr(threads_api, "publish_image",
                        lambda url, text=None, wait_seconds=30, topic_tag=None: sent["image"].append((url, topic_tag)) or "img-1")
    monkeypatch.setattr(threads_api, "publish_text",
                        lambda text, reply_to_id=None, wait_seconds=None, topic_tag=None: sent["text"].append((text, topic_tag)) or "txt-1")
    return sent


def gemini_returning(briefing, other=OPEN_OK):
    return lambda prompt, max_tokens=420: briefing if "[분류]" in prompt else other


def test_hot_news_card_is_posted_without_moving_the_cycle(run_env, monkeypatch):
    monkeypatch.setattr(auto_post, "_call_gemini", gemini_returning(BRIEFING_OK))
    auto_post.main()
    (url, tag), = run_env["image"]
    assert url.endswith(".png") and tag == "AI 뉴스"
    state = auto_post.load_posted()
    assert state["cycle_index"] == 0                                    # 순환 큐(프롬프트·활용법) 순서는 그대로
    assert state["last_news_date"] and "Claude Personal Finance" in state["news_used"][0]
    assert list(state["posted"].values())[0]["type"] == "briefing"


def test_not_due_news_falls_through_to_the_normal_morning_post(run_env, monkeypatch):
    monkeypatch.setattr(auto_post, "_call_gemini", gemini_returning(BRIEFING_OK))
    today = datetime.datetime.now(auto_post.KST).strftime("%Y-%m-%d")
    auto_post.save_posted({**auto_post.load_posted(), "last_news_date": today})
    auto_post.main()
    assert run_env["image"] == [] and run_env["text"]                   # 오늘 이미 뉴스를 올렸으니 일반 글


def test_unverifiable_hot_news_is_dropped_and_normal_post_goes_out(run_env, monkeypatch):
    bad = BRIEFING_OK.replace("Claude에 개인 재무 기능이 생겼어요", "GPT-9보다 99% 빨라졌어요")
    monkeypatch.setattr(auto_post, "_call_gemini", gemini_returning(bad))
    auto_post.main()
    assert run_env["image"] == [] and run_env["text"]
    state = auto_post.load_posted()
    assert state["news_used"] == [] and state["last_news_date"] == ""    # 실패한 뉴스는 기록하지 않는다
    assert state["cycle_index"] == 1                                    # 일반 글이 순환 큐 한 칸을 썼다
