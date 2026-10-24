# -*- coding: utf-8 -*-
"""하루 2회 발행 · 저녁 코너(corners.py) · 주제 태그 · 여러 장 카드 · 미리보기 모드 테스트."""
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from PIL import Image

import auto_post
import card_news
import config
import corners
import notion_api
import threads_api

KST = auto_post.KST


def kst(y, m, d, h=19):
    return datetime.datetime(y, m, d, h, 0, tzinfo=KST)


class Resp:
    def __init__(self, data=None, ok=True, status=200, text=""):
        self._data, self.ok, self.status_code, self.text = data or {}, ok, status, text

    def json(self):
        return self._data


# ── 시간대 → 슬롯 판정 ────────────────────────────────────────────

def test_primary_window_posts_morning_once():
    now = kst(2026, 10, 26, 7)
    slot, key, _ = auto_post.decide_slot(now, {"posted": {}}, "primary")
    assert (slot, key) == ("morning", "2026-10-26")
    slot, key, _ = auto_post.decide_slot(now, {"posted": {"2026-10-26": {}}}, "primary")
    assert slot is None


def test_backup_window_posts_evening_corner_after_morning():
    now = kst(2026, 10, 26, 19)
    slot, key, _ = auto_post.decide_slot(now, {"posted": {"2026-10-26": {}}}, "backup")
    assert (slot, key) == ("evening", "2026-10-26-evening")


def test_backup_window_catches_up_missed_morning_without_double_post():
    now = kst(2026, 10, 26, 19)
    slot, key, _ = auto_post.decide_slot(now, {"posted": {}}, "backup")
    assert (slot, key) == ("morning", "2026-10-26")   # 아침을 놓치면 예전처럼 대신 발행(저녁 코너는 그날 쉼)


def test_backup_window_skips_when_both_done():
    now = kst(2026, 10, 26, 19)
    state = {"posted": {"2026-10-26": {}, "2026-10-26-evening": {}}}
    assert auto_post.decide_slot(now, state, "backup")[0] is None


# ── 혜택 글 중복 방지 ─────────────────────────────────────────────

def test_benefit_item_already_posted_is_not_picked_again(monkeypatch):
    item = {"title": "AI 바우처 신설 1000명 모집 마감 임박", "summary": "과기정통부는 신청을 받는다", "published_ts": 1}
    monkeypatch.setattr(auto_post, "fetch_news", lambda *a, **k: [item])
    assert auto_post.pick_benefit_item("2026-10-26")["title"] == item["title"]
    assert auto_post.pick_benefit_item("2026-10-26", used_titles=[item["title"]]) is None


# ── Threads API: 주제 태그 · 여러 장 카드 ───────────────────────────

@pytest.fixture
def api(monkeypatch):
    monkeypatch.setattr(config, "THREADS_ACCESS_TOKEN", "t")
    monkeypatch.setattr(threads_api.time, "sleep", lambda *a, **k: None)
    calls = {"post": [], "get": []}
    counter = {"n": 0}

    def fake_post(url, params=None, timeout=None):
        calls["post"].append((url, dict(params)))
        if calls.get("reject_tag") and "topic_tag" in params:
            return Resp(ok=False, status=400, text="invalid topic_tag")
        counter["n"] += 1
        return Resp({"id": f"id{counter['n']}"})

    def fake_get(url, params=None, timeout=None):
        calls["get"].append((url, dict(params)))
        return Resp({"status": calls.get("status", "FINISHED")})

    monkeypatch.setattr(threads_api.requests, "post", fake_post)
    monkeypatch.setattr(threads_api.requests, "get", fake_get)
    return calls


def test_clean_topic_tag_rules():
    assert threads_api.clean_topic_tag("AI 프롬프트") == "AI 프롬프트"
    assert threads_api.clean_topic_tag("A.I & #프롬프트") == "AI  프롬프트"
    assert "." not in threads_api.clean_topic_tag("A.I") and "&" not in threads_api.clean_topic_tag("A&I")
    assert threads_api.clean_topic_tag("") is None and threads_api.clean_topic_tag(None) is None
    assert len(threads_api.clean_topic_tag("가" * 80)) == 50


def test_publish_text_sends_topic_tag(api):
    threads_api.publish_text("안녕", topic_tag="AI 용어")
    create = api["post"][0][1]
    assert create["topic_tag"] == "AI 용어" and create["media_type"] == "TEXT"


def test_replies_never_get_topic_tag(api):
    threads_api.publish_text("답글", reply_to_id="c1", topic_tag="AI 용어")
    assert "topic_tag" not in api["post"][0][1]


