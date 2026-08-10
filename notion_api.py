# -*- coding: utf-8 -*-
"""Notion API — 자료 나눔 데이터베이스 연동 (댓글 자동 응답 / 키워드 확인용).

노션 DB 구조 (권장):
  자료 DB (NOTION_DATABASE_ID) — 댓글 자동 응답 및 프롬프트 나눔 키워드 확인용
  - 제목 속성(title): 자료 이름
  - "키워드" 속성(텍스트): 이 중 하나라도 댓글에 있으면 이 페이지를 보냄.
    쉼표(,)로 여러 개를 넣을 수 있음 (예: "회의, 회의록")
  - 노션에서 "공유 > 게시(Publish)"를 켜야 public_url이 내려오고,
    대댓글도 이 주소(.notion.site)로 나감. 게시가 꺼진 페이지는 전송하지 않음.

  DB 안에 이름이 NOTION_HUB_TITLE(기본값 "AI 프롬프트 5종 모음")인 페이지를 하나 두면,
  구체적인 키워드가 매칭되지 않고 기본 키워드(DEFAULT_KEYWORD, 예: "신청")만
  댓글에 있을 때 이 허브 페이지가 대신 나감.
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


def _extract_keyword_text(page):
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


def _split_keywords(raw_text):
    """쉼표로 구분된 키워드 문자열을 개별 키워드 목록으로 분리한다 (앞뒤 공백 제거, 빈 값 제외)."""
    return [k.strip() for k in raw_text.split(",") if k.strip()]


def _clean_public_url(url):
    """URL 끝의 ?pvs=... 같은 쿼리 파라미터를 제거한다. 값이 없으면 None."""
    if not url:
        return None
    return url.split("?")[0]


def get_pages():
    """DB의 페이지들을 최신 생성순으로 반환.

    반환: [{"title": str, "url": str, "public_url": str|None, "keywords": [str, ...]}, ...]
    public_url이 None이면 해당 페이지가 아직 "웹에 게시"되지 않은 것.
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
            "public_url": _clean_public_url(page.get("public_url")),
            "keywords": _split_keywords(_extract_keyword_text(page)),
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
    except Exception as e:
        print(f"[키워드 확인] '{keyword}' 노션 DB 조회 실패({e}) → False로 간주")
        return False
    result = any(keyword in p.get("keywords", []) for p in pages)
    print(f"[키워드 확인] '{keyword}' 노션 DB 조회 → {result}")
    return result


def has_any_material():
    """봇 자료 DB에 자료가 하나라도 있는지 확인. 실패 시 False로 간주."""
    try:
        pages = get_pages()
    except Exception as e:
        print(f"[자료 확인] 노션 DB 조회 실패({e}) → False로 간주")
        return False
    result = len(pages) > 0
    print(f"[자료 확인] 노션 DB 자료 개수={len(pages)} → {result}")
    return result


def match_page(comment_text, pages):
    """댓글 텍스트에 맞는 노션 페이지를 찾는다.

    1) 페이지별 키워드(쉼표로 여러 개 가능) 중 하나라도 댓글에 포함되면 그 페이지
       (여러 키워드가 동시에 걸리면 더 긴 키워드를 우선)
    2) 구체 키워드가 안 걸리고 기본 키워드(DEFAULT_KEYWORD, 예: "신청")만 댓글에 있으면
       허브 페이지(NOTION_HUB_TITLE)를 반환한다.
       허브 페이지를 DB에서 못 찾으면 None (최신 페이지로 절대 대체하지 않음 —
       엉뚱한 자료가 나가는 걸 막기 위함)
    3) 둘 다 아니면 None
    """
    text = (comment_text or "").strip()
    if not text:
        return None

    # 1) 구체 키워드 매칭 — 모든 페이지의 모든 키워드를 모아 긴 것부터 검사
    keyword_hits = [(kw, p) for p in pages for kw in p["keywords"]]
    keyword_hits.sort(key=lambda item: len(item[0]), reverse=True)
    for kw, p in keyword_hits:
        if kw in text:
            print(f"[매칭] 댓글='{text}' → 키워드 '{kw}' 매칭")
            return p

    # 2) 구체 키워드가 없을 때 기본 키워드(예: "신청") → 허브 페이지
    if config.DEFAULT_KEYWORD and config.DEFAULT_KEYWORD in text:
        hub_title = (config.NOTION_HUB_TITLE or "").strip()
        hub = next((p for p in pages if p["title"] == hub_title), None)
        if hub:
            print(f"[매칭] 댓글='{text}' → 허브 페이지 전송")
            return hub
        print(f"[경고] 허브 페이지('{hub_title}')를 노션 DB에서 찾지 못함 → 전송 스킵")
        return None

    return None
