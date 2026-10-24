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
    pages = []
    cursor = None
    # 한 번에 100개까지만 오므로 next_cursor를 따라가며 전부 읽는다.
    # (예전에는 50개에서 잘려, 오래된 허브 페이지가 목록 밖으로 밀려나면 "신청" 응답이 멈출 수 있었다)
    for _ in range(20):
        body = {
            "sorts": [{"timestamp": "created_time", "direction": "descending"}],
            "page_size": 100,
        }
        if cursor:
            body["start_cursor"] = cursor
        r = requests.post(url, headers=_headers(), json=body, timeout=TIMEOUT)
        if not r.ok:
            raise RuntimeError(f"Notion DB 조회 실패: {r.status_code} {r.text[:300]}")
        data = r.json()
        for page in data.get("results", []):
            pages.append({
                "title": _extract_title(page),
                "url": page.get("url", ""),
                "public_url": _clean_public_url(page.get("public_url")),
                "keywords": _split_keywords(_extract_keyword_text(page)),
            })
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
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


def get_lead_ready_keywords():
    """LEAD 큐에 쓸 수 있는 키워드 집합을 반환한다.

    노션 자료 DB에 등록만 되어 있는 게 아니라 "웹에 게시(Publish)"까지 켜져
    public_url이 실제로 나오는 페이지의 키워드만 모은다. 게시가 안 된 페이지의
    키워드를 LEAD로 내보내면 신청받을 자료가 없는데 신청 유도만 하게 되므로 제외한다.
    조회 실패 시에는 빈 집합으로 간주한다(LEAD 큐 없음 → OPEN으로 폴백됨).
    """
    try:
        pages = get_pages()
    except Exception as e:
        print(f"[LEAD 키워드 확인] 노션 DB 조회 실패({e}) → 빈 집합으로 간주")
        return set()
    keywords = set()
    for p in pages:
        if p.get("public_url"):
            keywords.update(p.get("keywords", []))
    print(f"[LEAD 키워드 확인] 공개 발행된 키워드: {sorted(keywords)}")
    return keywords


def has_any_material():
    """신청 CTA가 눌렸을 때 실제로 보낼 허브 페이지가 있는지 확인. 실패 시 False로 간주.

    DB에 페이지가 있는지가 아니라, DEFAULT_KEYWORD 댓글에 match_page()가 실제로
    페이지를 찾아 돌려주는지를 확인한다 (체크 대상 = 전송 대상, comment_bot.py와 동일 기준).
    """
    try:
        pages = get_pages()
    except Exception as e:
        print(f"[자료 확인] 노션 DB 조회 실패({e}) → False로 간주")
        return False
    hub = match_page(config.DEFAULT_KEYWORD, pages)
    result = hub is not None
    print(f"[자료 확인] 허브 페이지 → {hub['title'] if hub else '없음'} = {result}")
    return result


def _norm_title(title):
    """제목 비교용 정규화 — 앞뒤·중복 공백과 대소문자 차이 때문에 허브를 못 찾는 일을 막는다."""
    return " ".join((title or "").split()).casefold()


def find_hub(pages):
    """허브 페이지(NOTION_HUB_TITLE)를 찾는다. 없으면 None."""
    hub_title = _norm_title(config.NOTION_HUB_TITLE)
    if not hub_title:
        return None
    return next((p for p in pages if _norm_title(p["title"]) == hub_title), None)


def match_page(comment_text, pages, post_keyword=None):
    """댓글 텍스트에 맞는 노션 페이지를 찾는다.

    0) 이 댓글이 달린 게시물의 키워드(post_keyword)가 댓글에 있으면 그 자료를 우선 보낸다.
       (예: '신청서 초안' 글에 "신청"이라고 남긴 경우는 그 자료)
    1) 페이지별 키워드(쉼표로 여러 개 가능) 중 하나라도 댓글에 포함되면 그 페이지
       (여러 키워드가 동시에 걸리면 더 긴 키워드를 우선)
       단, 기본 키워드(DEFAULT_KEYWORD, 예: "신청")와 똑같은 키워드는 여기서 제외한다.
       이게 허브용 만능 키워드인데 특정 자료의 키워드와 겹치면, 어느 게시물에서 "신청"을
       남기든 그 자료만 나가고 허브(프롬프트 5종 모음)는 영영 못 받는 문제가 생기기 때문이다.
    2) 구체 키워드가 안 걸리고 기본 키워드만 댓글에 있으면 허브 페이지(NOTION_HUB_TITLE)를 반환한다.
       허브 페이지를 DB에서 못 찾으면 None (최신 페이지로 절대 대체하지 않음 —
       엉뚱한 자료가 나가는 걸 막기 위함)
    3) 둘 다 아니면 None
    """
    text = (comment_text or "").strip()
    if not text:
        return None

    default_kw = (config.DEFAULT_KEYWORD or "").strip()

    # 0) 게시물 자체의 키워드
    if post_keyword and post_keyword in text:
        for p in pages:
            if post_keyword in p["keywords"]:
                print(f"[매칭] 댓글='{text}' → 게시물 키워드 '{post_keyword}' 매칭")
                return p

    # 1) 구체 키워드 매칭 — 모든 페이지의 모든 키워드를 모아 긴 것부터 검사
    keyword_hits = [(kw, p) for p in pages for kw in p["keywords"] if kw != default_kw]
    keyword_hits.sort(key=lambda item: len(item[0]), reverse=True)
    for kw, p in keyword_hits:
        if kw in text:
            print(f"[매칭] 댓글='{text}' → 키워드 '{kw}' 매칭")
            return p

    # 2) 구체 키워드가 없을 때 기본 키워드(예: "신청") → 허브 페이지
    if default_kw and default_kw in text:
        hub = find_hub(pages)
        if hub:
            print(f"[매칭] 댓글='{text}' → 허브 페이지 전송")
            return hub
        print(f"[경고] 허브 페이지('{config.NOTION_HUB_TITLE}')를 노션 DB에서 찾지 못함 → 전송 스킵")
        return None

    return None