def test_rejected_topic_tag_retries_without_it(api):
    api["reject_tag"] = True
    post_id = threads_api.publish_text("글", topic_tag="AI 용어")
    assert post_id
    creates = [p for p in api["post"] if p[0].endswith("/me/threads")]
    assert "topic_tag" in creates[0][1] and "topic_tag" not in creates[1][1]


def test_publish_carousel_builds_children_then_parent(api):
    threads_api.publish_carousel(["u1", "u2", "u3"], "본문", topic_tag="AI 프롬프트")
    creates = [p[1] for p in api["post"] if p[0].endswith("/me/threads")]
    assert [c.get("is_carousel_item") for c in creates[:3]] == ["true"] * 3
    parent = creates[3]
    assert parent["media_type"] == "CAROUSEL" and parent["children"] == "id1,id2,id3"
    assert parent["text"] == "본문" and parent["topic_tag"] == "AI 프롬프트"
    assert api["post"][-1][0].endswith("/threads_publish")


def test_publish_carousel_needs_two_images(api):
    with pytest.raises(ValueError):
        threads_api.publish_carousel(["only-one"], "x")


def test_publish_carousel_stops_on_container_error(api):
    api["status"] = "ERROR"
    with pytest.raises(RuntimeError):
        threads_api.publish_carousel(["u1", "u2"], "x")
    assert not any(p[0].endswith("/threads_publish") for p in api["post"])   # 발행 전에 멈춰 게시물이 생기지 않는다


# ── 저녁 코너: 요일 · 검증 · 글 만들기 ───────────────────────────────

def test_weekday_corner_calendar_covers_every_day():
    base = datetime.datetime(2026, 10, 26, tzinfo=KST)   # 월요일
    kinds = [corners.pick_kind(base + datetime.timedelta(days=i)) for i in range(7)]
    assert kinds == ["card_prompt", "term", "mistake", "card_prompt", "tip", "engage", "mistake"]
    assert set(kinds) == set(corners.EVENING_KINDS)


def test_every_topic_bank_has_a_basis_sentence():
    for bank in (corners.TERMS, corners.MISTAKES, corners.TIPS):
        assert len(bank) >= 20
        for title, basis in bank:
            assert title.strip() and len(basis) >= 10


@pytest.mark.parametrize("bad", ["효과가 30% 늘어요", "연구에 따르면 좋아요", "수강생분이 말씀하셨어요",
                                 "https://example.com 참고", "#해시태그 달기", "통계로 보면"])
def test_find_problems_flags_unverifiable_claims(bad):
    assert corners.find_problems(bad)


def test_find_problems_allows_plain_helpful_text():
    assert corners.find_problems('이렇게 말해보세요.\n"메모를 세 줄로 정리해줘"') == []


def fake_gemini_factory(responses):
    seen = []

    def fake(prompt, max_tokens=420):
        seen.append(prompt)
        r = responses.pop(0) if len(responses) > 1 else responses[0]
        return r(prompt) if callable(r) else r
    fake.seen = seen
    return fake


GOOD_TERM = "이 말, 들어보셨죠\n\n쉽게 말해 AI가 읽는 글자 조각이에요\n\n어려운 AI 말, 하나씩 쉽게 풀어드릴게요"


def test_term_post_uses_series_number_tag_and_basis(monkeypatch):
    fake = fake_gemini_factory([GOOD_TERM])
    monkeypatch.setattr(auto_post, "_call_gemini", fake)
    state = {"corner_seq": {"term": 4}}
    r = corners.write_evening_post(state, kst(2026, 10, 27), forced="term")
    assert r["kind"] == "term" and r["number"] == 5 and r["tag"] == "AI 용어" and r["card"] is None
    assert r["text"].startswith("📖 AI 한 줄 용어 #5")
    assert r["topic"] == corners.TERMS[4][0]
    assert corners.TERMS[4][1] in fake.seen[0]       # 사람이 적어둔 뜻이 프롬프트에 근거로 들어간다


def test_unverifiable_claims_trigger_one_retry_then_fallback(monkeypatch):
    bad = "연구에 따르면 시간이 30% 줄어요"
    fake = fake_gemini_factory([bad, GOOD_TERM])
    monkeypatch.setattr(auto_post, "_call_gemini", fake)
    assert corners.write_evening_post({}, kst(2026, 10, 27), forced="term")["text"]
    assert len(fake.seen) == 2

    monkeypatch.setattr(auto_post, "_call_gemini", fake_gemini_factory([bad]))
    assert corners.write_evening_post({}, kst(2026, 10, 27), forced="term") is None


