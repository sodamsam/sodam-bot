# -*- coding: utf-8 -*-
"""뉴스 선정 엔진(hotnews.py v2: 한국 뉴스 + 실제 검색어) 테스트와 브리핑 연동 테스트."""
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from conftest import REAL_SEARCH_INTEREST
import auto_post
import config
import hotnews
import instagram_api
import notion_api
import threads_api

TODAY = datetime.date(2026, 11, 15)
NOW = 1_800_000_000


def art(title, source="AI타임스", age_h=3):
    return {"title": title, "source": source, "published_ts": NOW - age_h * 3600}


# ── 점수: 독자에게 쓸모 있는 소식 / 제외할 소식 ────────────────────────

@pytest.mark.parametrize("title,passes", [
    ("제미나이 4 하이드로젠 무료 확대…자동 선택 끄면 모델 바뀐다", True),
    ("[요금제 비교] 제미나이 요금제 총정리…무료부터 울트라 30만 원까지", True),
    ("소상공인 AI 바우처 신청 접수 시작", True),
    ("네이버 개인형 AI 에이전트 앱 연내 출시", True),
    ("앤트로픽, 제조업용 ‘클로드 포 매뉴팩처링’ 출시…스킬 17종", False),      # 기업용
    ("엔비디아 AI 반도체 데이터센터 투자 확대", False),                       # 기업·투자
    ("오픈AI 상장 앞두고 주가 급등", False),
    ("게임 정보 변경 기록 도박 환금성 AI 판정", False),                        # 실제로 뽑혔던 오분류
    ("트럼프 AI 규제 행정명령 서명", False),
    ("AI 해킹 피해 급증", False),
    ("오늘의 날씨와 미세먼지", False),                                        # AI와 무관
])
def test_score_threshold(title, passes):
    score, _ = hotnews.score_item(title)
    assert (score >= hotnews.MIN_SCORE) is passes


def test_local_only_news_is_penalized():
    nation, _ = hotnews.score_item("AI 교육 무료 수강생 모집 누구나 참여")
    local, why = hotnews.score_item("순천시, 컴퓨터·스마트폰·AI 무료 교육... 시민 75명 모집")
    assert local < nation and any("지역" in w for w in why)


def test_outlet_bonus_is_capped_so_press_releases_do_not_dominate():
    a, _ = hotnews.score_item("제미나이 무료 확대", outlets=2)
    b, _ = hotnews.score_item("제미나이 무료 확대", outlets=30)
    assert b - a <= 1


def test_openai_name_is_not_mistaken_for_a_launch():
    """'오픈AI'의 '오픈'이 출시(오픈)로 오인되어 AI업데이트 점수와 태그가 붙던 버그."""
    _, why = hotnews.score_item("오픈AI 보고서 공개 요금제 안내")
    assert "AI업데이트" in why                                    # '공개'는 진짜 신호
    _, why2 = hotnews.score_item("오픈AI 요금제 안내")
    assert "AI업데이트" not in why2
    _, why3 = hotnews.score_item("서울시 AI 교육장 오픈 무료 체험")
    assert "AI업데이트" in why3                                   # 진짜 '오픈'은 그대로 인정


def test_opinion_pieces_lose_points():
    plain, _ = hotnews.score_item("오픈AI 챗GPT 교육 기능 공개")
    opinion, why = hotnews.score_item("오픈AI 챗GPT 교육 기능 공개…韓도 활용 기준 실효성 높여야")
    assert opinion < plain and any("의견" in w for w in why)


def test_local_center_news_is_penalized_too():
    nation, _ = hotnews.score_item("중장년 AI 활용 교육 무료 모집")
    local, why = hotnews.score_item("김해시중장년기술창업센터, AI 활용 교육 무료 모집")
    assert local < nation and any("지역" in w for w in why)


def test_support_news_with_businesses_is_classified_as_support():
    assert hotnews.kind_of("소상공인 AI 도입 지원 추진…11월 무료 이벤트") == "support"
    assert hotnews.kind_of("제미나이 4 하이드로젠 무료 확대") == "tool"


# ── 실제 검색어(네이버 자동완성) 반영 ──────────────────────────────────

def test_search_interest_reads_the_words_people_type():
    phrases = {"챗GPT": ["챗gpt 유료 가격", "챗 GPT 무료", "챗gpt 프롬프트"], "클로드": ["클로드 요금제"]}
    interest = REAL_SEARCH_INTEREST(fetcher=lambda q: phrases.get(q, []))
    assert {"유료", "가격", "무료", "프롬프트"} <= interest["챗GPT"] and "요금제" in interest["클로드"]


