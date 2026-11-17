# -*- coding: utf-8 -*-
"""노션 자료 자동 등록 — '프롬프트 5종' 자료를 써서 자료 나눔 DB에 페이지로 올린다.

소담봇의 글감 목록(auto_post.PROMPT_BANK) 가운데 아직 노션에 자료가 없는 주제를 하나 골라,
notion_drafts/ 의 기존 자료와 같은 짜임(쓰는 법 → 프롬프트 5개)으로 작성해 등록한다.
한 번 실행에 1개만 만든다(주 2회 실행).

주의: 노션 API로는 '웹에 게시'를 켤 수 없다. 자료 DB가 들어 있는 상위 페이지가 게시되어 있으면
새 페이지에도 공개 주소가 생기지만, 아니라면 노션에서 한 번 게시를 눌러야 댓글 봇이 이 자료를 보낸다
(공개 주소가 없는 자료는 봇이 원래 보내지 않는다).

DRY_RUN=yes 면 노션에 올리지 않고 previews/ 에 원고만 저장한다.
"""
import os
import re
import sys

import requests

import config
import notion_api

REPO = os.path.dirname(os.path.abspath(__file__))

PROMPT = """너는 '소담쌤'이다. 컴퓨터가 익숙하지 않은 40~60대에게 AI 활용법을 쉬운 말로 알려주는 다정한 강사다.
「{topic}」 을 도와주는 '복사해서 쓰는 프롬프트 5종' 자료를 마크다운으로 써라.

짜임(이 순서와 기호를 그대로 지켜라)
# {topic} 프롬프트 5종
(이 자료가 어떤 답답함을 풀어주는지 두 문장)

## 쓰는 법
1. 회색 상자 오른쪽 위 복사 버튼을 누르세요
2. 챗GPT 입력창에 붙여넣으세요
3. [대괄호] 부분만 내 내용으로 바꾸면 끝입니다

## 1. (첫 번째 프롬프트 이름)
**이럴 때 씁니다** — (한 문장)
```
(프롬프트 본문 5~9줄. 바꿀 곳은 [대괄호]. 마지막 줄은 "없는 내용은 지어내지 마.")
```
**더 좋게 쓰는 법** — (한 문장)

(## 2. ~ ## 5. 도 같은 짜임. 1번 결과를 2번에 넣는 식으로 순서대로 이어지게.)

규칙: 부드러운 존댓말, 쉬운 말. 가격·통계·날짜·기관 이름은 쓰지 마라. 마크다운 본문만 출력하라."""


def pick_topic(registered_keywords):
    """노션에 아직 자료가 없는 (주제, 키워드) 하나. 없으면 None."""
    import auto_post
    registered = {k.strip() for k in registered_keywords}
    for area in auto_post.PROMPT_BANK.values():
        for topic, keyword in area:
            if keyword not in registered:
                return topic, keyword
    return None


def check_material(md):
    """자료 원고의 문제를 찾는다. 없으면 빈 목록."""
    problems = []
    if md.count("```") < 10:
        problems.append("프롬프트 상자가 5개보다 적음")
    if len(re.findall(r"^## \d\.", md, flags=re.M)) < 5:
        problems.append("번호 붙은 소제목이 5개보다 적음")
    if not md.lstrip().startswith("# "):
        problems.append("제목 줄 없음")
    if re.search(r"\d[\d,]*\s*(?:만\s*원|원|%|퍼센트)", md):
        problems.append("가격·비율 표현이 들어 있음")
    return problems


def write_material(topic, llm=None):
    """자료 마크다운을 만든다. 두 번 안에 통과하지 못하면 None."""
    if llm is None:
        import auto_post
        llm = lambda p: auto_post._call_gemini(p, max_tokens=4000)
    extra = ""
    for attempt in (1, 2):
        try:
            md = llm(PROMPT.format(topic=topic) + extra)
        except Exception as e:
            print(f"[노션자료] {attempt}차 작성 실패: {e}")
            continue
        md = re.sub(r"^```(?:markdown|md)?\s*\n(?=# )", "", md.strip())
        if md.count("```") % 2 == 1:      # 바깥을 감싼 코드 울타리의 닫는 쪽만 남은 경우
            md = re.sub(r"\n```\s*$", "", md)
        problems = check_material(md)
        if not problems:
            return md
        print(f"[노션자료] {attempt}차 원고 반려: {problems}")
        extra = f"\n\n[다시 쓰기] 앞선 원고의 문제: {', '.join(problems)}. 짜임을 정확히 지켜라."
    return None


def main():
    dry = os.environ.get("DRY_RUN", "").strip().lower() in ("1", "y", "yes", "true")
    if not (config.NOTION_TOKEN and config.NOTION_DATABASE_ID and config.GEMINI_API_KEY):
        print("[노션자료] NOTION_TOKEN / NOTION_DATABASE_ID / GEMINI_API_KEY 가 모두 있어야 합니다.")
        return 1
    pages = notion_api.get_pages()
    picked = pick_topic([k for p in pages for k in p["keywords"]])
    if not picked:
        print("[노션자료] 모든 글감에 자료가 이미 있습니다.")
        return 0
    topic, keyword = picked
    print(f"[노션자료] 주제 선정: {topic} (키워드: {keyword})")
    md = write_material(topic)
    if not md:
        print("[노션자료] 기준을 통과한 원고가 없어 이번에는 올리지 않습니다.")
        return 0
    title = md.splitlines()[0].lstrip("# ").strip()
    if dry:
        os.makedirs(os.path.join(REPO, "previews"), exist_ok=True)
        with open(os.path.join(REPO, "previews", f"notion_{keyword}.md"), "w", encoding="utf-8") as f:
            f.write(md)
        print(f"[노션자료][미리보기] previews/notion_{keyword}.md 에 저장했습니다.")
        return 0
    page = notion_api.create_page(title, keyword, md)
    print(f"[노션자료] 등록 완료: {title} → {page.get('url')}")
    if page.get("public_url"):
        print(f"[노션자료] 공개 주소: {page['public_url']}")
    else:
        print("[노션자료] 아직 공개 주소가 없습니다. 노션에서 이 페이지(또는 상위 페이지)의 '게시'를 켜야 댓글 봇이 보냅니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