CARD_OK = """[카드제목]
회의록 정리, 이 문장 하나로 끝나요
[상황]
회의 메모가 엉망일 때
[프롬프트]
아래 회의 메모를 정리해줘.
표로 보여주고 없는 내용은 지어내지 마.
[여기에 회의 메모 붙여넣기]
[결과예시]
결정된 일: 문구는 이번 주 확정
[팁]
마음에 안 들면 더 짧게 부탁해 보세요
[후킹]
회의 끝나고 메모가 엉망이신가요"""


def test_card_prompt_post_builds_card_and_short_body(monkeypatch):
    monkeypatch.setattr(auto_post, "_call_gemini", fake_gemini_factory([CARD_OK]))
    r = corners.write_evening_post({}, kst(2026, 10, 26), forced="card_prompt")
    assert r["kind"] == "card_prompt" and r["tag"] == "AI 프롬프트"
    assert r["card"]["series"] == "복붙 프롬프트 #1" and "[여기에 회의 메모 붙여넣기]" in r["card"]["prompt"]
    assert "[여기에 회의 메모 붙여넣기]" in r["text"] and len(r["text"]) <= 490


def test_card_prompt_without_placeholder_or_markers_is_rejected(monkeypatch):
    no_placeholder = CARD_OK.replace("[여기에 회의 메모 붙여넣기]", "회의 메모를 붙여넣으세요")
    monkeypatch.setattr(auto_post, "_call_gemini", fake_gemini_factory([no_placeholder]))
    assert corners.write_evening_post({}, kst(2026, 10, 26), forced="card_prompt") is None
    monkeypatch.setattr(auto_post, "_call_gemini", fake_gemini_factory(["아무 형식 없는 답"]))
    assert corners.write_evening_post({}, kst(2026, 10, 26), forced="card_prompt") is None


def test_long_prompt_never_gets_cut_in_body(monkeypatch):
    long_prompt = CARD_OK.replace("표로 보여주고", "아주 길게 " * 60 + "표로 보여주고")
    monkeypatch.setattr(auto_post, "_call_gemini", fake_gemini_factory([long_prompt]))
    r = corners.write_evening_post({}, kst(2026, 10, 26), forced="card_prompt")
    assert len(r["text"]) <= 490
    assert ("복사할 문장은 카드" in r["text"]) or ("아주 길게" in r["text"] and "붙여넣기]" in r["text"])


# ── 카드 이미지 ──────────────────────────────────────────────────

def test_prompt_slides_render_four_images(tmp_path):
    data = {"series": "복붙 프롬프트 #1", "headline": "회의록 정리, 이 문장 하나로 끝나요", "situation": "메모가 엉망일 때",
            "prompt": "아래 회의 메모를 정리해줘\n[여기에 붙여넣기]", "example": "결정된 일: 확정", "tip": "더 짧게 부탁해 보세요"}
    paths = card_news.generate_prompt_slides(data, str(tmp_path), "p")
    assert len(paths) == 4
    for p in paths:
        assert Image.open(p).size == (1080, 1350)


def test_prompt_slides_survive_very_long_text(tmp_path):
    data = {"series": "복붙 프롬프트 #99", "headline": "아주 긴 제목 " * 12, "situation": "상황 " * 60,
            "prompt": "매우 긴 프롬프트 줄\n" * 40, "example": "결과 " * 200, "tip": "팁 " * 100}
    assert len(card_news.generate_prompt_slides(data, str(tmp_path), "long")) == 4


# ── main(): 저녁 슬롯 · 대체 · 미리보기 ────────────────────────────

