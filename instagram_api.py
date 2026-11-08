# -*- coding: utf-8 -*-
"""인스타그램 피드 발행 (Instagram API with Instagram Login, graph.instagram.com).

카드뉴스(여러 장/한 장)를 스레드에 올린 직후 같은 카드를 인스타 피드에도 올린다.
INSTAGRAM_ACCESS_TOKEN이 없으면 아무 일도 하지 않는다(enabled()가 False).
게시할 계정 번호는 토큰으로 /me를 조회해 직접 알아낸다(INSTAGRAM_USER_ID는 없어도 되고, 틀려도 영향 없음).
Meta 화면에는 앱별 번호와 인스타 계정 번호가 함께 보여 헷갈리기 쉬워서, 사람이 넣는 값에 기대지 않는다.

인스타 API 제약
  - 이미지는 JPEG만 가능하고 공개 URL이어야 한다 (그래서 카드의 JPEG 사본을 저장소에 함께 올린다)
  - 캐러셀은 2~10장, 비율은 4:5(1080x1350)~1.91:1
  - 캡션 2,200자, 해시태그 30개까지
"""
import os
import time

import requests

API_BASE = "https://graph.instagram.com/v21.0"
TIMEOUT = 30

# 글 종류별 해시태그 (인스타는 검색·추천에 해시태그가 쓰인다. 스레드 글에는 붙이지 않는다)
HASHTAGS = {
    "card_prompt": "#AI프롬프트 #챗GPT #AI활용 #직장인AI #업무자동화 #프롬프트 #소담AI랩",
    "briefing": "#AI뉴스 #AI소식 #챗GPT #클로드 #AI활용 #소담AI랩",
}
DEFAULT_HASHTAGS = "#AI활용 #챗GPT #직장인AI #소담AI랩"


def _token():
    return os.environ.get("INSTAGRAM_ACCESS_TOKEN", "")


def _user_id():
    return os.environ.get("INSTAGRAM_USER_ID", "")


def enabled():
    return bool(_token())


_account_cache = {}


def account_id():
    """토큰이 가리키는 인스타 프로페셔널 계정 번호(user_id). 같은 토큰이면 한 번만 조회한다."""
    token = _token()
    if _account_cache.get("token") != token:
        info = _request("GET", "/me", {"fields": "user_id,username"})
        _account_cache.update(token=token, id=info.get("user_id") or info.get("id") or _user_id())
    return _account_cache["id"]


def _request(method, path, params):
    params = dict(params, access_token=_token())
    fn = requests.post if method == "POST" else requests.get
    r = fn(f"{API_BASE}{path}", params=params, timeout=TIMEOUT)
    if not r.ok:
        raise RuntimeError(f"Instagram {method} {path} 실패: {r.status_code} {r.text[:300]}")
    return r.json()


def _wait_ready(container_id, timeout=90, interval=3):
    waited = 0
    while waited <= timeout:
        info = _request("GET", f"/{container_id}", {"fields": "status_code"})
        status = info.get("status_code")
        if status in (None, "FINISHED", "PUBLISHED"):
            return
        if status in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"인스타 컨테이너 처리 실패({status})")
        time.sleep(interval)
        waited += interval
    raise RuntimeError("인스타 컨테이너 처리가 시간 안에 끝나지 않았습니다")


def build_caption(text, content_type):
    """스레드 본문을 인스타 캡션으로 만든다: 본문 + 해시태그. 2,200자를 넘으면 본문을 줄인다."""
    tags = HASHTAGS.get(content_type, DEFAULT_HASHTAGS)
    room = 2200 - len(tags) - 2
    body = text if len(text) <= room else text[:room - 1] + "…"
    return f"{body}\n\n{tags}"


def publish_image(image_url, caption):
    """한 장짜리 피드 게시물."""
    uid = account_id()
    container = _request("POST", f"/{uid}/media", {"image_url": image_url, "caption": caption})
    _wait_ready(container["id"])
    return _request("POST", f"/{uid}/media_publish", {"creation_id": container["id"]}).get("id")


def publish_carousel(image_urls, caption):
    """여러 장 넘겨보는 피드 게시물 (2~10장)."""
    if not 2 <= len(image_urls) <= 10:
        raise ValueError("인스타 캐러셀은 이미지 2~10장이 필요합니다")
    uid = account_id()
    child_ids = []
    for url in image_urls:
        child = _request("POST", f"/{uid}/media", {"image_url": url, "is_carousel_item": "true"})
        child_ids.append(child["id"])
    for cid in child_ids:
        _wait_ready(cid)
    parent = _request("POST", f"/{uid}/media",
                      {"media_type": "CAROUSEL", "children": ",".join(child_ids), "caption": caption})
    _wait_ready(parent["id"])
    return _request("POST", f"/{uid}/media_publish", {"creation_id": parent["id"]}).get("id")


def publish_cards(image_urls, text, content_type):
    """카드 이미지 URL 목록을 인스타에 올린다. 한 장이면 단일 게시물, 여러 장이면 캐러셀."""
    caption = build_caption(text, content_type)
    if len(image_urls) >= 2:
        return publish_carousel(image_urls, caption)
    return publish_image(image_urls[0], caption)
