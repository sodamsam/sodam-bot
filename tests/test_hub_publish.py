# -*- coding: utf-8 -*-
"""허브 예약 글 발행기(hub_publish.py) — 시각 판단과 형식 검사."""
import datetime

import hub_publish

KST = hub_publish.KST
NOW = datetime.datetime(2026, 11, 16, 19, 0, tzinfo=KST)


def test_empty_time_is_due_now():
    assert hub_publish.is_due({"publish_at": ""}, NOW)
    assert hub_publish.is_due({}, NOW)


def test_future_time_waits_and_past_time_goes():
    assert not hub_publish.is_due({"publish_at": "2026-11-16T19:01"}, NOW)
    assert hub_publish.is_due({"publish_at": "2026-11-16T19:00"}, NOW)
    assert hub_publish.is_due({"publish_at": "2026-11-16T07:00"}, NOW)


def test_broken_time_never_publishes():
    assert not hub_publish.is_due({"publish_at": "내일 저녁"}, NOW)


def test_validate_catches_bad_jobs():
    assert hub_publish.validate({}) is not None
    assert hub_publish.validate({"threads": {"parts": []}}) is not None
    assert hub_publish.validate({"threads": {"parts": ["가" * 501]}}) is not None
    assert hub_publish.validate({"instagram": {"headline": "", "caption": "글"}}) is not None
    assert hub_publish.validate({"instagram": {"headline": "제목", "caption": "가" * 2201}}) is not None


def test_validate_passes_good_job():
    job = {"threads": {"parts": ["본문", "이어지는 댓글"], "tag": "AI활용"},
           "instagram": {"headline": "제목", "caption": "글"}}
    assert hub_publish.validate(job) is None
