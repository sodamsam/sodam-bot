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


def _get_all_pages(path, params, max_pages=5):
    """paging.cursors.after를 따라가며 여러 페이지를 이어 붙인다."""
    params = dict(params)
    items = []
    for _ in range(max_pages):
        data = _get(path, params)
        items += data.get("data", [])
        after = (data.get("paging") or {}).get("cursors", {}).get("after")
        if not after or not (data.get("paging") or {}).get("next"):
            break
        params["after"] = after
    return items


def get_replies(post_id):
    """게시물에 달린 모든 댓글(대댓글 포함, 페이지 넘김 포함).

    /conversation은 댓글에 달린 답글까지 한 번에 돌려준다. 예전 /replies는 최상위 댓글만
    주고 50개에서 끊겨서, 답글 형태로 달린 "신청"이나 많은 댓글 중 뒤쪽 것을 놓쳤다.
    /conversation이 실패하면(권한 등) 예전 방식으로 되돌아간다.
    """
    fields = "id,text,username,timestamp"
    try:
        return _get_all_pages(f"/{post_id}/conversation", {"fields": fields, "limit": 50})
    except Exception as e:
        print(f"[댓글 조회] /conversation 실패 → /replies로 대체 ({e})")
        return _get_all_pages(f"/{post_id}/replies", {"fields": fields, "limit": 50})


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


def reply_to(post_id, text):
    """post_id 게시물(또는 댓글)에 내 계정으로 댓글을 단다."""
    return publish_text(text, reply_to_id=post_id, wait_seconds=8)


def publish_image(image_url, text=None, wait_seconds=30):
    """이미지(카드뉴스) 게시물 발행. image_url은 외부에서 접근 가능한 공개 URL이어야 한다.

    3단계: (1) IMAGE 컨테이너 생성 → (2) 서버 처리 대기 → (3) 발행
    """
    params = {"media_type": "IMAGE", "image_url": image_url}
    if text:
        params["text"] = text
    creation = _post("/me/threads", params)
    creation_id = creation.get("id")
    if not creation_id:
        raise RuntimeError(f"이미지 컨테이너 생성 실패: {creation}")
    time.sleep(wait_seconds)
    result = _post("/me/threads_publish", {"creation_id": creation_id})
    return result.get("id")
