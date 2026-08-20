# -*- coding: utf-8 -*-
"""data/posted_log.csv 발행 로그 적재 테스트."""
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import auto_post
import config
import cta
import notion_api
import threads_api

from tests.test_open_lead import _mock_gemini_post  # 기존 mock 재사용


def _read_rows(path):
    with open(path, "r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def test_append_creates_file_with_header(tmp_path, monkeypatch):
    log_file = tmp_path / "posted_log.csv"
    monkeypatch.setattr(auto_post, "POSTED_LOG_FILE", str(log_file))

    auto_post.append_posted_log({
        "date_kst": "2026-08-12T07:00:00+09:00", "post_type": "open", "topic": "회의록 정리",
        "keyword": "회의", "cta_id": 2, "queue_left": 5, "thread_id": "123", "is_draft": False,
    })

    rows = _read_rows(log_file)
    assert len(rows) == 1
    assert rows[0]["post_type"] == "open"
    assert rows[0]["keyword"] == "회의"


def test_append_twice_only_one_header(tmp_path, monkeypatch):
    log_file = tmp_path / "posted_log.csv"
    monkeypatch.setattr(auto_post, "POSTED_LOG_FILE", str(log_file))

    for i in range(2):
        auto_post.append_posted_log({
            "date_kst": f"2026-08-1{i}T07:00:00+09:00", "post_type": "lead", "topic": "t", "keyword": "k",
            "cta_id": 0, "queue_left": 1, "thread_id": str(i), "is_draft": False,
        })

    lines = log_file.read_text(encoding="utf-8").splitlines()
    assert lines[0] == ",".join(auto_post.POSTED_LOG_COLUMNS)
    assert len(_read_rows(log_file)) == 2


def test_main_appends_log_row_on_open_publish(tmp_path, monkeypatch):
    monkeypatch.setattr(auto_post, "POSTED_FILE", str(tmp_path / "posted.json"))
    monkeypatch.setattr(auto_post, "POSTED_LOG_FILE", str(tmp_path / "posted_log.csv"))
    monkeypatch.setattr(auto_post, "TOPIC_DOC_FILE", str(tmp_path / "글감_발행목록.md"))
    monkeypatch.setattr(cta, "LAST_CTA_FILE", str(tmp_path / "last_cta.json"))
    monkeypatch.setattr(config, "THREADS_ACCESS_TOKEN", "fake")
    monkeypatch.setattr(config, "NOTION_TOKEN", "fake")
    monkeypatch.setattr(config, "NOTION_DATABASE_ID", "fake")
    monkeypatch.setattr(config, "GEMINI_API_KEY", "fake")
    monkeypatch.setattr(notion_api, "get_lead_ready_keywords", lambda: set())
    monkeypatch.setattr(auto_post.requests, "post", _mock_gemini_post)
    monkeypatch.setattr(threads_api, "publish_text", lambda text, reply_to_id=None, wait_seconds=None: "fake-post-id")
    monkeypatch.setenv("POST_TYPE", "open")
    monkeypatch.setenv("FORCE_WINDOW", "test")

    auto_post.main()

    rows = _read_rows(tmp_path / "posted_log.csv")
    assert len(rows) == 1
    assert rows[0]["post_type"] == "open"
    assert rows[0]["thread_id"] == "fake-post-id"
    assert rows[0]["topic"] != ""
