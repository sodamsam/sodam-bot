# -*- coding: utf-8 -*-
"""CTA 로테이션 모듈 테스트."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cta


def test_open_cta_returns_text_from_pool(tmp_path, monkeypatch):
    monkeypatch.setattr(cta, "LAST_CTA_FILE", str(tmp_path / "last_cta.json"))
    text, idx = cta.pick_cta("open")
    assert text == cta.CTA_FOLLOW[idx]


def test_lead_cta_fills_keyword_placeholder(tmp_path, monkeypatch):
    monkeypatch.setattr(cta, "LAST_CTA_FILE", str(tmp_path / "last_cta.json"))
    text, idx = cta.pick_cta("lead", keyword="식단")
    assert "식단" in text
    assert "{keyword}" not in text


def test_never_repeats_immediately(tmp_path, monkeypatch):
    """같은 타입을 연달아 뽑으면 직전 인덱스와는 절대 겹치지 않는다."""
    monkeypatch.setattr(cta, "LAST_CTA_FILE", str(tmp_path / "last_cta.json"))
    prev_idx = None
    for _ in range(30):
        _, idx = cta.pick_cta("open")
        if prev_idx is not None:
            assert idx != prev_idx
        prev_idx = idx


def test_persists_across_calls(tmp_path, monkeypatch):
    """직전 선택이 파일에 저장되어 다음 pick_cta 호출(별도 프로세스 흉내)에도 반영된다."""
    last_file = tmp_path / "last_cta.json"
    monkeypatch.setattr(cta, "LAST_CTA_FILE", str(last_file))

    _, idx1 = cta.pick_cta("open")
    assert last_file.exists()

    # 파일에서 다시 읽어와도 같은 인덱스가 직전 선택으로 기록되어 있어야 한다
    saved = cta._load_last()
    assert saved["open"] == idx1


def test_open_and_lead_tracked_independently(tmp_path, monkeypatch):
    monkeypatch.setattr(cta, "LAST_CTA_FILE", str(tmp_path / "last_cta.json"))
    _, open_idx = cta.pick_cta("open")
    _, lead_idx = cta.pick_cta("lead", keyword="회의")
    saved = cta._load_last()
    assert saved["open"] == open_idx
    assert saved["lead"] == lead_idx
