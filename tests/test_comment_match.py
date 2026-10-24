# -*- coding: utf-8 -*-
"""'신청' 댓글 매칭·허브 응답 회귀 테스트 (2026-10 점검에서 발견한 문제)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import comment_bot
import config
import notion_api
import threads_api

HUB = {"title": "AI 프롬프트 5종 모음", "url": "u", "public_url": "https://x.notion.site/hub", "keywords": []}
GRANT = {"title": "지원사업 신청서 초안 쓰기", "url": "u", "public_url": "https://x.notion.site/g", "keywords": ["신청"]}
BIZ = {"title": "사업계획서 개요 잡기", "url": "u", "public_url": "https://x.notion.site/b", "keywords": ["사업"]}
PAGES = [GRANT, BIZ, HUB]


def test_apply_comment_goes_to_hub_even_if_a_page_uses_apply_as_keyword():
    page = notion_api.match_page("신청합니다", PAGES)
    assert page is HUB


def test_comment_on_the_grant_post_still_gets_the_grant_page():
    page = notion_api.match_page("신청", PAGES, post_keyword="신청")
    assert page is GRANT


def test_specific_keyword_still_wins_over_hub():
    page = notion_api.match_page("사업 자료도 신청할게요", PAGES)
    assert page is BIZ


def test_hub_title_match_ignores_spacing_and_case():
    hub = dict(HUB, title="  AI  프롬프트 5종   모음 ")
    assert notion_api.find_hub([hub]) is hub


def test_missing_hub_returns_none():
    assert notion_api.match_page("신청", [GRANT, BIZ]) is None


def test_get_pages_follows_pagination(monkeypatch):
    calls = []

    class R:
        ok = True
        def __init__(self, data): self._d = data
        def json(self): return self._d

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(json.get("start_cursor"))
        def page(t): return {"url": "u", "public_url": None, "properties": {"이름": {"type": "title", "title": [{"plain_text": t}]}}}
        if json.get("start_cursor") is None:
            return R({"results": [page("a")], "has_more": True, "next_cursor": "c2"})
        return R({"results": [page("b")], "has_more": False})

    monkeypatch.setattr(notion_api.requests, "post", fake_post)
    monkeypatch.setattr(config, "NOTION_TOKEN", "t")
    monkeypatch.setattr(config, "NOTION_DATABASE_ID", "d")
    pages = notion_api.get_pages()
    assert [p["title"] for p in pages] == ["a", "b"]
    assert calls == [None, "c2"]


def test_main_uses_post_keyword_from_log(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STATE_FILE", str(tmp_path / "state" / "replied.json"))
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "posted_log.csv").write_text(
        "date_kst,post_type,topic,keyword,cta_id,queue_left,thread_id,is_draft\n"
        "2026-10-23T07:00:00+09:00,open,지원사업 신청서 초안 쓰기,신청,3,41,post-grant,False\n",
        encoding="utf-8")
    monkeypatch.setattr(threads_api, "get_me", lambda: {"id": "1", "username": "sodam_ai_lab"})
    monkeypatch.setattr(notion_api, "get_pages", lambda: PAGES)
    monkeypatch.setattr(threads_api, "get_recent_posts", lambda limit=5: [{"id": "post-grant"}, {"id": "post-other"}])
    monkeypatch.setattr(threads_api, "get_replies", lambda pid: [{"id": "c-" + pid, "username": "u-" + pid, "text": "신청"}])
    for name in ("THREADS_ACCESS_TOKEN", "NOTION_TOKEN", "NOTION_DATABASE_ID"):
        monkeypatch.setattr(config, name, "x")
    monkeypatch.setattr(comment_bot.time, "sleep", lambda *a, **k: None)
    sent = {}
    monkeypatch.setattr(threads_api, "publish_text",
                        lambda text, reply_to_id=None, wait_seconds=None: sent.setdefault(reply_to_id, text))
    comment_bot.main()
    assert "x.notion.site/g" in sent["c-post-grant"]    # 신청서 글에 남긴 '신청' → 신청서 자료
    assert "x.notion.site/hub" in sent["c-post-other"]  # 다른 글에 남긴 '신청' → 허브
