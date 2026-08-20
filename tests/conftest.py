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


@pytest.fixture(autouse=True)
def isolate_repo_files(tmp_path, monkeypatch):
    monkeypatch.setattr(auto_post, "POSTED_FILE", str(tmp_path / "posted.json"))
    monkeypatch.setattr(auto_post, "POSTED_LOG_FILE", str(tmp_path / "posted_log.csv"))
    monkeypatch.setattr(auto_post, "TOPIC_DOC_FILE", str(tmp_path / "글감_발행목록.md"))
    monkeypatch.setattr(cta, "LAST_CTA_FILE", str(tmp_path / "last_cta.json"))
