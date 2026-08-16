# -*- coding: utf-8 -*-
"""v2.1/v2.2 패치 테스트: 인스타·신청 유도 문구, 마무리 문구 선택, 발행 흐름."""
import json
import sys
import os
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import auto_post
import notion_api
import threads_api
import config


# ── pick_closing_line 단위 테스트 ───────────────────────────────

def test_two_way_split_when_material_exists():
    """cta_seq 0~7 범위, 자료 존재=True → 신청/일반이 정확히 4:4로 순환한다."""
    counts = {"신청": 0, "일반": 0}
    for cta_seq in range(8):
        line = auto_post.pick_closing_line("howto", False, None, cta_seq, has_material=True)
        if line in auto_post.APPLY_CTA_LINES:
            counts["신청"] += 1
            assert cta_seq % 2 == 0
        elif line in auto_post.GENERIC_FOLLOW_LINES:
            counts["일반"] += 1
            assert cta_seq % 2 == 1
        else:
            pytest.fail(f"알 수 없는 문구: {line}")
    assert counts == {"신청": 4, "일반": 4}


def test_each_pool_rotates_through_all_its_lines():
    """각 문구 풀(신청/일반) 내부에서도 한 문장만 반복되지 않고 3개가 골고루 순환한다."""
    seen = {"신청": set(), "일반": set()}
    for cta_seq in range(6):
        line = auto_post.pick_closing_line("howto", False, None, cta_seq, has_material=True)
        if line in auto_post.APPLY_CTA_LINES:
            seen["신청"].add(line)
        else:
            seen["일반"].add(line)
    assert len(seen["신청"]) == len(auto_post.APPLY_CTA_LINES)
    assert len(seen["일반"]) == len(auto_post.GENERIC_FOLLOW_LINES)


def test_apply_cta_falls_back_to_generic_when_no_material():
    """자료 DB가 비어있으면(has_material=False) 신청 유도 자리는 일반 문구로 대체되고,
    신청 유도 문구는 어떤 cta_seq에서도 절대 나오지 않는다."""
    for cta_seq in range(9):
        line = auto_post.pick_closing_line("howto", False, None, cta_seq, has_material=False)
        assert line not in auto_post.APPLY_CTA_LINES, (cta_seq, line)
        assert line in auto_post.GENERIC_FOLLOW_LINES, (cta_seq, line)


def test_keyword_funnel_always_wins_regardless_of_cta_seq():
    """has_keyword=True면 cta_seq·자료 존재 여부와 무관하게 항상 정확 매칭 댓글 퍼널."""
    for cta_seq in range(10):
        for has_material in (True, False):
            line = auto_post.pick_closing_line("prompt", True, "식단", cta_seq, has_material)
            assert line == '댓글에 "식단" 남겨주시면 5개 더 보내드릴게요'
            assert line not in auto_post.GENERIC_FOLLOW_LINES
            assert line not in auto_post.APPLY_CTA_LINES


def test_benefit_never_mixes_apply_line():
    """post_type='benefit'일 때 신청 유도 문구가 절대 섞이지 않는다."""
    for cta_seq in range(20):
        for has_material in (True, False):
            line = auto_post.pick_closing_line("benefit", False, None, cta_seq, has_material)
            assert line == auto_post.BENEFIT_FOLLOW_LINE
            assert line not in auto_post.APPLY_CTA_LINES


def test_has_material_cached_per_day(monkeypatch):
    """자료 존재 여부는 같은 날짜 안에서는 재조회하지 않고 state에 캐시된 값을 쓴다."""
    calls = {"n": 0}

    def fake_has_any_material():
        calls["n"] += 1
        return True

    monkeypatch.setattr(notion_api, "has_any_material", fake_has_any_material)

    state = {}
    assert auto_post._get_has_material(state, "2026-08-09") is True
    assert auto_post._get_has_material(state, "2026-08-09") is True
    assert calls["n"] == 1  # 같은 날짜 → 재조회 없음

    assert auto_post._get_has_material(state, "2026-08-10") is True
    assert calls["n"] == 2  # 날짜가 바뀌면 재조회


