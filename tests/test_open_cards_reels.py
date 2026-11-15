# -*- coding: utf-8 -*-
"""무료 프롬프트 나눔(OPEN) 글의 인스타 카드 + 릴스 발행 테스트."""
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from PIL import Image

import auto_post
import card_news
import conftest  # noqa: F401
import instagram_api
import notion_api
import reel_maker

OPEN_RAW = "[후킹]\n회의 메모가 엉망일 때\n[프롬프트]\n아래 회의 메모를 정리해 주세요.\n[여기에 붙여넣기]\n[예시]\n결정된 일: 장소 확정"


def test_open_post_returns_card_material(monkeypatch):
    monkeypatch.setattr(notion_api, "get_lead_ready_keywords", lambda: set())
    monkeypatch.setattr(auto_post, "_call_gemini", lambda *a, **k: OPEN_RAW)
    r = auto_post.write_queued_prompt_post({}, auto_post.datetime.datetime.now(auto_post.KST), forced_type="open")
    card = r["card"]
    assert card["series"] == "무료 프롬프트 나눔"
    assert card["headline"] == r["topic"]
    assert "아래 회의 메모를 정리해 주세요." in card["prompt"] and "[여기에 붙여넣기]" in card["prompt"]
    assert card["situation"] == "회의 메모가 엉망일 때"
    assert card["prompt"].splitlines()[0] in r["text"]          # 카드와 글의 문장이 같다


def test_lead_post_has_no_card(monkeypatch):
    monkeypatch.setattr(auto_post, "LEAD_ENABLED", True)
    monkeypatch.setattr(notion_api, "get_lead_ready_keywords", lambda: {"회의"})
    monkeypatch.setattr(auto_post, "_call_gemini", lambda *a, **k: "[후킹]\n가\n[이유]\n나\n[맛보기]\n다")
    r = auto_post.write_queued_prompt_post({}, auto_post.datetime.datetime.now(auto_post.KST), forced_type="lead")
    assert r["post_type"] == "lead" and r["card"] is None


def test_make_open_cards_makes_four_jpegs_only(tmp_path):
    card = {"series": "무료 프롬프트 나눔", "headline": "회의록 정리하기", "situation": "메모가 엉망일 때",
            "prompt": "아래 메모를 정리해 주세요\n[여기에 붙여넣기]", "example": "결정된 일: 확정", "tip": "더 짧게 부탁해 보세요"}
    urls, paths = auto_post.make_open_cards(card, "2026-11-16")
    assert len(urls) == 4 and len(paths) == 4
    for p in paths:
        assert p.endswith(".jpg") and Image.open(p).size == (1080, 1350)
    assert not [f for f in os.listdir(os.path.dirname(paths[0])) if f.endswith(".png")]


@pytest.fixture
def ig_calls(monkeypatch):
    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN", "tok")
    monkeypatch.setattr(instagram_api.time, "sleep", lambda *a, **k: None)
    calls = []

    def fake_request(method, path, params):
        calls.append((method, path, dict(params)))
        if path == "/me":
            return {"user_id": "777"}
        if method == "GET":
            return {"status_code": "FINISHED"}
        return {"id": "c1" if path.endswith("/media") else "reel99"}
    monkeypatch.setattr(instagram_api, "_request", fake_request)
    instagram_api._account_cache.clear()
    return calls


def test_publish_reel_sends_reels_container(ig_calls):
    assert instagram_api.publish_reel("https://vid/a.mp4", "캡션") == "reel99"
    create = [c for c in ig_calls if c[1] == "/777/media"][0][2]
    assert create["media_type"] == "REELS" and create["video_url"] == "https://vid/a.mp4"
    assert create["share_to_feed"] == "false"


def test_reel_from_cards_needs_two_or_more(ig_calls, tmp_path):
    assert auto_post.publish_reel_from_cards([str(tmp_path / "a.jpg")], "글", "briefing", "2026-11-16") is None
    assert not [c for c in ig_calls if c[1].endswith("/media")]


def test_reel_from_cards_publishes(ig_calls, tmp_path):
    paths = [str(tmp_path / f"{i}.jpg") for i in range(4)]
    assert auto_post.publish_reel_from_cards(paths, "글", "open", "2026-11-16") == "reel99"
    create = [c for c in ig_calls if c[1] == "/777/media"][0][2]
    assert create["video_url"].startswith("https://vid/reel_open_2026-11-16")
    assert "#소담AI랩" in create["caption"]


def test_reel_can_be_turned_off(ig_calls, tmp_path, monkeypatch):
    monkeypatch.setattr(auto_post, "REELS_ENABLED", False)
    assert auto_post.publish_reel_from_cards(["a.jpg", "b.jpg"], "글", "open", "2026-11-16") is None


def test_real_reel_file_has_video_and_music(tmp_path, monkeypatch):
    """실제 ffmpeg으로 영상이 만들어지는지(있을 때만). conftest의 가짜 make_reel 대신 진짜를 쓴다."""
    real = conftest.REAL_MAKE_REEL
    try:
        import imageio_ffmpeg  # noqa: F401
    except Exception:
        if not shutil.which("ffmpeg"):
            pytest.skip("ffmpeg 없음")
    card = {"series": "무료 프롬프트 나눔", "headline": "회의록 정리하기", "situation": "", "prompt": "가", "example": "나", "tip": "다"}
    pngs = card_news.generate_prompt_slides(card, str(tmp_path / "c"), "t")
    jpgs = [card_news.save_jpeg_copy(p) for p in pngs[:2]]
    out = real(jpgs, str(tmp_path / "r.mp4"), seed="x")
    assert os.path.getsize(out) > 20000
    assert reel_maker.pick_bgm("x") and reel_maker.pick_bgm("x") == reel_maker.pick_bgm("x")
    assert abs(reel_maker.reel_length(2) - 7.6) < 0.01