def test_search_interest_failure_is_harmless():
    def boom(q):
        raise RuntimeError("네트워크 오류")

    assert REAL_SEARCH_INTEREST(fetcher=boom) == {}


def test_interest_match_adds_points_and_is_explained():
    base, _ = hotnews.score_item("클로드 새 요금제 공개")
    boosted, why = hotnews.score_item("클로드 새 요금제 공개", interest={"클로드": {"요금제", "무료"}})
    assert boosted == base + 2 and any("검색어" in w for w in why)


# ── 지난 신청 소식 제외 ───────────────────────────────────────────────

@pytest.mark.parametrize("text,gone", [
    ("종로구 무료 AI 마케팅 교육 모집, 공식 접수 마감은 9월 30일", True),
    ("AI 바우처 신청은 12월 20일까지", False),
    ("AI 교육 접수는 1월 5일까지", False),                   # 11월 기준 1월은 내년이라 아직 유효
    ("제미나이 무료 확대", False),                            # 날짜 없음
    ("9월 30일 출시된 앱 새 기능", False),                    # 마감 문맥 아님
])
def test_expired_deadlines(text, gone):
    assert hotnews.expired(text, TODAY) is gone


# ── 묶기 · 고르기 ─────────────────────────────────────────────────────

def fetcher_with(items):
    return lambda q: items


GOOD = [art("제미나이 4 하이드로젠 무료 확대…자동 선택 끄면 모델 바뀐다", "A"),
        art("제미나이 4 하이드로젠 무료 사용자도 확대 적용", "B")]
SUPPORT = [art("소상공인 AI 바우처 신청 접수 시작 최대 지원", "C")]
BAD = [art("앤트로픽 제조업용 클로드 출시 기업용 스킬", "D"), art("종로구 무료 AI 교육 모집 접수 마감은 9월 30일", "E")]


def test_similar_headlines_cluster_into_one_story():
    sizes = sorted(len(c["items"]) for c in hotnews.cluster(GOOD + SUPPORT))
    assert sizes == [1, 2]


def test_picks_the_most_useful_story_and_skips_unwanted_ones():
    item = hotnews.pick_hot_item(fetcher=fetcher_with(GOOD + SUPPORT + BAD), interest={}, today=TODAY)
    assert "제미나이" in item["title"] and item["lang"] == "ko" and item["outlets"] == 2
    assert item["kind"] == "tool" and "#제미나이" in item["tags"] and item["tags"].endswith("#소담AI랩")


def test_prefers_the_other_kind_when_scores_are_close():
    tool_first = hotnews.pick_hot_item(fetcher=fetcher_with(GOOD + SUPPORT), interest={}, today=TODAY, prefer_kind="support")
    assert tool_first["kind"] == "support" and "바우처" in tool_first["title"]
    assert hotnews.pick_hot_item(fetcher=fetcher_with(GOOD + SUPPORT), interest={}, today=TODAY, prefer_kind="tool")["kind"] == "tool"


def test_nothing_good_returns_none():
    assert hotnews.pick_hot_item(fetcher=fetcher_with(BAD), interest={}, today=TODAY) is None


def test_already_covered_story_is_skipped():
    first = hotnews.pick_hot_item(fetcher=fetcher_with(GOOD), interest={}, today=TODAY)
    assert hotnews.pick_hot_item(used_titles=[first["title"]], fetcher=fetcher_with(GOOD), interest={}, today=TODAY) is None


def test_one_failing_query_does_not_stop_the_others():
    calls = {"n": 0}

    def flaky(q):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("네트워크 오류")
        return GOOD

    assert hotnews.pick_hot_item(fetcher=flaky, interest={}, today=TODAY) is not None


def test_hashtags_come_from_the_headline_only():
    tags = hotnews.hashtags("챗GPT 요금제 할인…소상공인도 무료 체험")
    assert "#챗GPT" in tags and "#AI요금제" in tags and "#소상공인AI" in tags and "#카카오AI" not in tags


def test_news_due_spacing():
    today = datetime.date(2026, 11, 10)
    assert hotnews.news_due({}, today)
    assert not hotnews.news_due({"last_news_date": "2026-11-09"}, today)
    assert hotnews.news_due({"last_news_date": "2026-11-08"}, today)


# ── 인스타 캡션: 소식별 해시태그 ────────────────────────────────────────