def test_cta_seq_persists_across_save_and_load(tmp_path, monkeypatch):
    """cta_seq가 저장/로드 왕복 후에도 보존된다."""
    posted_file = tmp_path / "posted.json"
    monkeypatch.setattr(auto_post, "POSTED_FILE", str(posted_file))

    state = auto_post.load_posted()
    assert state["cta_seq"] == 0  # 기본값

    state["cta_seq"] = 7
    auto_post.save_posted(state)

    reloaded = auto_post.load_posted()
    assert reloaded["cta_seq"] == 7


def test_generic_phrase_removed_from_source():
    """'이런 프롬프트 계속 올려요' 문자열이 실제 생성 코드 경로에 남아있지 않다(주석 제외)."""
    src = Path(auto_post.__file__).read_text(encoding="utf-8")
    for line in src.splitlines():
        if "이런 프롬프트 계속 올려요" in line:
            assert line.strip().startswith("#"), f"literal phrase still used in code: {line}"

    assert "이런 프롬프트 계속 올려요" not in auto_post.GENERIC_FOLLOW_LINES

    for post_type, has_kw in (("howto", False), ("prompt", False)):
        for cta_seq in range(20):
            for has_material in (True, False):
                line = auto_post.pick_closing_line(post_type, has_kw, "테스트", cta_seq, has_material)
                assert line != "이런 프롬프트 계속 올려요"


# ── mock 기반 시나리오 (3-1-C) ──────────────────────────────────

class FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json = json_data
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self.text = json.dumps(json_data, ensure_ascii=False)

    def json(self):
        return self._json


def _make_mock_post(keyword_registered):
    """requests.post를 노션/제미나이 URL에 따라 분기해서 응답하는 mock."""
    import re

    def mock_post(url, json=None, headers=None, params=None, timeout=None, **kwargs):
        if "api.notion.com" in url:
            if keyword_registered:
                page = {
                    "properties": {
                        "이름": {"type": "title", "title": [{"plain_text": "식단 자료"}]},
                        config.NOTION_KEYWORD_PROP: {
                            "type": "rich_text",
                            "rich_text": [{"plain_text": "식단"}],
                        },
                    },
                    "url": "https://notion.so/fake-page",
                }
                results = [page]
            else:
                results = []
            return FakeResponse({"results": results})

        if "generativelanguage.googleapis.com" in url:
            prompt_text = json["contents"][0]["parts"][0]["text"]
            # closing_line 자체에 큰따옴표가 들어갈 수 있어(예: 댓글 퍼널 문구) [^"]+ 대신
            # 줄 안에서 마지막 따옴표까지 그리디하게 잡는다.
            m = re.search(r'정확히 이 문장을 그대로 쓰세요: "(.+)"', prompt_text)
            closing = m.group(1) if m else "(마무리 문구를 찾지 못함)"
            body = (
                "[본문]\n"
                "요즘 이런 고민 많으시죠\n"
                "이렇게 해보세요\n"
                "[여기에 내용 붙여넣기]로 정리해줘\n"
                f"{closing}\n"
                "[댓글2]\n댓글2용 프롬프트 [여기에 내용]\n"
                "[댓글3]\n댓글3용 프롬프트 [여기에 내용]"
            )
            return FakeResponse({"candidates": [{"content": {"parts": [{"text": body}]}}]})

        raise AssertionError(f"예상치 못한 URL 호출: {url}")

    return mock_post


def _normalize(s):
    return "".join(s.split())


