# -*- coding: utf-8 -*-
"""공용 pytest fixture.

테스트가 auto_post.main() 등을 거쳐 발행 흐름을 실행하면 기본적으로
실제 저장소 파일(state/posted.json, data/posted_log.csv,
docs/글감_발행목록.md, state/last_cta.json)에 쓰게 된다. 이 conftest는
그 네 경로를 모든 테스트에서 자동으로 tmp_path 하위로 격리해,
개별 테스트가 깜빡하고 monkeypatch를 빠뜨려도 실제 파일이 오염되지 않게 한다.

개별 테스트가 자신만의 tmp_path 하위 경로로 다시 monkeypatch.setattr을
호출하면 그 값이 이 fixture의 기본값을 덮어쓴다(동작 그대로 유지, 이름만 다름).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import auto_post
import cta
import hotnews
import reel_maker

REAL_MAKE_REEL = reel_maker.make_reel             # 가짜로 바꾸기 전의 진짜 영상 생성 함수
REAL_SEARCH_INTEREST = hotnews.search_interest   # 네트워크 차단 전의 원래 함수(fetcher를 직접 넘기는 테스트용)


@pytest.fixture(autouse=True)
def isolate_repo_files(tmp_path, monkeypatch):
    monkeypatch.setattr(auto_post, "POSTED_FILE", str(tmp_path / "posted.json"))
    monkeypatch.setattr(auto_post, "POSTED_LOG_FILE", str(tmp_path / "posted_log.csv"))
    monkeypatch.setattr(auto_post, "TOPIC_DOC_FILE", str(tmp_path / "글감_발행목록.md"))
    monkeypatch.setattr(cta, "LAST_CTA_FILE", str(tmp_path / "last_cta.json"))


@pytest.fixture(autouse=True)
def no_real_news_fetch(monkeypatch):
    """테스트가 실제 인터넷(구글 뉴스·네이버 자동완성)에 접속하지 않게 막는다. 필요한 테스트가 직접 다시 지정한다."""
    monkeypatch.setattr(hotnews, "fetch_news_kr", lambda *a, **k: [])
    monkeypatch.setattr(hotnews, "search_interest", lambda *a, **k: {})


@pytest.fixture(autouse=True)
def no_real_card_publish(tmp_path, monkeypatch):
    """테스트가 실제 저장소에 카드·영상을 커밋·푸시하거나 ffmpeg을 돌리지 않게 막는다. 필요한 테스트가 직접 다시 지정한다."""
    monkeypatch.setattr(auto_post, "_card_dir", lambda: str(tmp_path / "cards"))
    monkeypatch.setattr(auto_post, "_publish_card_files",
                        lambda paths: [f"https://img/{os.path.basename(p)}" for p in paths])
    monkeypatch.setattr(auto_post, "_publish_video_file", lambda path: [f"https://vid/{os.path.basename(path)}"])

    def fake_reel(image_paths, out_path, seed="", bgm_path=None):
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        open(out_path, "wb").write(b"mp4")
        return out_path
    monkeypatch.setattr(reel_maker, "make_reel", fake_reel)
