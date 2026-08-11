# -*- coding: utf-8 -*-
"""docs/글감_발행목록.md 자동 생성 테스트."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import auto_post
import notion_api
import prompt_queue

TEST_BANK = {
    "office": [("회의록 정리", "회의"), ("메일 답장", "메일")],
    "biz": [("상세페이지", "상세"), ("리뷰 답글", "리뷰")],
    "life": [("식단 짜기", "식단"), ("장보기", "장보기")],
}
TEST_AREAS = ["office", "biz", "life"]


def test_render_topic_doc_sections_and_status():
    lead_keywords = {"회의"}          # 등록 + 게시됨
    registered_keywords = {"회의", "상세"}  # 상세는 등록만 되고 미게시
    md = prompt_queue.render_topic_doc(
        TEST_BANK, TEST_AREAS, lead_keywords, registered_keywords,
        published_keywords=set(), generated_at="2026-08-12 07:00 KST",
    )

    assert "# 글감 발행 목록" in md
    assert "## LEAD 큐" in md
    assert "## OPEN 큐" in md
    assert "## 발행 완료" in md
    assert "✅ 게시됨 (LEAD 가능)" in md   # 회의
    assert "🔶 등록만 됨 (미게시)" in md   # 상세
    assert "❌ 미등록" in md               # 나머지


def test_render_topic_doc_published_log_sorted_desc():
    md = prompt_queue.render_topic_doc(
        TEST_BANK, TEST_AREAS, lead_keywords=set(), registered_keywords=set(),
        published_keywords=set(), generated_at="now",
        published_log=[
            {"date_kst": "2026-08-10T07:00:00+09:00", "post_type": "open", "topic": "메일 답장", "keyword": "메일"},
            {"date_kst": "2026-08-12T07:00:00+09:00", "post_type": "lead", "topic": "식단 짜기", "keyword": "식단"},
        ],
    )
    idx_12 = md.index("2026-08-12")
    idx_10 = md.index("2026-08-10")
    assert idx_12 < idx_10  # 최신이 먼저 나와야 함


def test_render_topic_doc_no_log_shows_placeholder():
    md = prompt_queue.render_topic_doc(
        TEST_BANK, TEST_AREAS, lead_keywords=set(), registered_keywords=set(),
        published_keywords=set(), generated_at="now",
    )
    assert "아직 발행 기록이 없습니다" in md


def test_update_topic_doc_writes_file(tmp_path, monkeypatch):
    monkeypatch.setattr(auto_post, "TOPIC_DOC_FILE", str(tmp_path / "글감_발행목록.md"))
    monkeypatch.setattr(auto_post, "POSTED_LOG_FILE", str(tmp_path / "posted_log.csv"))  # 로그 없음 → 빈 상태
    monkeypatch.setattr(notion_api, "get_pages", lambda: [
        {"title": "회의 자료", "url": "u", "public_url": "https://x.notion.site/y", "keywords": ["회의"]},
    ])

    state = {"published_keywords": ["회의"]}
    auto_post.update_topic_doc(state, auto_post.datetime.datetime.now(auto_post.KST))

    content = (tmp_path / "글감_발행목록.md").read_text(encoding="utf-8")
    assert "게시됨" in content
    assert "회의" in content


def test_update_topic_doc_skips_on_notion_failure(tmp_path, monkeypatch, capsys):
    doc_file = tmp_path / "글감_발행목록.md"
    monkeypatch.setattr(auto_post, "TOPIC_DOC_FILE", str(doc_file))

    def boom():
        raise RuntimeError("network down")

    monkeypatch.setattr(notion_api, "get_pages", boom)

    auto_post.update_topic_doc({}, auto_post.datetime.datetime.now(auto_post.KST))

    assert not doc_file.exists()
    assert "문서 갱신 건너뜀" in capsys.readouterr().out
