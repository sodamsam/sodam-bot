# -*- coding: utf-8 -*-
"""AI 티 지우기(글 버릇 제거) 테스트."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import auto_post as ap


def test_persona_has_ai_tell_rules_and_keeps_original():
    assert "AI 티 지우기" in ap.PERSONA
    assert "따뜻하고, 담백하고, 과하지 않게" in ap.PERSONA  # 기존 페르소나 유지


def test_em_dash_becomes_comma():
    assert ap.strip_ai_tells("엑셀 — 어렵지 않아요") == "엑셀, 어렵지 않아요"
    assert "—" not in ap.strip_ai_tells("가—나–다")


def test_number_range_dash_becomes_tilde():
    assert ap.strip_ai_tells("10—20분") == "10~20분"


def test_bold_marks_removed():
    assert ap.strip_ai_tells("**중요**한 팁") == "중요한 팁"


def test_plain_text_unchanged():
    t = "오늘은 메일 정리를 해봐요.\n\n5분이면 돼요."
    assert ap.strip_ai_tells(t) == t


def test_finalize_applies_filter():
    out = ap._finalize("좋은 방법 — 바로 해보세요", max_blocks=4)
    assert "—" not in out
