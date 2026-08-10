# -*- coding: utf-8 -*-
"""봇 자료 DB 확인용 스크립트 — 절대 아무것도 수정하지 않음(읽기 전용).

각 페이지의 이름 / 키워드 / public_url / url 값을 그대로 출력한다.
public_url 은 노션 페이지가 '웹에 게시(Publish)' 되어 있을 때만 값이 채워진다.
이 값이 비어 있으면(None) 해당 페이지가 아직 게시되지 않은 것이다.

사용 환경변수:
  NOTION_TOKEN        - 노션 통합(Integration) 토큰 (필수)
  NOTION_DATABASE_ID  - 봇 자료 DB의 ID (필수)
"""
import sys

import requests

import config

TIMEOUT = 30
NOTION_API = "https://api.notion.com/v1"


def _headers():
    return {
        "Authorization": f"Bearer {config.NOTION_TOKEN}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }


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


def fetch_all_pages():
    """DB의 모든 페이지를 페이지네이션 처리하며 가져온다."""
    pages = []
    url = f"{NOTION_API}/databases/{config.NOTION_DATABASE_ID}/query"
    body = {"page_size": 100}
    while True:
        r = requests.post(url, headers=_headers(), json=body, timeout=TIMEOUT)
        if not r.ok:
            raise RuntimeError(f"노션 DB 조회 실패: {r.status_code} {r.text[:300]}")
        data = r.json()
        pages.extend(data.get("results", []))
        if not data.get("has_more"):
            break
        body["start_cursor"] = data.get("next_cursor")
    return pages


def main():
    if not config.NOTION_TOKEN or not config.NOTION_DATABASE_ID:
        raise SystemExit(
            "[설정 오류] NOTION_TOKEN, NOTION_DATABASE_ID 환경변수가 필요합니다."
        )

    print("[확인] 봇 자료 DB 조회를 시작합니다 (읽기 전용, 수정 없음)")
    try:
        pages = fetch_all_pages()
    except Exception as e:
        print(f"[오류] DB 조회 실패: {e}")
        sys.exit(1)

    print(f"[확인] 총 {len(pages)}개 페이지 발견\n")

    for page in pages:
        title = _extract_title(page)
        keyword = _extract_keyword(page)
        public_url = page.get("public_url")
        url = page.get("url")

        print(title)
        print(f"  키워드: {keyword or '(없음)'}")
        print(f"  public_url: {public_url or '(비어 있음 — 게시되지 않음)'}")
        print(f"  url: {url}")
        print()

    print("[확인] 완료")


if __name__ == "__main__":
    main()