@pytest.fixture
def run_env(tmp_path, monkeypatch):
    for name, val in (("THREADS_ACCESS_TOKEN", "x"), ("NOTION_TOKEN", "x"), ("NOTION_DATABASE_ID", "x"),
                      ("GEMINI_API_KEY", "x")):
        monkeypatch.setattr(config, name, val)
    monkeypatch.setattr(notion_api, "get_pages", lambda: [])
    monkeypatch.setattr(auto_post, "fetch_news", lambda *a, **k: [])
    monkeypatch.setattr(auto_post.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(auto_post, "_card_dir", lambda: str(tmp_path / "cards"))
    monkeypatch.setattr(auto_post, "_publish_card_files", lambda paths: [f"https://img/{i}.png" for i, _ in enumerate(paths)])
    monkeypatch.setenv("FORCE_WINDOW", "force")
    monkeypatch.delenv("DRY_RUN", raising=False)
    sent = {"text": [], "carousel": []}

    def fake_text(text, reply_to_id=None, wait_seconds=None, topic_tag=None):
        sent["text"].append((text, topic_tag))
        return "post-1"

    def fake_carousel(urls, text=None, topic_tag=None):
        sent["carousel"].append((urls, text, topic_tag))
        return "post-car"

    monkeypatch.setattr(threads_api, "publish_text", fake_text)
    monkeypatch.setattr(threads_api, "publish_carousel", fake_carousel)
    return sent


def test_evening_term_post_is_published_with_tag_and_counted(run_env, monkeypatch):
    monkeypatch.setattr(auto_post, "_call_gemini", fake_gemini_factory([GOOD_TERM]))
    monkeypatch.setenv("POST_TYPE", "term")
    auto_post.main()
    text, tag = run_env["text"][0]
    assert tag == "AI 용어" and text.startswith("📖 AI 한 줄 용어 #1")
    state = auto_post.load_posted()
    assert state["corner_seq"] == {"term": 1}
    assert state["cycle_index"] == 0                       # 아침 순환 큐는 건드리지 않는다
    assert any(k.endswith("-evening") for k in state["posted"])


def test_evening_card_prompt_publishes_four_slide_carousel(run_env, monkeypatch):
    monkeypatch.setattr(auto_post, "_call_gemini", fake_gemini_factory([CARD_OK]))
    monkeypatch.setenv("POST_TYPE", "card_prompt")
    auto_post.main()
    urls, text, tag = run_env["carousel"][0]
    assert len(urls) == 4 and tag == "AI 프롬프트" and "[여기에 회의 메모 붙여넣기]" in text
    assert auto_post.load_posted()["corner_seq"] == {"card_prompt": 1}


def test_carousel_failure_still_posts_the_text(run_env, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("캐러셀 실패")

    monkeypatch.setattr(threads_api, "publish_carousel", boom)
    monkeypatch.setattr(auto_post, "_call_gemini", fake_gemini_factory([CARD_OK]))
    monkeypatch.setenv("POST_TYPE", "card_prompt")
    auto_post.main()
    assert run_env["text"] and "[여기에 회의 메모 붙여넣기]" in run_env["text"][0][0]


OPEN_OK = "[후킹]\n회의 메모가 엉망이신가요\n[프롬프트]\n아래를 정리해줘 [여기에 붙여넣기] 없는 내용은 지어내지 마\n[예시]\n이렇게 정리돼요"


def test_failed_evening_corner_falls_back_to_proven_open_post(run_env, monkeypatch):
    def gemini(prompt, max_tokens=420):
        return "연구에 따르면 30% 줄어요" if "AI 한 줄 용어" in prompt else OPEN_OK

    monkeypatch.setattr(auto_post, "_call_gemini", gemini)
    monkeypatch.setenv("POST_TYPE", "term")
    auto_post.main()
    text, tag = run_env["text"][0]
    assert tag == "AI 프롬프트" and "연구에 따르면" not in text
    state = auto_post.load_posted()
    assert state["corner_seq"] == {} and any(k.endswith("-evening") for k in state["posted"])


def test_dry_run_publishes_nothing_and_records_nothing(run_env, monkeypatch):
    previews = []
    monkeypatch.setattr(auto_post, "_write_preview", lambda *a: previews.append(a))
    monkeypatch.setattr(auto_post, "_call_gemini", fake_gemini_factory([GOOD_TERM]))
    monkeypatch.setenv("DRY_RUN", "yes")
    monkeypatch.setenv("POST_TYPE", "term")
    auto_post.main()
    assert run_env["text"] == [] and run_env["carousel"] == []
    assert previews and previews[0][0] == "term"
    assert auto_post.load_posted()["posted"] == {}
    assert not os.path.exists(auto_post.POSTED_LOG_FILE)


def test_morning_posts_get_topic_tags(run_env, monkeypatch):
    monkeypatch.setattr(auto_post, "_call_gemini", fake_gemini_factory([OPEN_OK]))
    monkeypatch.setenv("POST_TYPE", "open")
    auto_post.main()
    assert run_env["text"][0][1] == "AI 프롬프트"


def test_comment_bot_never_sends_notion_replies_on_evening_posts():
    import comment_bot
    for kind in corners.EVENING_KINDS + ("briefing", "benefit"):
        assert kind in comment_bot.NO_NOTION_REPLY_POST_TYPES
    assert "open" not in comment_bot.NO_NOTION_REPLY_POST_TYPES and "howto" not in comment_bot.NO_NOTION_REPLY_POST_TYPES
