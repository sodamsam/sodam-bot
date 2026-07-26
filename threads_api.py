# -*- coding: utf-8 -*-
"""Threads 공식 API 래퍼 (graph.threads.net)."""
import time
import requests
import config

TIMEOUT = 30


def _get(path, params=None):
    params = dict(params or {})
    params["access_token"] = config.THREADS_ACCESS_TOKEN
    r = requests.get(f"{config.THREADS_API_BASE}{path}", params=params, timeout=TIMEOUT)
    if not r.ok:
        raise RuntimeError(f"Threads GET {path} 실패: {r.status_code} {r.text[:300]}")
    return r.json()


def _post(path, params=None):
    params = dict(params or {})
    params["access_token"] = config.THREADS_ACCESS_TOKEN
    r = requests.post(f"{config.THREADS_API_BASE}{path}", params=params, timeout=TIMEOUT)
    if not r.ok:
        raise RuntimeError(f"Threads POST {path} 실패: {r.status_code} {r.text[:300]}")
    return r.json()


def get_me():
    """내 계정 정보 (id, username)."""
    return _get("/me", {"fields": "id,username"})


def get_recent_posts(limit=5):
    """내 최근 게시물 목록."""
    data = _get("/me/threads", {
        "fields": "id,text,timestamp,permalink",
        "limit": limit,
    })
    return data.get("data", [])


def get_replies(post_id):
    """게시물에 달린 최상위 댓글 목록."""
    data = _get(f"/{post_id}/replies", {
        "fields": "id,text,username,timestamp",
        "limit": 50,
    })
    return data.get("data", [])


def publish_text(text, reply_to_id=None, wait_seconds=None):
    """텍스트 게시물 발행. reply_to_id를 주면 해당 댓글에 대댓글로 달림.

    3단계: (1) 컨테이너 생성 → (2) 서버 처리 대기(공식 권장) → (3) 발행
    wait_seconds 기본값: 본문 글 30초, 대댓글 8초
    """
    params = {"media_type": "TEXT", "text": text}
    if reply_to_id:
        params["reply_to_id"] = reply_to_id
    creation = _post("/me/threads", params)
    creation_id = creation.get("id")
    if not creation_id:
        raise RuntimeError(f"컨테이너 생성 실패: {creation}")
    if wait_seconds is None:
        wait_seconds = 8 if reply_to_id else 30
    time.sleep(wait_seconds)  # Threads 서버가 컨테이너를 처리할 시간 (공식 권장)
    result = _post("/me/threads_publish", {"creation_id": creation_id})
    return result.get("id")
