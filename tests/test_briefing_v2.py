# -*- coding: utf-8 -*-
"""브리핑 소재 선정·카드 생성 테스트."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import auto_post
import card_news


def item(title, summary=""):
    return {"title": title, "summary": summary, "published_ts": 1.0}


def test_gambling_article_is_rejected():
    # 2026-10-21에 실제로 뽑혔던 종류의 기사
    assert auto_post._briefing_relevance_score(item("게임 정보가 바뀌었는지 확인하는 도박 환금성 변경 기록")) == 0


def test_non_ai_article_is_rejected():
    assert auto_post._briefing_relevance_score(item("정부, 소상공인 지원 확대 발표")) == 0


def test_ai_launch_article_scores():
    assert auto_post._briefing_relevance_score(item("Claude, 구글 문서·시트 사이드바 공개 베타 출시")) >= 2


def test_vague_review_stage_article_scores_lower():
    firm = auto_post._briefing_relevance_score(item("AI 바우처 신청 접수 시작"))
    vague = auto_post._briefing_relevance_score(item("AI 바우처 신청 확대 검토"))
    assert vague < firm


def test_parse_point():
    assert auto_post._parse_point("문서 옆 창 | 화면을 떠나지 않고 부탁해요") == {"title": "문서 옆 창", "desc": "화면을 떠나지 않고 부탁해요"}
    assert auto_post._parse_point("세로선 없음") is None


def test_card_renders_at_4_by_5(tmp_path):
    out = tmp_path / "c.png"
    card_news.generate_news_card({
        "category": "신청 가능", "date": "10월 24일",
        "headline": "아주 긴 제목이 들어와도 세 줄 안에 맞춰서 들어가야 합니다 정말로요",
        "points": [{"title": "가" * 30, "desc": "나" * 60}] * 3,
        "action_label": "신청 방법", "action": "공식 공고문에서 접수 기간과 대상을 꼭 확인하세요" * 2,
        "source": "테스트"}, str(out))
    from PIL import Image
    assert Image.open(out).size == (1080, 1350)
