# -*- coding: utf-8 -*-
"""인스타그램 카드 발행(instagram_api) + auto_post 연동 테스트."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from PIL import Image

import auto_post
import card_news
import config
import instagram_api
import notion_api
import threads_api


class Resp:
    def __init__(self, data=None, ok=True, status=200, text=""):
        self._data, self.ok, self.status_code, self.text = data or {}, ok, status, text

    def json(self):
        return self._data


@pytest.fixture
def ig(monkeypatch):
    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN", "tok")
    monkeypatch.setenv("INSTAGRAM_USER_ID", "123")
    monkeypatch.setattr(instagram_api.time, "sleep", lambda *a, **k: None)
    instagram_api._account_cache.clear()
    calls = {"post": [], "get": []}
    n = {"i": 0}

    def post(url, params=None, timeout=None):
        calls["post"].append((url, dict(params)))
        n["i"] += 1
        return Resp({"id": f"c{n['i']}"})

    def get(url, params=None, timeout=None):
        calls["get"].append((url, dict(params)))
        return Resp({"status_code": calls.get("status", "FINISHED"), "user_id": "123", "username": "sodam_ai_lab"})

    monkeypatch.setattr(instagram_api.requests, "post", post)
    monkeypatch.setattr(instagram_api.requests, "get", get)
    return calls


def test_enabled_needs_only_the_token(monkeypatch):
    monkeypatch.delenv("INSTAGRAM_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("INSTAGRAM_USER_ID", raising=False)
    assert instagram_api.enabled() is False
    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN", "t")
    assert instagram_api.enabled() is True        # 계정 번호는 토큰으로 알아내므로 INSTAGRAM_USER_ID는 없어도 된다


def test_account_id_comes_from_the_token_not_the_secret(ig, monkeypatch):
    monkeypatch.setenv("INSTAGRAM_USER_ID", "1129090112816803")      # 앱 ID를 잘못 넣은 경우
    instagram_api._account_cache.clear()
    instagram_api.publish_cards(["https://x/1.jpg"], "본문", "briefing")
    media_create = [p for p in ig["post"] if p[0].endswith("/media")][0]
    assert "/123/media" in media_create[0] and "1129090112816803" not in media_create[0]   # /me가 알려준 번호 123을 쓴다


def test_caption_has_hashtags_and_respects_limit():
    cap = instagram_api.build_caption("본문입니다", "card_prompt")
    assert cap.startswith("본문입니다") and "#AI프롬프트" in cap
    long = instagram_api.build_caption("가" * 5000, "briefing")
    assert len(long) <= 2200 and long.endswith("#소담AI랩")
    assert "#소담AI랩" in instagram_api.build_caption("x", "unknown-type")


def test_single_image_flow(ig):
    media_id = instagram_api.publish_cards(["https://x/1.jpg"], "본문", "briefing")
    assert media_id
    create = ig["post"][0]
    assert create[0].endswith("/123/media") and create[1]["image_url"] == "https://x/1.jpg"
    assert "#AI뉴스" in create[1]["caption"] and create[1]["access_token"] == "tok"
    assert ig["post"][-1][0].endswith("/123/media_publish")


def test_carousel_flow_builds_children_then_parent(ig):
    instagram_api.publish_cards(["u1", "u2", "u3", "u4"], "본문", "card_prompt")
    creates = [p[1] for p in ig["post"] if p[0].endswith("/media")]
    assert [c.get("is_carousel_item") for c in creates[:4]] == ["true"] * 4
    parent = creates[4]
    assert parent["media_type"] == "CAROUSEL" and parent["children"] == "c1,c2,c3,c4"
    assert "#AI프롬프트" in parent["caption"]
    assert ig["post"][-1][0].endswith("/media_publish")


def test_carousel_size_limits(ig):
    with pytest.raises(ValueError):
        instagram_api.publish_carousel(["only-one"], "x")
    with pytest.raises(ValueError):
        instagram_api.publish_carousel([f"u{i}" for i in range(11)], "x")


def test_container_error_stops_before_publishing(ig):
    ig["status"] = "ERROR"
    with pytest.raises(RuntimeError):
        instagram_api.publish_carousel(["u1", "u2"], "x")
    assert not any(p[0].endswith("/media_publish") for p in ig["post"])


def test_jpeg_copy_keeps_card_size(tmp_path):
    png = tmp_path / "c.png"
    Image.new("RGB", (1080, 1350), (80, 30, 160)).save(png)
    jpg = card_news.save_jpeg_copy(str(png))
    assert jpg.endswith(".jpg") and Image.open(jpg).size == (1080, 1350) and Image.open(jpg).format == "JPEG"


# ── auto_post 연동 ────────────────────────────────────────────────

CARD_OK = """[카드제목]
회의록 정리, 이 문장 하나로 끝나요
[상황]
회의 메모가 엉망일 때
[프롬프트]
아래 회의 메모를 정리해줘.
표로 보여주고 없는 내용은 지어내지 마.
[여기에 회의 메모 붙여넣기]
[결과예시]
결정된 일: 문구는 이번 주 확정
[팁]
마음에 안 들면 더 짧게 부탁해 보세요
[후킹]
회의 끝나고 메모가 엉망이신가요"""


@pytest.fixture
def run_env(tmp_path, monkeypatch):
    for name in ("THREADS_ACCESS_TOKEN", "NOTION_TOKEN", "NOTION_DATABASE_ID", "GEMINI_API_KEY"):
        monkeypatch.setattr(config, name, "x")
    monkeypatch.setattr(notion_api, "get_pages", lambda: [])
    monkeypatch.setattr(auto_post, "fetch_news", lambda *a, **k: [])
    monkeypatch.setattr(auto_post.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(auto_post, "_card_dir", lambda: str(tmp_path / "cards"))
    # PNG 4장 + JPEG 4장 순서로 공개 URL을 돌려준다 (실제 git push 대신)
    monkeypatch.setattr(auto_post, "_publish_card_files",
                        lambda paths: [f"https://img/{os.path.basename(p)}" for p in paths])
    monkeypatch.setattr(auto_post, "_call_gemini", lambda prompt, max_tokens=420: CARD_OK)
    monkeypatch.setenv("FORCE_WINDOW", "force")
    monkeypatch.setenv("POST_TYPE", "card_prompt")
    monkeypatch.delenv("DRY_RUN", raising=False)
    sent = {"carousel": [], "ig": []}
    monkeypatch.setattr(threads_api, "publish_carousel",
                        lambda urls, text=None, topic_tag=None: sent["carousel"].append(urls) or "t-1")
    monkeypatch.setattr(threads_api, "publish_text", lambda *a, **k: "t-text")
    return sent


def test_card_post_goes_to_instagram_with_jpeg_urls(run_env, monkeypatch):
    monkeypatch.setattr(instagram_api, "enabled", lambda: True)
    monkeypatch.setattr(instagram_api, "publish_cards",
                        lambda urls, text, kind: run_env["ig"].append((urls, kind)) or "ig-9")
    auto_post.main()
    urls, kind = run_env["ig"][0]
    assert kind == "card_prompt" and len(urls) == 4 and all(u.endswith(".jpg") for u in urls)
    assert all(u.endswith(".png") for u in run_env["carousel"][0])         # 스레드에는 PNG
    entry = [v for k, v in auto_post.load_posted()["posted"].items() if k.endswith("-evening")][0]
    assert entry["instagram_id"] == "ig-9" and entry["post_id"] == "t-1"


def test_instagram_failure_never_breaks_the_threads_post(run_env, monkeypatch, capsys):
    monkeypatch.setattr(instagram_api, "enabled", lambda: True)

    def boom(*a, **k):
        raise RuntimeError("인스타 오류")

    monkeypatch.setattr(instagram_api, "publish_cards", boom)
    auto_post.main()                                                         # 예외 없이 끝나야 한다
    state = auto_post.load_posted()
    assert any(k.endswith("-evening") for k in state["posted"]) and state["corner_seq"] == {"card_prompt": 1}
    assert "인스타 발행 실패" in capsys.readouterr().out


def test_instagram_is_skipped_when_not_configured(run_env, monkeypatch):
    monkeypatch.setattr(instagram_api, "enabled", lambda: False)
    monkeypatch.setattr(instagram_api, "publish_cards", lambda *a: pytest.fail("설정이 없으면 호출되면 안 된다"))
    auto_post.main()
    assert run_env["carousel"]