def test_instagram_caption_uses_news_specific_tags():
    cap = instagram_api.build_caption("본문", "briefing", tags="#제미나이 #AI무료 #소담AI랩")
    assert cap.endswith("#제미나이 #AI무료 #소담AI랩")
    assert "#AI뉴스" in instagram_api.build_caption("본문", "briefing")          # 태그를 안 주면 기본값


# ── main(): 뉴스가 아침 글로 끼어드는지 ────────────────────────────────

BRIEFING_OK = """[분류]
무료·요금
[카드제목]
제미나이, 이제 무료로 더 많이 쓸 수 있어요
[포인트1]
무엇이 바뀌었나 | 제미나이 4 하이드로젠 무료 사용이 확대돼요
[포인트2]
누가 쓰나 | 제공 범위는 계정·국가에 따라 달라요
[포인트3]
알아둘 점 | 자동 선택을 끄면 모델이 바뀔 수 있어요
[행동라벨]
바로 해보기
[행동]
"이 보고서를 세 줄로 요약해줘"라고 부탁해 보세요
[본문]
제미나이 무료 사용이 넓어졌어요

4 하이드로젠 모델을 무료로도 쓸 수 있게 확대됐어요

제공 범위는 계정·국가에 따라 달라요

이런 AI 소식, 쉬운 말로 정리해서 알려드려요"""

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
    now = datetime.datetime.now(datetime.timezone.utc).timestamp()
    fresh = [dict(g, published_ts=now - 7200) for g in GOOD]
    monkeypatch.setattr(hotnews, "fetch_news_kr", lambda q, **k: fresh)
    monkeypatch.setattr(instagram_api, "enabled", lambda: False)
    sent = {"image": [], "text": []}
    monkeypatch.setattr(threads_api, "publish_image",
                        lambda url, text=None, wait_seconds=30, topic_tag=None: sent["image"].append((url, topic_tag)) or "img-1")
    monkeypatch.setattr(threads_api, "publish_text",
                        lambda text, reply_to_id=None, wait_seconds=None, topic_tag=None: sent["text"].append((text, topic_tag)) or "txt-1")
    return sent


def gemini_returning(briefing, other=OPEN_OK):
    return lambda prompt, max_tokens=420: briefing if "[분류]" in prompt else other


def test_news_card_is_posted_with_kind_recorded_and_cycle_untouched(run_env, monkeypatch):
    monkeypatch.setattr(auto_post, "_call_gemini", gemini_returning(BRIEFING_OK))
    auto_post.main()
    (url, tag), = run_env["image"]
    assert url.endswith(".png") and tag == "AI 뉴스"
    state = auto_post.load_posted()
    assert state["cycle_index"] == 0
    assert state["last_news_kind"] == "tool" and state["last_news_date"] and "제미나이" in state["news_used"][0]


def test_news_tags_reach_the_instagram_caption(run_env, monkeypatch):
    monkeypatch.setattr(auto_post, "_call_gemini", gemini_returning(BRIEFING_OK))
    monkeypatch.setattr(instagram_api, "enabled", lambda: True)
    got = {}
    monkeypatch.setattr(instagram_api, "publish_cards",
                        lambda urls, text, kind, tags=None: got.update(urls=urls, kind=kind, tags=tags) or "ig-1")
    auto_post.main()
    assert got["kind"] == "briefing" and got["urls"][0].endswith(".jpg")
    assert "#제미나이" in got["tags"] and got["tags"].endswith("#소담AI랩")


def test_not_due_news_falls_through_to_the_normal_morning_post(run_env, monkeypatch):
    monkeypatch.setattr(auto_post, "_call_gemini", gemini_returning(BRIEFING_OK))
    today = datetime.datetime.now(auto_post.KST).strftime("%Y-%m-%d")
    auto_post.save_posted({**auto_post.load_posted(), "last_news_date": today})
    auto_post.main()
    assert run_env["image"] == [] and run_env["text"]


def test_card_with_invented_numbers_is_dropped_and_normal_post_goes_out(run_env, monkeypatch):
    bad = BRIEFING_OK.replace("제미나이, 이제 무료로 더 많이 쓸 수 있어요", "제미나이 무료 사용량 99% 늘었어요")
    monkeypatch.setattr(auto_post, "_call_gemini", gemini_returning(bad))
    auto_post.main()
    assert run_env["image"] == [] and run_env["text"]
    state = auto_post.load_posted()
    assert state["news_used"] == [] and state["last_news_date"] == ""
