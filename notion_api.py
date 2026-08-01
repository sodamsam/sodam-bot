# -*- coding: utf-8 -*-
"""Notion API — 자료 데이터베이스 및 저녁 소재 노트 DB 연동.

노션 DB 구조 (권장):
  1) 자료 DB (NOTION_DATABASE_ID) — 댓글 자동 응답용
     - 제목 속성(title): 자료 이름
     - "키워드" 속성(텍스트 또는 선택): 이 키워드가 댓글에 있으면 이 페이지 링크를 보냄
       (비워두면 키워드 매칭 대상에서 제외되고, 최신 페이지 후보로만 사용됨)

  2) "저녁 소재 노트" DB (NOTION_EVENING_DB_ID) — 저녁 "AI로 이렇게 바뀌었어요" 코너용
     - 이름 (제목): 소재 제목
     - 날짜: 있었던 일의 날짜
     - 상황: 그날 있었던 구체적인 상황 (텍스트)
     - ai활용: AI로 무엇을 했는지 (텍스트)
     - 변화/결과: 실제로 달라진 점 (텍스트)
     - 사용여부 (선택: 미사용 / 사용): 발행에 쓴 메모는 자동으로 "사용"으로 바뀜
"""
import requests
import config

TIMEOUT = 30
NOTION_API = "https://api.notion.com/v1"
HEADERS_BASE = {
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}

EVENING_STATUS_PROP = "사용여부"
EVENING_STATUS_UNUSED = "미사용"
EVENING_STATUS_USED = "사용"


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


def _extract_rich_text(prop):
    if not prop:
        return ""
    return "".join(p.get("plain_text", "") for p in prop.get("rich_text", [])).strip()


def _extract_date(prop):
    if not prop:
        return ""
    date = prop.get("date") or {}
    return date.get("start", "") or ""


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


# ── 저녁 소재 노트 (반자동 저녁 발행용) ───────────────────────

def get_unused_evening_note():
    """"저녁 소재 노트" DB에서 사용여부=미사용인 항목 중 날짜가 가장 오래된 것 1개를 가져온다.

    반환: {"page_id", "이름", "날짜", "상황", "ai활용", "변화결과"} 또는 (없으면) None
    """
    url = f"{NOTION_API}/databases/{config.NOTION_EVENING_DB_ID}/query"
    body = {
        "filter": {
            "property": EVENING_STATUS_PROP,
            "select": {"equals": EVENING_STATUS_UNUSED},
        },
        "sorts": [{"property": "날짜", "direction": "ascending"}],
        "page_size": 1,
    }
    r = requests.post(url, headers=_headers(), json=body, timeout=TIMEOUT)
    if not r.ok:
        raise RuntimeError(f"저녁 소재 노트 조회 실패: {r.status_code} {r.text[:300]}")
    results = r.json().get("results", [])
    if not results:
        return None

    page = results[0]
    props = page.get("properties", {})
    return {
        "page_id": page["id"],
        "이름": _extract_title(page),
        "날짜": _extract_date(props.get("날짜")),
        "상황": _extract_rich_text(props.get("상황")),
        "ai활용": _extract_rich_text(props.get("ai활용")),
        "변화결과": _extract_rich_text(props.get("변화/결과")),
    }


def mark_evening_note_used(page_id):
    """발행에 사용한 저녁 소재 노트의 "사용여부"를 "사용"으로 업데이트한다."""
    url = f"{NOTION_API}/pages/{page_id}"
    body = {"properties": {EVENING_STATUS_PROP: {"select": {"name": EVENING_STATUS_USED}}}}
    r = requests.patch(url, headers=_headers(), json=body, timeout=TIMEOUT)
    if not r.ok:
        raise RuntimeError(f"저녁 소재 노트 상태 업데이트 실패: {r.status_code} {r.text[:300]}")
