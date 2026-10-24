# -*- coding: utf-8 -*-
"""운영 자동화 도구(tools/health_check.py, tools/rotate_threads_token.py) 테스트."""
import datetime
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import pytest

import health_check
import rotate_threads_token as rotate

KST = health_check.KST


def _kst(y, m, d, h=21):
    return datetime.datetime(y, m, d, h, 10, tzinfo=KST)


# ── health_check ────────────────────────────────────────────────

def test_posted_today_ok():
    state = {"posted": {"2026-10-24": {"type": "howto", "post_id": "1"}}}
    ok, msg = health_check.check_posted_today(state, _kst(2026, 10, 24))
    assert ok and "howto" in msg


def test_posted_today_missing_is_failure():
    state = {"posted": {"2026-10-23": {"type": "open", "post_id": "1"}}}
    ok, msg = health_check.check_posted_today(state, _kst(2026, 10, 24))
    assert not ok and "2026-10-24" in msg


def test_posted_today_empty_state():
    ok, _ = health_check.check_posted_today({}, _kst(2026, 10, 24))
    assert not ok


def test_comment_bot_recent_ok():
    now = datetime.datetime(2026, 10, 24, 12, 0, tzinfo=datetime.timezone.utc)
    ok, _ = health_check.check_comment_bot_recency("2026-10-24T11:20:00Z", now)
    assert ok


def test_comment_bot_stale_warns_with_hint():
    now = datetime.datetime(2026, 10, 24, 12, 0, tzinfo=datetime.timezone.utc)
    ok, msg = health_check.check_comment_bot_recency("2026-10-24T07:00:00Z", now)
    assert not ok and "cron-job.org" in msg


def test_comment_bot_no_runs():
    now = datetime.datetime(2026, 10, 24, 12, 0, tzinfo=datetime.timezone.utc)
    ok, _ = health_check.check_comment_bot_recency(None, now)
    assert not ok


def test_threads_token_empty():
    ok, _ = health_check.check_threads_token("")
    assert not ok


# ── rotate_threads_token ────────────────────────────────────────

class _Resp:
    def __init__(self, ok=True, data=None, status=200, text=""):
        self.ok, self._data, self.status_code, self.text = ok, data or {}, status, text

    def json(self):
        return self._data


def test_refresh_token_returns_new_token(monkeypatch):
    monkeypatch.setattr(rotate.requests, "get",
                        lambda *a, **k: _Resp(data={"access_token": "NEW", "expires_in": 5184000}))
    assert rotate.refresh_token("OLD") == ("NEW", 5184000)


def test_refresh_token_failure_raises(monkeypatch):
    monkeypatch.setattr(rotate.requests, "get",
                        lambda *a, **k: _Resp(ok=False, status=400, text="expired"))
    with pytest.raises(RuntimeError):
        rotate.refresh_token("OLD")


def test_main_requires_admin_token(monkeypatch, capsys):
    monkeypatch.setenv("THREADS_ACCESS_TOKEN", "OLD")
    monkeypatch.delenv("GH_ADMIN_TOKEN", raising=False)
    assert rotate.main() == 1
    assert "GH_ADMIN_TOKEN" in capsys.readouterr().out


def test_main_does_not_store_when_new_token_rejected(monkeypatch, capsys):
    monkeypatch.setenv("THREADS_ACCESS_TOKEN", "OLD")
    monkeypatch.setenv("GH_ADMIN_TOKEN", "ADMIN")
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setattr(rotate, "refresh_token", lambda t: ("NEW", 5184000))
    monkeypatch.setattr(rotate, "token_works", lambda t: False)
    stored = []
    monkeypatch.setattr(rotate, "store_secret", lambda *a: stored.append(a))
    assert rotate.main() == 1
    assert stored == []


def test_main_success_masks_and_stores(monkeypatch, capsys):
    monkeypatch.setenv("THREADS_ACCESS_TOKEN", "OLD")
    monkeypatch.setenv("GH_ADMIN_TOKEN", "ADMIN")
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setattr(rotate, "refresh_token", lambda t: ("NEW-SECRET", 5184000))
    monkeypatch.setattr(rotate, "token_works", lambda t: True)
    stored = []
    monkeypatch.setattr(rotate, "store_secret", lambda *a: stored.append(a))
    assert rotate.main() == 0
    assert stored == [("NEW-SECRET", "o/r", "ADMIN")]
    out = capsys.readouterr().out
    assert "::add-mask::NEW-SECRET" in out
    assert out.count("NEW-SECRET") == 1  # 마스킹 지시줄 외에는 값이 출력되지 않아야 함


def test_evening_corner_check_is_a_warning_level_signal():
    state = {"posted": {"2026-10-26-evening": {"type": "term"}}}
    assert health_check.check_evening_posted(state, _kst(2026, 10, 26))[0]
    ok, msg = health_check.check_evening_posted({"posted": {}}, _kst(2026, 10, 26))
    assert not ok and "저녁 코너" in msg


def test_evening_fallback_post_is_reported():
    state = {"posted": {"2026-10-26-evening": {"type": "open"}}}
    ok, msg = health_check.check_evening_posted(state, _kst(2026, 10, 26))
    assert not ok and "대체 글" in msg
