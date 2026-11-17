# -*- coding: utf-8 -*-
"""네이버 블로그 글 준비(naver_post.py) — 검색어 선정, 형식·근거 검사, HTML 조립."""
import naver_post


def good_draft():
    return {
        "title": "챗GPT 사용법 엑셀 수식 쉽게 만드는 법",
        "intro": "엑셀 수식이 어려워서 미뤄두셨죠. " * 6,
        "sections": [
            {"heading": f"소제목 {n}", "paragraphs": ["차근차근 따라 하시면 됩니다. " * 12, "두 번째 문단입니다. " * 8],
             "steps": ["1. 챗GPT를 엽니다", "입력칸을 누릅니다"]}
            for n in (1, 2, 3)
        ],
        "prompt": "아래 표에서 [원하는 계산]을 하는 엑셀 수식을 알려줘.\n수식을 한 줄씩 쉬운 말로 풀이해줘.",
        "prompt_situation": "수식을 어떻게 써야 할지 모를 때 씁니다.",
        "example": "=SUMIF(A:A, \"사과\", B:B)",
        "tip": "틀리면 오류 글자를 그대로 붙여넣어 다시 물어보세요.",
        "closing": "오늘은 수식 하나만 물어보세요. 그것으로 충분합니다.",
        "tags": ["챗GPT", "엑셀", "#AI 활용"],
        "card_points": [{"title": "표를 붙여넣기", "desc": "있는 그대로 복사"}] * 3,
    }


def test_keyword_score_filters_money_and_short_words():
    assert naver_post.keyword_score("챗GPT 요금제 비교") == 0
    assert naver_post.keyword_score("챗GPT") == 0
    assert naver_post.keyword_score("챗GPT 사용법 엑셀") > naver_post.keyword_score("챗GPT 지브리 스타일")


def test_pick_keyword_skips_used_and_prefers_practical():
    fake = lambda seed: ["챗GPT 주가 전망", "챗GPT 사용법 엑셀", "챗GPT 보고서 요약 방법"]
    assert naver_post.pick_keyword([], suggest=fake) == "챗GPT 보고서 요약 방법"
    assert naver_post.pick_keyword(["챗GPT 보고서 요약 방법"], suggest=fake) == "챗GPT 사용법 엑셀"
    assert naver_post.pick_keyword(["챗GPT보고서요약방법", "챗GPT 사용법 엑셀"], suggest=fake) is None


def test_good_draft_passes_and_claims_are_caught():
    d = good_draft()
    assert naver_post.check_shape(d, "howto") == []
    assert naver_post.find_claims(naver_post.draft_text(d)) == []
    d["tip"] = "한 달에 29,000원이면 됩니다. 선착순입니다."
    assert naver_post.find_claims(naver_post.draft_text(d)) == ["29,000원", "선착순"]
    assert naver_post.find_claims("3월 5일까지 신청", "접수는 3월 5일까지") == []


def test_write_post_retries_then_fails_closed():
    calls = []

    def bad(prompt):
        calls.append(prompt)
        d = good_draft()
        d["tip"] = "50% 할인됩니다."
        return d

    assert naver_post.write_post("howto", keyword="챗GPT 사용법 엑셀", llm=bad) is None
    assert len(calls) == 2 and "다시 쓰기" in calls[1]
    assert naver_post.write_post("howto", keyword="챗GPT 사용법 엑셀", llm=lambda p: good_draft())["title"]


def test_html_escapes_and_places_images():
    d = good_draft()
    d["intro"] = "<script>alert(1)</script> 안녕하세요"
    out = naver_post.build_html(d, "howto", ["https://x/1.jpg", "https://x/2.jpg", "https://x/3.jpg", "https://x/4.jpg"])
    assert "<script>" not in out and "&lt;script&gt;" in out
    assert out.count("<img ") == 4 and out.count("<h3>") == 6
    assert "1. 챗GPT를 엽니다<br>2. 입력칸을 누릅니다" in out
    assert "<blockquote>" in out
    news = naver_post.build_html(d, "news", ["https://x/1.jpg"])
    assert news.count("<img ") == 1 and "<blockquote>" not in news
    assert "■ 소제목 1" in naver_post.build_text(d, "howto")


def test_cta_is_added_when_saved(monkeypatch):
    d = good_draft()
    monkeypatch.setattr(naver_post, "load_cta", lambda: {"text": "강의 문의는 여기로", "url": "https://example.com"})
    assert "강의 문의는 여기로<br>https://example.com" in naver_post.build_html(d, "howto", [])
    assert naver_post.build_text(d, "howto").endswith("강의 문의는 여기로\nhttps://example.com")
    monkeypatch.setattr(naver_post, "load_cta", lambda: {})
    assert "강의 문의" not in naver_post.build_html(d, "howto", [])