def test_scenario_keyword_registered(monkeypatch):
    """키워드 '식단'이 노션에 등록된 경우 → 댓글 퍼널 문구가 나온다."""
    monkeypatch.setattr(auto_post.requests, "post", _make_mock_post(keyword_registered=True))

    # AREAS=["office","biz","life"], seq=2 → life 영역 idx=0 → ("일주일 식단 짜기", "식단")
    state = {"prompt_seq": 2, "cta_seq": 0}
    text, extra_comments, has_kw = auto_post.write_prompt_post(state, auto_post.datetime.datetime.now(auto_post.KST))

    print(f"[시나리오1 결과] has_kw={has_kw}")
    print(f"[시나리오1 본문 마지막] {text.splitlines()[-1]}")

    assert has_kw is True
    expected = '댓글에 "식단" 남겨주시면 5개 더 보내드릴게요'
    assert _normalize(expected) in _normalize(text)


def test_scenario_keyword_not_registered(monkeypatch):
    """키워드가 노션에 등록 안 된 경우 → GENERIC/APPLY 문구 중 하나, 옛 문구는 없음.

    이 mock은 노션 DB를 완전히 빈 것으로 응답하므로(자료 0개), has_any_material()도
    False가 되어 신청 유도는 자동으로 배제되고 cta_seq=0 → 일반 문구로 대체된다.
    """
    monkeypatch.setattr(auto_post.requests, "post", _make_mock_post(keyword_registered=False))

    # seq=0 → office 영역 idx=0 → ("회의록 정리하기", "회의") — 노션엔 등록 안 된 것으로 mock
    state = {"prompt_seq": 0, "cta_seq": 0}
    text, extra_comments, has_kw = auto_post.write_prompt_post(state, auto_post.datetime.datetime.now(auto_post.KST))

    print(f"[시나리오2 결과] has_kw={has_kw}")
    print(f"[시나리오2 본문 마지막] {text.splitlines()[-1]}")

    assert has_kw is False
    normalized_text = _normalize(text)
    # 자료 DB가 비어있는 mock이므로 신청 유도는 절대 나오면 안 된다.
    assert not any(_normalize(c) in normalized_text for c in auto_post.APPLY_CTA_LINES)
    candidates = auto_post.GENERIC_FOLLOW_LINES
    assert any(_normalize(c) in normalized_text for c in candidates)
    assert "이런프롬프트계속올려요" not in normalized_text


# ── main() 통합 테스트: cta_seq는 발행 성공 시에만 증가 ─────────

def _patch_common(monkeypatch, tmp_path, posted_data=None):
    posted_file = tmp_path / "posted.json"
    if posted_data is not None:
        posted_file.write_text(json.dumps(posted_data, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(auto_post, "POSTED_FILE", str(posted_file))

    monkeypatch.setattr(config, "THREADS_ACCESS_TOKEN", "fake-token")
    monkeypatch.setattr(config, "NOTION_TOKEN", "fake-token")
    monkeypatch.setattr(config, "NOTION_DATABASE_ID", "fake-db")
    monkeypatch.setattr(config, "GEMINI_API_KEY", "fake-key")

    monkeypatch.setattr(auto_post, "fetch_news", lambda *a, **k: [])  # 혜택 소재 없음 → 큐로 진행
    monkeypatch.setattr(auto_post.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(auto_post.requests, "post", _make_mock_post(keyword_registered=False))

    return posted_file


def test_main_increments_cta_seq_on_successful_publish(tmp_path, monkeypatch):
    _patch_common(monkeypatch, tmp_path)
    monkeypatch.setattr(threads_api, "publish_text", lambda text, reply_to_id=None, wait_seconds=None: "fake-post-id")
    monkeypatch.setenv("FORCE_WINDOW", "test")

    auto_post.main()

    state = auto_post.load_posted()
    assert state["cta_seq"] == 1


def test_main_does_not_increment_cta_seq_on_publish_failure(tmp_path, monkeypatch):
    _patch_common(monkeypatch, tmp_path)

    def failing_publish(text, reply_to_id=None, wait_seconds=None):
        raise RuntimeError("Threads 서버 오류(mock)")

    monkeypatch.setattr(threads_api, "publish_text", failing_publish)
    monkeypatch.setenv("FORCE_WINDOW", "test")

    with pytest.raises(RuntimeError):
        auto_post.main()

    state = auto_post.load_posted()
    assert state["cta_seq"] == 0
