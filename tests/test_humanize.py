# -*- coding: utf-8 -*-
"""AI 티 지우기(humanize.py) — 스킬의 10가지 규칙 중 자동으로 고치거나 찾을 수 있는 것."""
import humanize


def test_clean_removes_dash_bold_greeting_and_openers():
    t = "안녕하세요 여러분! 또한, **회의록**을 정리합니다 — 정말 쉽거든요."
    assert humanize.clean(t) == "회의록을 정리합니다, 정말 쉽거든요."


def test_clean_keeps_meaning_numbers_and_line_breaks():
    t = "저도 마흔 넘어 처음 배웠어요.\n\n3분이면 끝나요."
    assert humanize.clean(t) == t


def test_find_tells_names_rules():
    tells = humanize.find_tells("전문가들은 이것이 혁신적이라고 말합니다. 이는 AI의 중요성을 알 수 있습니다. 정리하면 쉽습니다.")
    assert {"10 모호한 출처", "8 광고성 단어", "9 의미 키우는 꼬리 문장", "5 끝에서 요약"} <= set(tells)
    assert "2 부정 후 뒤집기" in humanize.find_tells("어려운 기술이 아니라 습관입니다.")
    assert humanize.find_tells("회의록은 녹음부터 해요. 그다음 붙여넣으면 되죠. 3분이면 끝나요.") == []


def test_rules_prompt_has_all_ten_rules():
    rules = humanize.rules_prompt()
    for n in range(1, 11):
        assert f"{n}." in rules


def test_number_range_matches_auto_post_rule():
    assert humanize.clean("10—20분") == "10~20분"
