# -*- coding: utf-8 -*-
"""comment_bot.py 첫 요청자 안내 로직 테스트."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import comment_bot
import config
import notion_api
import threads_api

PAGE = {"title": "회의록 정리 프롬프트", "url": "https://notion.so/x", "public_url": "https://x.notion.site/y", "keywords": ["회의"]}


# ── build_reply_text ─────────────────────────────────────────

def test_repeat_requester_gets_plain_reply():
    text = comment_bot.build_reply_text(PAGE, is_first_time=False)
    assert text == config.REPLY_TEMPLATE.format(title=PAGE["title"], link=PAGE["public_url"])


def test_first_time_requester_gets_extra_line():
    text = comment_bot.build_reply_text(PAGE, is_first_time=True)
    assert any(line in text for line in comment_bot.FIRST_TIME_NOTE_LINES)


def test_no_direct_follow_request_phrase_anywhere():
    """"팔로우해주세요"류 직접 요청 문구는 어떤 조합에서도 나오면 안 된다."""
    for is_first in (True, False):
        text = comment_bot.build_reply_text(PAGE, is_first_time=is_first)
        assert "팔로우해주세요" not in text
        assert "팔로우해두세요" not in text


# ── main() 통합: 첫 요청자 vs 재요청자 ────────────────────────

def _patch_common(monkeypatch, state_file, posts, comments_by_post):
    monkeypatch.setattr(config, "STATE_FILE", str(state_file))
    monkeypatch.setattr(threads_api, "get_me", lambda: {"id": "1", "username": "sodam_ai_lab"})
    monkeypatch.setattr(notion_api, "get_pages", lambda: [PAGE])
    monkeypatch.setattr(threads_api, "get_recent_posts", lambda limit=5: posts)
    monkeypatch.setattr(threads_api, "get_replies", lambda post_id: comments_by_post[post_id])
    monkeypatch.setattr(config, "THREADS_ACCESS_TOKEN", "fake")
    monkeypatch.setattr(config, "NOTION_TOKEN", "fake")
    monkeypatch.setattr(config, "NOTION_DATABASE_ID", "fake")
    monkeypatch.setattr(comment_bot.time, "sleep", lambda *a, **k: None)


def test_first_requester_reply_includes_note_repeat_does_not(tmp_path, monkeypatch):
    posts = [{"id": "post1"}]
    comments = {
        "post1": [
            {"id": "c1", "username": "alice", "text": "회의 자료 주세요"},
            {"id": "c2", "username": "alice", "text": "회의 자료 다시 주세요"},
        ]
    }
    _patch_common(monkeypatch, tmp_path / "replied.json", posts, comments)

    sent = {}

    def fake_publish(text, reply_to_id=None, wait_seconds=None):
        sent[reply_to_id] = text
        return "fake-id"

    monkeypatch.setattr(threads_api, "publish_text", fake_publish)

    comment_bot.main()

    assert any(line in sent["c1"] for line in comment_bot.FIRST_TIME_NOTE_LINES)
    assert not any(line in sent["c2"] for line in comment_bot.FIRST_TIME_NOTE_LINES)

    state = comment_bot.load_state()
    assert "alice" in state["replied_usernames"]


def test_second_run_same_user_is_not_first_time_again(tmp_path, monkeypatch):
    state_file = tmp_path / "replied.json"
    posts = [{"id": "post1"}]

    # 1회차: alice가 처음 요청
    _patch_common(monkeypatch, state_file, posts, {"post1": [{"id": "c1", "username": "alice", "text": "회의 부탁"}]})
    monkeypatch.setattr(threads_api, "publish_text", lambda text, reply_to_id=None, wait_seconds=None: "id1")
    comment_bot.main()

    # 2회차: alice가 다른 댓글로 또 요청 → 이번엔 첫 요청자가 아니어야 함
    _patch_common(monkeypatch, state_file, posts, {"post1": [{"id": "c2", "username": "alice", "text": "회의 자료 또 부탁"}]})
    sent = {}
    monkeypatch.setattr(threads_api, "publish_text", lambda text, reply_to_id=None, wait_seconds=None: sent.setdefault(reply_to_id, text) or "id2")
    comment_bot.main()

    assert not any(line in sent["c2"] for line in comment_bot.FIRST_TIME_NOTE_LINES)
