# -*- coding: utf-8 -*-
"""아침 발행 OPEN/LEAD 큐 연결(auto_post.write_queued_prompt_post, determine_prompt_type) 테스트."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import auto_post
import cta
import config
import notion_api
import threads_api


class FakeResponse:
    def __init__(self, json_data):
        self._json = json_data
        self.status_code = 200
        self.ok = True
        self.text = json.dumps(json_data, ensure_ascii=False)

    def json(self):
        return self._json


def _mock_gemini_post(url, json=None, headers=None, params=None, timeout=None, **kwargs):
    prompt_text = json["contents"][0]["parts"][0]["text"]
    if "[맛보기]" in prompt_text:
        body = "[후킹]\n요즘 이런 고민 많으시죠\n[이유]\n이래서 필요해요\n[맛보기]\n이런 내용이 담겨있어요"
    else:
        body = (
            "[후킹]\n요즘 이런 고민 많으시죠\n"
            "[프롬프트]\n아래는 [여기에 내용]을 정리해줘\n출력형식: 표로\n없는 내용은 지어내지 마\n"
            "[예시]\n이렇게 정리돼서 나와요"
        )
    return FakeResponse({"candidates": [{"content": {"parts": [{"text": body}]}}]})


@pytest.fixture(autouse=True)
def _isolate_cta_state(tmp_path, monkeypatch):
    monkeypatch.setattr(cta, "LAST_CTA_FILE", str(tmp_path / "last_cta.json"))


# ── determine_prompt_type ───────────────────────────────────────

def test_post_type_env_overrides_weekday(monkeypatch):
    monkeypatch.setenv("POST_TYPE", "lead")
    monday = auto_post.datetime.datetime(2026, 8, 10, 7, 0, tzinfo=auto_post.KST)  # 월요일(OPEN 요일)이지만
    assert auto_post.determine_prompt_type(monday) == "lead"


def test_weekday_map_used_when_no_override(monkeypatch):
    monkeypatch.delenv("POST_TYPE", raising=False)
    wednesday = auto_post.datetime.datetime(2026, 8, 12, 7, 0, tzinfo=auto_post.KST)  # 수요일 → LEAD
    monday = auto_post.datetime.datetime(2026, 8, 10, 7, 0, tzinfo=auto_post.KST)  # 월요일 → OPEN
    assert auto_post.determine_prompt_type(wednesday) == "lead"
    assert auto_post.determine_prompt_type(monday) == "open"


# ── write_queued_prompt_post ─────────────────────────────────────

def test_lead_type_picks_from_registered_keyword(monkeypatch):
    monkeypatch.setattr(notion_api, "get_lead_ready_keywords", lambda: {"회의", "상세", "식단"})
    monkeypatch.setattr(auto_post.requests, "post", _mock_gemini_post)

    state = {}
    result = auto_post.write_queued_prompt_post(state, auto_post.datetime.datetime.now(auto_post.KST), forced_type="lead")

    assert result["post_type"] == "lead"
    assert result["keyword"] in {"회의", "상세", "식단"}
    assert result["keyword"] in result["text"]  # LEAD CTA에 {keyword}가 치환되어 본문에 들어감
    assert state["published_keywords"] == [result["keyword"]]
    assert state["lead_seq"] == 1


def test_lead_falls_back_to_open_when_lead_queue_empty(monkeypatch, capsys):
    monkeypatch.setattr(notion_api, "get_lead_ready_keywords", lambda: set())  # 노션에 등록된 자료 없음
    monkeypatch.setattr(auto_post.requests, "post", _mock_gemini_post)

    state = {}
    result = auto_post.write_queued_prompt_post(state, auto_post.datetime.datetime.now(auto_post.KST), forced_type="lead")

    assert result["post_type"] == "open"
    captured = capsys.readouterr()
    assert "LEAD 큐가 비어 OPEN으로 대체 발행" in captured.out


def test_open_type_excludes_lead_keywords(monkeypatch):
    monkeypatch.setattr(notion_api, "get_lead_ready_keywords", lambda: {"회의"})
    monkeypatch.setattr(auto_post.requests, "post", _mock_gemini_post)

    state = {}
    result = auto_post.write_queued_prompt_post(state, auto_post.datetime.datetime.now(auto_post.KST), forced_type="open")

    assert result["post_type"] == "open"
    assert result["keyword"] != "회의"  # 회의는 LEAD 전용이므로 OPEN에는 안 나와야 함


def test_already_published_keyword_not_picked_again(monkeypatch):
    monkeypatch.setattr(notion_api, "get_lead_ready_keywords", lambda: set())
    monkeypatch.setattr(auto_post.requests, "post", _mock_gemini_post)

    # office 영역의 첫 글감("회의록 정리하기"/"회의")이 이미 발행된 것으로 마이그레이션됨
    state = {"prompt_seq": 1}
    result = auto_post.write_queued_prompt_post(state, auto_post.datetime.datetime.now(auto_post.KST), forced_type="open")

    assert result["keyword"] != "회의"
    assert "회의" in state["published_keywords"]  # 마이그레이션으로 이미 들어가 있어야 함


def test_migration_runs_once_and_prompt_seq_untouched(monkeypatch):
    monkeypatch.setattr(notion_api, "get_lead_ready_keywords", lambda: set())
    monkeypatch.setattr(auto_post.requests, "post", _mock_gemini_post)

    state = {"prompt_seq": 3}
    auto_post.write_queued_prompt_post(state, auto_post.datetime.datetime.now(auto_post.KST), forced_type="open")

    assert state["prompt_seq"] == 3  # 롤백 대비로 그대로 보존
    assert state["published_keywords_migrated"] is True


# ── main() 통합: POST_TYPE 강제 지정 시 혜택/활용법 판단을 건너뛴다 ──

def test_main_post_type_open_skips_benefit_check(tmp_path, monkeypatch):
    posted_file = tmp_path / "posted.json"
    monkeypatch.setattr(auto_post, "POSTED_FILE", str(posted_file))
    monkeypatch.setattr(config, "THREADS_ACCESS_TOKEN", "fake-token")
    monkeypatch.setattr(config, "NOTION_TOKEN", "fake-token")
    monkeypatch.setattr(config, "NOTION_DATABASE_ID", "fake-db")
    monkeypatch.setattr(config, "GEMINI_API_KEY", "fake-key")
    monkeypatch.setattr(notion_api, "get_lead_ready_keywords", lambda: set())
    monkeypatch.setattr(auto_post.requests, "post", _mock_gemini_post)
    monkeypatch.setattr(threads_api, "publish_text", lambda text, reply_to_id=None, wait_seconds=None: "fake-post-id")

    benefit_calls = {"n": 0}

    def fail_if_called(*a, **k):
        benefit_calls["n"] += 1
        return []

    monkeypatch.setattr(auto_post, "fetch_news", fail_if_called)
    monkeypatch.setenv("POST_TYPE", "open")
    monkeypatch.setenv("FORCE_WINDOW", "test")

    auto_post.main()

    assert benefit_calls["n"] == 0  # 혜택 소재 검색(뉴스 조회)이 아예 호출되지 않아야 함
    state = auto_post.load_posted()
    posted_entry = list(state["posted"].values())[0]
    assert posted_entry["type"] == "open"
