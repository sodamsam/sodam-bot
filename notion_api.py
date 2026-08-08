# -*- coding: utf-8 -*-
"""Notion API — 자료 나눔 데이터베이스 연동 (댓글 자동 응답 / 키워드 확인용).

노션 DB 구조 (권장):
  자료 DB (NOTION_DATABASE_ID) — 댓글 자동 응답 및 프롬프트 나눔 키워드 확인용
  - 제목 속성(title): 자료 이름
  - "키워드" 속성(텍스트 또는 선택): 이 키워드가 댓글에 있으면 이 페이지 링크를 보냄
    (비워두면 키워드 매칭 대상에서 제외되고, 최신 페이지 후보로만 사용됨)
"""
import requests
import config

TIMEOUT = 30
NOTION_API = "https://api.notion.com/v1"
HEADERS_BASE = {
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}


def _headers():
    h = dict(HEADERS_BASE)
    h["Authorization"] = f"Bearer {config.NOTION_TOKEN}"
    return h


def _extract_title(page):
    for prop in page.get("properties", {}).values():
        if prop.get("type") == "title":
            parts = prop.get("title", [])
            return "".join(p.get("plain_text", "") for p in parts).strip()
    return "(제목 없음)"


def _extract_keyword(page):
    prop = page.get("properties", {}).get(config.NOTION_KEYWORD_PROP)
    if not prop:
        return ""
    t = prop.get("type")
    if t == "rich_text":
        return "".join(p.get("plain_text", "") for p in prop.get("rich_text", [])).strip()
    if t == "select":
        sel = prop.get("select")
        return (sel or {}).get("name", "").strip()
    if t == "title":
        return "".join(p.get("plain_text", "") for p in prop.get("title", [])).strip()
    return ""


def get_pages():
    """DB의 페이지들을 최신 생성순으로 반환.

    반환: [{"title": str, "url": str, "keyword": str}, ...]
    """
    url = f"{NOTION_API}/databases/{config.NOTION_DATABASE_ID}/query"
    body = {
        "sorts": [{"timestamp": "created_time", "direction": "descending"}],
        "page_size": 50,
    }
    r = requests.post(url, headers=_headers(), json=body, timeout=TIMEOUT)
    if not r.ok:
        raise RuntimeError(f"Notion DB 조회 실패: {r.status_code} {r.text[:300]}")
    pages = []
    for page in r.json().get("results", []):
        pages.append({
            "title": _extract_title(page),
            "url": page.get("url", ""),
            "keyword": _extract_keyword(page),
        })
    return pages


def has_keyword(keyword):
    """자료 나눔 DB에 해당 키워드가 실제로 등록되어 있는지 확인한다.

    조회 실패(네트워크 오류 등) 시에는 False로 간주한다.
    없는 자료를 안내하는 것보다 안 넣는 게 안전하다.
    """
    if not keyword:
        return False
    try:
        pages = get_pages()
    except Exception:
        return False
    return any(p.get("keyword") == keyword for p in pages)


def match_page(comment_text, pages):
    """댓글 텍스트에 맞는 노션 페이지를 찾는다.

    1) 페이지별 키워드가 댓글에 포함되면 그 페이지 (긴 키워드 우선)
    2) 아니면 기본 키워드(DEFAULT_KEYWORD)가 댓글에 있으면 최신 페이지
    3) 둘 다 아니면 None
    """
    text = (comment_text or "").strip()
    if not text:
        return None

    keyword_pages = [p for p in pages if p["keyword"]]
    keyword_pages.sort(key=lambda p: len(p["keyword"]), reverse=True)
    for p in keyword_pages:
        if p["keyword"] in text:
            return p

    if config.DEFAULT_KEYWORD and config.DEFAULT_KEYWORD in text and pages:
        return pages[0]  # 최신 페이지

    return None
