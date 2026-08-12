# -*- coding: utf-8 -*-
"""매일 자동 글 발행 봇 (v2.0).

GitHub Actions가 1순위 시간대(07~09시 KST) 동안 20분마다 실행하고,
그 시간대에 아직 오늘 발행 안 했다면 바로 1회 발행한다.
1순위 시간대를 놓치면(cron 누락 등) 2순위 시간대(18~20시 KST)에 발행한다.
하루 발행은 1회로 제한한다(날짜 기준 판단).

콘텐츠 파이프라인 (전부 무료):
  - 매 실행마다 정부·지자체 AI 지원 혜택 소재를 먼저 검사한다.
    신선도 점수 3점 이상 + 신청 방법 확인 + 환각 검증을 통과하면 그 날은 혜택 글을 발행한다.
  - 통과하는 혜택 소재가 없으면 순환 큐(POST_CYCLE)를 따라
    "프롬프트 나눔"(주력) 또는 "업무 활용법" 글을 발행한다.
  → Gemini 무료 API로 초안 작성 → Threads API 발행

프롬프트 나눔 글은 본문에 프롬프트 1개, 발행한 글에 댓글로 2개를 더 이어 붙인다.

품질 원칙:
  - 혜택 글은 기사에 실제로 적힌 사실만 언급 (숫자·기관명·전화번호 환각 방어 2단 적용)
  - 모든 글 마지막 줄에 담백한 팔로우 이유 한 줄을 넣어 전환율을 높인다
  - 부드러운 존댓말, 4050 독자 눈높이에 맞춘 쉬운 말
"""
import csv
import hashlib
import json
import os
import re
import time
import datetime
from zoneinfo import ZoneInfo
import requests
import feedparser

import config
import cta
import notion_api
import prompt_queue
import threads_api

KST = ZoneInfo("Asia/Seoul")
POSTED_FILE = os.path.join(os.path.dirname(__file__), "state", "posted.json")
POSTED_LOG_FILE = os.path.join(os.path.dirname(__file__), "data", "posted_log.csv")
POSTED_LOG_COLUMNS = [
    "date_kst", "post_type", "topic", "keyword", "cta_id", "queue_left", "thread_id", "is_draft",
]
TOPIC_DOC_FILE = os.path.join(os.path.dirname(__file__), "docs", "글감_발행목록.md")

# 발행 시간대 정의 (KST 기준)
PRIMARY_HOURS = (7, 8, 9)      # 07:00 ~ 09:59
BACKUP_HOURS = (18, 19, 20)    # 18:00 ~ 20:59

GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "{model}:generateContent?key={key}"
)

# 계정 페르소나 — 모든 글의 기준이 되는 정체성
PERSONA = """[계정 정체성 — 소담 AI 랩 (@sodam_ai_lab)]
- 컴퓨터와 스마트폰이 익숙하지 않은 40~50대가 주 대상
  (직장인, 소상공인, 부업 준비생, 집안일하며 배우고 싶은 사람)
- 이들이 "나도 할 수 있겠다"고 느끼게 만드는 것이 이 계정의 목적
- '소담하다'는 이름처럼: 따뜻하고, 담백하고, 과하지 않게
- 옆에서 차근차근 알려주는 다정한 선생님의 목소리
- 전문용어를 자랑하지 않고, 누구나 이해할 수 있는 쉬운 말로
- 항상 "그래서 내가 오늘 뭘 해보면 되는지"까지 알려주는 실용성
- 독자를 가르치려 들지 않고, 함께 해보자고 권하는 태도"""

# 7일 주기 순환 큐 — 요일에 고정하지 않아 발행이 누락돼도 순서만 밀리고 깨지지 않는다.
POST_CYCLE = ["prompt", "prompt", "howto", "prompt", "prompt", "howto", "prompt"]

INSTAGRAM_HANDLE = "sodam_ai_lab"

# 프롬프트/업무활용 글에서 키워드 댓글 퍼널이 없을 때 쓰는 마무리 문구 풀
# "이런 프롬프트 계속 올려요"는 매번 반복되어 뻔해 보이므로 제외한다.
GENERIC_FOLLOW_LINES = [
    "AI 어렵지 않게 풀어드리는 이야기, 계속 올릴게요",
    "이런 활용법 매일 정리해서 올려요",
    "다음에도 바로 써먹을 수 있는 걸로 가져올게요",
]

# 가끔 섞어 넣는 인스타 유도 문구 풀
INSTAGRAM_CTA_LINES = [
    f"카드로 더 보기 편하게 정리해서 인스타 {INSTAGRAM_HANDLE}에도 올려요",
    f"인스타 {INSTAGRAM_HANDLE}에 카드뉴스로 한눈에 정리해뒀어요",
    f"더 깔끔하게 보고 싶으시면 인스타 {INSTAGRAM_HANDLE}도 있어요",
]

# benefit 글은 기사 내용에 맞춰 Gemini가 팔로우 이유를 직접 쓰므로 이 문구는
# pick_closing_line()이 인스타 문구로 새지 않도록 막는 고정 기본값일 뿐이다.
BENEFIT_FOLLOW_LINE = "이런 지원 놓치지 않게 계속 찾아서 올려요"

# 정확한 키워드 매칭이 안 될 때, 신청 만능 키워드로 유도하는 문구 풀.
# comment_bot.py의 DEFAULT_KEYWORD("신청") 로직과 짝을 이룬다:
# 댓글에 "신청"이 있으면 노션 DB에서 가장 최근 등록된 자료로 자동 응답한다.
APPLY_CTA_LINES = [
    "댓글에 '신청'만 남겨주시면 요즘 나눔 중인 자료 보내드려요",
    "지금 나눠드리고 있는 자료 있어요, 댓글에 '신청' 남겨주세요",
    "요즘 나눔 중인 거 받고 싶으시면 댓글에 '신청'이라고 남겨주세요",
]

# 첫 줄 공식 — 어그로 없이 '정보의 힘'으로 스크롤을 멈추게 하는 패턴 (날마다 랜덤)
HOOK_PATTERNS = [
    '가장 놀라운 사실을 그대로 첫 줄에 (예: "AI로 찍어낸 영상, 이제 수익 내기 어려워진대요")',
    '변화의 결과를 먼저 (예: "이제 OO는 무료로 할 수 있게 됐어요")',
    '독자에게 직접 영향을 주는 지점부터 (예: "OO 하시는 분들, 이번 주부터 달라져요")',
    '공감가는 상황으로 바로 들어가기 (예: "어제저녁, ~하다가 진짜 답답했거든요")',
    '기존 상식이 바뀐 지점을 짚기 (예: "지금까지 OO였는데, 이제 반대가 됐어요")',
    '가장 실용적인 결론을 먼저 (예: "이것만 알아두면 OO는 안 하셔도 돼요")',
]

# ── 상태 관리 ────────────────────────────────────────────────

def load_posted():
    try:
        with open(POSTED_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    data.setdefault("posted", {})
    data.setdefault("used_titles", [])
    data.setdefault("cycle_index", 0)
    data.setdefault("prompt_seq", 0)
    data.setdefault("cta_seq", 0)
    data.setdefault("material_check", {})
    return data


def save_posted(state):
    os.makedirs(os.path.dirname(POSTED_FILE), exist_ok=True)
    state["used_titles"] = state["used_titles"][-100:]
    keys = sorted(state["posted"].keys())[-60:]
    state["posted"] = {k: state["posted"][k] for k in keys}
    with open(POSTED_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def append_posted_log(row):
    """발행할 때마다 data/posted_log.csv에 한 줄 추가한다 (효과 측정용).

    파일이 없으면 헤더와 함께 새로 만든다. open/lead가 아닌 유형(benefit/howto)은
    topic 정도만 채워지고 keyword/cta_id/queue_left는 빈 값으로 남는다.
    """
    os.makedirs(os.path.dirname(POSTED_LOG_FILE), exist_ok=True)
    file_exists = os.path.exists(POSTED_LOG_FILE)
    with open(POSTED_LOG_FILE, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=POSTED_LOG_COLUMNS)
        if not file_exists:
            writer.writeheader()
        writer.writerow({col: row.get(col, "") for col in POSTED_LOG_COLUMNS})


def _read_posted_log_rows():
    if not os.path.exists(POSTED_LOG_FILE):
        return []
    with open(POSTED_LOG_FILE, "r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def update_topic_doc(state, now_kst):
    """docs/글감_발행목록.md를 현재 OPEN/LEAD 큐 상태로 다시 써준다.

    노션 DB 조회에 실패해도 발행 자체는 막지 않도록 예외를 삼키고 건너뛴다.
    """
    try:
        pages = notion_api.get_pages()
    except Exception as e:
        print(f"[글감목록] 노션 DB 조회 실패({e}) → 문서 갱신 건너뜀")
        return

    registered_keywords = set()
    lead_keywords = set()
    for p in pages:
        registered_keywords.update(p.get("keywords", []))
        if p.get("public_url"):
            lead_keywords.update(p.get("keywords", []))

    published_keywords = set(state.get("published_keywords", []))
    published_log = [
        row for row in _read_posted_log_rows()
        if row.get("post_type") in ("open", "lead") and row.get("keyword")
    ]

    md = prompt_queue.render_topic_doc(
        PROMPT_BANK, AREAS, lead_keywords, registered_keywords, published_keywords,
        generated_at=now_kst.strftime("%Y-%m-%d %H:%M KST"),
        published_log=published_log,
    )

    os.makedirs(os.path.dirname(TOPIC_DOC_FILE), exist_ok=True)
    with open(TOPIC_DOC_FILE, "w", encoding="utf-8") as f:
        f.write(md)
    print("[글감목록] docs/글감_발행목록.md 갱신 완료")


# ── 마무리 문구 선택 ─────────────────────────────────────────

def _get_has_material(state, date_str):
    """봇 자료 DB에 자료가 있는지 하루 단위로 캐시해서 확인한다."""
    cache = state.get("material_check") or {}
    if cache.get("date") == date_str:
        return cache["has_material"]
    has_material = notion_api.has_any_material()
    state["material_check"] = {"date": date_str, "has_material": has_material}
    return has_material


def pick_closing_line(post_type, has_keyword, keyword, cta_seq, has_material):
    """마무리 문구를 결정한다. 우선순위:
    1) 정확 키워드 매칭 댓글 퍼널 (최우선)
    2) benefit 고정 문구
    3) 신청 만능 유도 / 인스타 유도 / 일반 문구 — cta_seq % 3으로 3분의 1씩 순환
       (신청 유도는 자료 DB가 비어있으면 일반 문구로 대체)"""
    if post_type == "prompt" and has_keyword:
        line_type = "정확매칭"
        line = f'댓글에 "{keyword}" 남겨주시면 5개 더 보내드릴게요'
    elif post_type == "benefit":
        line_type = "혜택"
        line = BENEFIT_FOLLOW_LINE
    else:
        choice = cta_seq % 3
        if choice == 0 and has_material:
            line_type = "신청유도"
            line = APPLY_CTA_LINES[(cta_seq // 3) % len(APPLY_CTA_LINES)]
        elif choice == 1:
            line_type = "인스타유도"
            line = INSTAGRAM_CTA_LINES[(cta_seq // 3) % len(INSTAGRAM_CTA_LINES)]
        else:
            line_type = "일반문구"
            line = GENERIC_FOLLOW_LINES[(cta_seq // 3) % len(GENERIC_FOLLOW_LINES)]

    print(f"[마무리 문구 선택] 유형={line_type} (cta_seq={cta_seq}, 자료 존재={has_material})")
    return line


# ── 시간 판단 ────────────────────────────────────────────────

def current_window(now_kst):
    """지금이 어느 발행 시간대인지 반환 (아니면 None)."""
    h = now_kst.hour
    if h in PRIMARY_HOURS:
        return "primary"
    if h in BACKUP_HOURS:
        return "backup"
    return None


def should_post_now(now_kst, state):
    """오늘 이미 발행했으면 False — 날짜만으로 판단(1순위/2순위 구분 없이 하루 1회)."""
    date_str = now_kst.strftime("%Y-%m-%d")
    if date_str in state["posted"]:
        return False, date_str, "오늘 이미 발행함"
    return True, date_str, "발행 시간대 진입 — 즉시 발행"


# ── 뉴스 수집 (구글 뉴스 RSS, 무료·키 불필요) ────────────────

def _strip_html(text):
    return re.sub(r"<[^>]+>", " ", text or "").strip()


def fetch_news(query, limit=6):
    """구글 뉴스 RSS에서 (제목 + 기사 요약) 목록을 가져온다. 30시간 이내 기사만."""
    url = (
        "https://news.google.com/rss/search?q="
        + requests.utils.quote(query)
        + "&hl=ko&gl=KR&ceid=KR:ko"
    )
    feed = feedparser.parse(url)
    items = []
    now = datetime.datetime.now(datetime.timezone.utc)
    for e in feed.entries[: limit * 3]:
        title = re.sub(r"\s*-\s*[^-]+$", "", e.get("title", "")).strip()  # 끝의 언론사명 제거
        published = e.get("published_parsed")
        pub_ts = None
        if published:
            pub_dt = datetime.datetime(*published[:6], tzinfo=datetime.timezone.utc)
            pub_ts = pub_dt.timestamp()
            if (now - pub_dt).total_seconds() > 60 * 60 * 30:  # 30시간 이내만
                continue
        if title:
            summary = _strip_html(e.get("summary", ""))[:200]
            items.append({"title": title, "summary": summary, "published_ts": pub_ts})
        if len(items) >= limit:
            break
    return items


def _pick_queries(date_str, tag, pool, n):
    """날짜+태그 해시로 오늘 사용할 검색어 조합을 고정 선택 (매일 다르게, 재시도해도 동일)."""
    seed = int(hashlib.sha256(f"{date_str}-{tag}-queries".encode()).hexdigest(), 16)
    pool = list(pool)
    picked = []
    for i in range(min(n, len(pool))):
        idx = (seed >> (i * 8)) % len(pool)
        picked.append(pool.pop(idx))
    return picked


# ── 줄바꿈 후처리 (AI가 지시를 안 지켜도 코드로 보정) ────────

BREAK_MARKS = ("。", ".", "!", "?", ",", "~")


def wrap_lines(text, limit=20):
    """한 줄이 limit자를 넘으면 어절(띄어쓰기) 단위로 자연스럽게 끊어 준다."""
    out_lines = []
    for raw in text.split("\n"):
        line = raw.strip()
        if not line:
            out_lines.append("")
            continue

        words = line.split()
        cur = ""
        for w in words:
            candidate = (cur + " " + w).strip()
            if cur and len(candidate) > limit:
                out_lines.append(cur)
                cur = w
            else:
                cur = candidate
                if cur.endswith(BREAK_MARKS) and len(cur) >= limit * 0.6:
                    out_lines.append(cur)
                    cur = ""
        if cur:
            out_lines.append(cur)

    merged = []
    for l in out_lines:
        if (l and len(l) <= 3 and merged and merged[-1]
                and len(merged[-1]) + len(l) + 1 <= limit + 4):
            merged[-1] = merged[-1] + " " + l
        else:
            merged.append(l)

    result = []
    for l in merged:
        if l == "" and result and result[-1] == "":
            continue
        result.append(l)
    return "\n".join(result).strip()


def trim_paragraphs(text, max_blocks=4):
    """단락이 너무 많으면 핵심만 남긴다."""
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
    if len(blocks) <= max_blocks:
        return "\n\n".join(blocks)
    head = blocks[:2]
    tail = blocks[-(max_blocks - 2):]
    return "\n\n".join(head + tail)


# ── Gemini 호출 (무료 등급) ───────────────────────────────────

def _call_gemini(prompt):
    url = GEMINI_URL.format(model=config.GEMINI_MODEL, key=config.GEMINI_API_KEY)
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 420},
    }
    r = requests.post(url, json=body, timeout=60)
    if not r.ok:
        raise RuntimeError(
            f"Gemini 호출 실패: {r.status_code} {r.text[:300]}\n"
            f"[점검] 404면 GEMINI_MODEL 값이 현재 유효한 모델명인지, "
            f"429면 무료 한도 초과가 아닌지 확인하세요. (현재 모델: {config.GEMINI_MODEL})"
        )
    data = r.json()
    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except (KeyError, IndexError):
        raise RuntimeError(f"Gemini 응답 형식 오류: {str(data)[:300]}")
    return text.strip().strip('"').strip()


def _finalize(text, max_blocks):
    text = trim_paragraphs(text, max_blocks=max_blocks)
    text = wrap_lines(text, limit=20)
    if len(text) > 495:
        text = text[:492] + "…"
    return text


# ── 혜택 정보 ('benefit') ──────────────────────────────────────

BENEFIT_QUERIES = [
    "과기정통부 AI 지원 when:2d",
    "정부 AI 무료 지원 when:2d",
    "AI 바우처 신청 when:2d",
    "지자체 AI 교육 무료 when:3d",
    "소상공인 AI 지원사업 when:3d",
    "AI 교육 수강생 모집 when:3d",
    "국민 AI 무료 제공 when:2d",
    "AI 크레딧 지원 when:2d",
]

_FRESH_NEW_WORDS = ("출범", "신설", "새로", "처음", "개시", "오픈", "시작", "확대", "개편")
_FRESH_SCALE_PATTERN = re.compile(r"\d+\s*(?:만\s*원|원|명|개소|건)")
_FRESH_URGENCY_WORDS = ("선착순", "마감", "한정", "조기", "소진")
_FRESH_APPLY_WORDS = ("신청", "접수", "모집", "참여", "이용")
_FRESH_EXCLUDE_WORDS = ("기업 대상", "컨소시엄", "협약", "입찰", "공모전 참가기업")


def freshness_score(item):
    """혜택 소재의 신선도를 0~4점으로 채점."""
    text = f"{item.get('title', '')} {item.get('summary', '')}"
    score = 0
    if any(w in text for w in _FRESH_NEW_WORDS):
        score += 1
    if _FRESH_SCALE_PATTERN.search(text):
        score += 1
    if any(w in text for w in _FRESH_URGENCY_WORDS):
        score += 1
    if any(w in text for w in _FRESH_APPLY_WORDS) and not any(w in text for w in _FRESH_EXCLUDE_WORDS):
        score += 1
    return score


# 신청 방법(전화번호/사이트/기관명/접수처) 실마리가 기사에 있는지 확인
_APPLICATION_HINT_PATTERN = re.compile(
    r"\d{2,4}-\d{3,4}-\d{4}"
    r"|\d{4}-\d{4}"
    r"|https?://\S+"
    r"|\w+\.(?:go\.kr|or\.kr|co\.kr|com|kr)"
    r"|접수처|홈페이지|누리집"
    r"|\w+(?:부|청|처|원|공단|진흥원|센터|시청|도청|군청)"
)


def has_application_hint(source_text):
    return bool(_APPLICATION_HINT_PATTERN.search(source_text))


def pick_benefit_item(date_str):
    """신선도 3점 이상 + 신청 방법 정보가 있는 혜택 소재 1건을 고른다. 없으면 None."""
    queries = _pick_queries(date_str, "benefit", BENEFIT_QUERIES, 4)
    items = []
    for q in queries:
        items += fetch_news(q, 4)

    seen = set()
    dedup = []
    for it in items:
        if it["title"] in seen:
            continue
        seen.add(it["title"])
        dedup.append(it)

    scored = [(freshness_score(it), it) for it in dedup]
    scored = [(s, it) for s, it in scored if s >= 3]
    if not scored:
        print(f"[혜택] 신선도 3점 이상 소재 없음(검토 {len(dedup)}건) → 큐로 진행")
        return None

    scored.sort(key=lambda pair: (-pair[0], -(pair[1].get("published_ts") or 0)))
    best_score, best_item = scored[0]
    source_text = f"{best_item['title']} {best_item.get('summary', '')}"
    if not has_application_hint(source_text):
        print(f"[혜택] 신청 방법 정보 없음 — 「{best_item['title']}」 → 큐로 진행")
        return None

    print(f"[혜택] 소재 선정: 「{best_item['title']}」 (신선도 {best_score}점)")
    return best_item


def _gen_benefit_draft(item, extra_prompt=""):
    prompt = f"""당신은 스레드(Threads) 계정 '소담 AI 랩'의 운영자 '소담쌤'입니다.

{PERSONA}

아래는 방금 나온 정부·지자체 AI 지원 관련 기사입니다. 이 기사에 실제로 적힌 내용만 사용해
스레드 게시글 1개를 한국어로 작성하세요. 기사에 없는 숫자·기관명·전화번호·마감일은
절대 지어내지 않습니다.

[기사]
제목: {item['title']}
요약: {item.get('summary', '')}

[오늘의 코너: 혜택 정보]
"놓치면 아까운 정부·지자체 AI 지원 소식을 가장 먼저 전하는 코너"입니다.

[글의 구조 — 반드시 이 순서로]
1) 질문형 도입 한 줄 — 독자 상황을 건드린다 (예: "AI 배우러 학원 알아보셨어요?")
2) 반전 한 줄 — 핵심 혜택을 짧게 (예: "동네에서 공짜로 합니다.")
3) 정체 — 어느 기관의 무슨 사업인지
4) 규모와 내용 — 기사에 있는 숫자만
5) 신청 방법 — 기사에 있는 전화번호·사이트명·문의처를 그대로
6) 팔로우 이유 한 줄 — 예: "이런 지원 놓치지 않게 계속 찾아서 올려요"

[사실성 규칙 — 가장 중요, 절대 위반 금지]
- 기사 제목과 요약에 실제로 적힌 숫자, 금액, 인원, 기관명, 전화번호, 마감일만 옮겨 적습니다
- 기사에 없는 수치나 기관명, 연락처를 추측하거나 지어내지 않습니다
- 신청 방법 정보가 기사에 부분적으로만 있으면 있는 정보(기관명 등)까지만 안내합니다

[어조 규칙]
- 부드럽고 친근한 존댓말, 명령·경고·과장·어그로 금지
- 도입부의 질문 1문장을 제외하면 질문으로 끝내지 않습니다

[분량 규칙 — 반드시 지킬 것]
- 전체 250~400자 (공백 포함). 신청 방법까지 있는 완결된 글이어야 합니다
- 단락은 최대 6개

[가독성 규칙]
- 한 줄은 20자 이내로 끊어서 씁니다
- 단락 사이에는 빈 줄을 넣습니다
- 이모지는 딱 1개만
- 해시태그·링크·인사말 금지
- 게시글 본문만 출력 (설명·따옴표 없이)
{extra_prompt}"""
    text = _call_gemini(prompt)
    return _finalize(text, max_blocks=6)


def write_benefit_post(item, now_kst):
    """혜택 글 초안을 생성하고 환각 검증을 2단으로 통과시킨다. 실패하면 None."""
    source_text = f"{item['title']} {item.get('summary', '')}"

    text = _gen_benefit_draft(item)
    problems = find_fabrications(text, source_text)
    if not problems:
        print("[혜택 검증] 1차 통과")
        return text
    print(f"[혜택 검증] 1차 실패 — 문제: {problems}")

    warn = f"""

[경고 — 직전 시도에서 발견된 문제]
아래 표현은 기사에 근거가 없습니다. 절대 다시 쓰지 마세요: {problems}
숫자, 금액, 인원, 전화번호, 기관명, 마감일은 기사에 글자 그대로 적힌 것만 옮겨 적으세요.
기사에 없으면 아예 언급하지 마세요."""
    text2 = _gen_benefit_draft(item, extra_prompt=warn)
    problems2 = find_fabrications(text2, source_text)
    if not problems2:
        print("[혜택 검증] 2차 통과")
        return text2

    print(f"[혜택 검증] 2차도 실패 — 문제: {problems2} → 혜택 글 포기, 큐로 진행")
    return None


# ── 환각 방어 (혜택 글 전용) ───────────────────────────────────

RISKY_WORDS = ("선착순", "마감 임박", "조기 마감", "한정", "오늘까지", "내일까지", "무상", "전액")

CLAIM_PATTERN = re.compile(
    r"\d[\d,]*\s*(?:만\s*원|원|명|개소|개월|건|퍼센트|%|배)"
    r"|\d+\s*월\s*\d+\s*일"
    r"|\d+\s*일\s*(?:까지|간)"
    r"|\d{2,4}-\d{3,4}-\d{4}"      # 전화번호
    r"|\d{4}-\d{4}"                # 대표번호
)

_ORG_PATTERN = re.compile(r"\w+(?:부|청·처|원|공단|진흥원|센터|시청|도청|군청)")


def _normalize_claim(s):
    return re.sub(r"[\s,]", "", s)


def find_fabrications(text, source_text):
    """기사에 근거 없는 사실 주장을 찾아 목록으로 반환."""
    problems = []
    norm_source = _normalize_claim(source_text)

    for m in CLAIM_PATTERN.finditer(text):
        claim = m.group()
        if _normalize_claim(claim) not in norm_source:
            problems.append(claim)

    for w in RISKY_WORDS:
        if w in text and w not in source_text:
            problems.append(w)

    for m in _ORG_PATTERN.finditer(text):
        org = m.group()
        if org not in source_text:
            problems.append(org)

    return problems


# ── 프롬프트 나눔 ('prompt') — 봇의 주력 ─────────────────────

PROMPT_BANK = {
    "office": [  # 직장인 업무
        ("회의록 정리하기", "회의"),
        ("받은 메일에 답장 쓰기", "메일"),
        ("긴 보고서 핵심만 뽑기", "요약"),
        ("주간 업무보고 쓰기", "주보"),
        ("엑셀 수식 만들기", "엑셀"),
        ("발표자료 목차 잡기", "발표"),
        ("자료 비교표 만들기", "비교"),
        ("거절 메일 정중하게 쓰기", "거절"),
        ("일정 조율 메일 쓰기", "일정"),
        ("회의 안건 정리하기", "안건"),
        ("업무 인수인계서 쓰기", "인수"),
        ("사과 메일 쓰기", "사과"),
        ("메모를 표로 정리하기", "표"),
        ("외국어 메일 번역하고 답장하기", "번역"),
        ("면접 예상질문 뽑기", "면접"),
        ("자기소개서 다듬기", "자소서"),
        ("업무 매뉴얼 초안 쓰기", "매뉴얼"),
        ("협업 요청 메시지 쓰기", "협업"),
        ("긴 문서에서 필요한 부분만 찾기", "검색"),
        ("하루 할 일 우선순위 정하기", "할일"),
    ],
    "biz": [  # 자영업·부업
        ("상세페이지 문구 쓰기", "상세"),
        ("상품 소개글 쓰기", "소개"),
        ("고객 리뷰에 답글 달기", "리뷰"),
        ("클레임 응대 문구 만들기", "클레임"),
        ("SNS 홍보 문구 쓰기", "홍보"),
        ("이벤트 안내문 쓰기", "이벤트"),
        ("메뉴 설명 문구 쓰기", "메뉴"),
        ("가격 인상 안내문 쓰기", "인상"),
        ("단골 감사 메시지 쓰기", "단골"),
        ("예약 안내 문구 만들기", "예약"),
        ("배달앱 사장님 댓글 쓰기", "배달"),
        ("전단지 문구 쓰기", "전단"),
        ("블로그 포스팅 초안 쓰기", "블로그"),
        ("경쟁사와 비교해 정리하기", "경쟁"),
        ("매출 메모 정리하기", "매출"),
        ("직원 공지문 쓰기", "공지"),
        ("사업계획서 개요 잡기", "사업"),
        ("지원사업 신청서 초안 쓰기", "신청"),
        ("간판·명함 문구 만들기", "간판"),
        ("휴무 안내문 쓰기", "휴무"),
    ],
    "life": [  # 생활·집안일
        ("일주일 식단 짜기", "식단"),
        ("장보기 목록 만들기", "장보기"),
        ("냉장고 재료로 요리 정하기", "냉장고"),
        ("아이 숙제 봐주기", "숙제"),
        ("아이 독서록 도와주기", "독서록"),
        ("여행 일정 짜기", "여행"),
        ("어려운 공문서 쉬운 말로 풀기", "공문"),
        ("계약서에서 확인할 점 찾기", "계약"),
        ("병원 가기 전 질문 정리하기", "병원"),
        ("가계부 항목 정리하기", "가계부"),
        ("집안일 계획 세우기", "집안일"),
        ("선물 고르기", "선물"),
        ("경조사 문구 쓰기", "경조사"),
        ("이사 준비 체크리스트 만들기", "이사"),
        ("보험 약관 이해하기", "보험"),
        ("자녀와 대화 주제 찾기", "대화"),
        ("산책·스트레칭 습관 계획 세우기", "습관"),
        ("반려동물 돌봄 메모 정리하기", "반려"),
        ("부모님께 쉽게 설명하기", "설명"),
        ("중고거래 판매글 쓰기", "중고"),
    ],
}
AREAS = ["office", "biz", "life"]

# 요일별 아침 발행 타입 — 월요일=0 ... 일요일=6 (파이썬 datetime.weekday() 기준)
# OPEN(주 4일: 월화목금) = 프롬프트 전문 공개, LEAD(주 3일: 수토일) = 맛보기+신청 유도
# 소담쌤이 나중에 요일 배분을 바꾸고 싶으면 이 딕셔너리만 고치면 된다.
WEEKDAY_TYPE_MAP = {
    0: "open",   # 월
    1: "open",   # 화
    2: "lead",   # 수
    3: "open",   # 목
    4: "open",   # 금
    5: "lead",   # 토
    6: "lead",   # 일
}
_WEEKDAY_NAMES_KO = ["월", "화", "수", "목", "금", "토", "일"]


def determine_prompt_type(now_kst):
    """오늘 아침 발행이 OPEN인지 LEAD인지 정한다.

    POST_TYPE 환경변수가 open/lead로 지정돼 있으면 요일과 무관하게 그 값을 강제로 쓴다
    (테스트용). 지정이 없으면 KST 기준 오늘 요일로 WEEKDAY_TYPE_MAP을 따른다.
    """
    forced = os.environ.get("POST_TYPE", "").strip().lower()
    if forced in ("open", "lead"):
        print(f"[타입 강제 지정] POST_TYPE={forced}")
        return forced
    weekday = now_kst.weekday()
    post_type = WEEKDAY_TYPE_MAP[weekday]
    print(f"[타입 결정] {_WEEKDAY_NAMES_KO[weekday]}요일 → {post_type.upper()} 타입")
    return post_type


_PROMPT_QUALITY_RULES = """[프롬프트 품질 규칙 — 반드시 지킬 것]
- 각 프롬프트는 6줄 이내, 그대로 복사해 쓸 수 있는 완성형 문장으로 작성
- 사용자가 바꿔 넣을 자리는 반드시 [여기에 회의 내용 붙여넣기]처럼 대괄호로 표시
- 출력 형식을 반드시 지정 (표로/3줄로/번호를 붙여서/글자 수 제한/말투 등). 빠지면 결과가 매번 달라집니다
- "잘 써줘", "좋게 만들어줘" 같은 막연한 지시 금지
- 전문용어 금지. 컴퓨터를 잘 모르는 사람도 읽을 수 있는 말로
- 각 프롬프트 마지막 줄에 "없는 내용은 지어내지 마" 류의 제약을 넣을 것"""


def _split_markers(raw, markers):
    """raw 텍스트를 [마커] 단위로 분리."""
    pattern = re.compile(r"\[(" + "|".join(re.escape(m) for m in markers) + r")\]")
    parts = {m: "" for m in markers}
    matches = list(pattern.finditer(raw))
    for i, m in enumerate(matches):
        key = m.group(1)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(raw)
        parts[key] = raw[start:end].strip()
    return parts


def _gen_prompt_bundle(topic, closing_line):
    prompt = f"""당신은 스레드(Threads) 계정 '소담 AI 랩'의 운영자 '소담쌤'입니다.

{PERSONA}

오늘의 프롬프트 나눔 주제: "{topic}"

이 주제를 시간 순서로 쪼개 실전 프롬프트 3개를 만드세요.
(예: 재료 정리 → 본작업 → 다듬기·확장·점검처럼, 같은 작업을 단계별로 쪼갠 것입니다.
서로 다른 5개를 모으는 것이 아니라, 하나의 상황을 시간순으로 쪼갭니다.)

{_PROMPT_QUALITY_RULES}

[게시글 본문 — [본문] 마커 뒤에 이 구조로 작성]
1줄) 상황 — 독자가 겪는 불편함 한 줄
2줄) 해결 — 이 프롬프트로 뭐가 되는지
3~4줄) 프롬프트 1번 본문 — 위 품질 규칙을 지킨 완성형 프롬프트
1줄) 마무리 — 정확히 이 문장을 그대로 쓰세요: "{closing_line}"

[분량 규칙]
- [본문] 전체 200~300자 (공백 포함)
- 질문으로 끝내지 않습니다
- 첫 줄은 25자 이내, 배경 설명으로 시작하지 않습니다
- 부드러운 존댓말, 명령·경고·과장 금지
- 이모지는 [본문]에 딱 1개만
- 해시태그·링크·인사말 금지

[출력 형식 — 반드시 아래 마커를 정확히 그대로 사용]
[본문]
(위 구조를 따른 게시글 본문)
[댓글2]
(2번 프롬프트 — 완성형, 대괄호 자리표시, 출력형식 지정, 6줄 이내)
[댓글3]
(3번 프롬프트 — 완성형, 대괄호 자리표시, 출력형식 지정, 6줄 이내)

마커 외의 다른 설명·따옴표는 출력하지 마세요."""
    return _call_gemini(prompt)


# v2.3부터 아침 발행은 write_queued_prompt_post()(OPEN/LEAD 큐)로 대체됨.
# main()에서는 더 이상 호출하지 않지만, 롤백 대비로 함수와 테스트를 그대로 남겨둔다.
def write_prompt_post(state, now_kst):
    """프롬프트 나눔 글을 작성한다. (본문, 댓글로 이어붙일 프롬프트 목록, 키워드 매칭 여부) 반환."""
    seq = state.get("prompt_seq", 0)
    area = AREAS[seq % 3]
    idx = (seq // 3) % 20
    topic, keyword = PROMPT_BANK[area][idx]

    has_kw = notion_api.has_keyword(keyword)

    date_str = now_kst.strftime("%Y-%m-%d")
    has_material = _get_has_material(state, date_str)
    cta_seq = state.get("cta_seq", 0)
    closing_line = pick_closing_line("prompt", has_kw, keyword, cta_seq, has_material)
    print(f"[마무리 문구] {closing_line}")

    raw = _gen_prompt_bundle(topic, closing_line)
    parts = _split_markers(raw, ("본문", "댓글2", "댓글3"))

    body = parts["본문"] or raw
    text = _finalize(body, max_blocks=4)

    extra_comments = []
    for k in ("댓글2", "댓글3"):
        c = parts.get(k, "").strip()
        if c:
            extra_comments.append(_finalize(c, max_blocks=2))

    print(f"[프롬프트] 영역={area} 주제={topic} 키워드매칭={has_kw}")
    return text, extra_comments, has_kw


# ── 아침 발행: OPEN/LEAD 글감 큐 ('open' / 'lead') ─────────────

def _gen_open_post(topic, closing_line):
    """OPEN 글(프롬프트 전문 공개)의 재료(후킹/프롬프트 전문/예시)를 Gemini로 생성한다."""
    prompt = f"""당신은 스레드(Threads) 계정 '소담 AI 랩'의 운영자 '소담쌤'입니다.

{PERSONA}

오늘의 프롬프트 나눔 주제: "{topic}"

{_PROMPT_QUALITY_RULES}

[출력 형식 — 반드시 아래 마커를 정확히 그대로 사용]
[후킹]
(이 프롬프트가 필요한 문제 상황을 짚는 한 줄. 25자 이내, 배경 설명 없이 바로)
[프롬프트]
(위 품질 규칙을 지킨, 그대로 복사해 쓸 수 있는 완성형 프롬프트 본문)
[예시]
(이 프롬프트를 쓰면 실제로 어떤 결과가 나오는지 2~3줄 예시. 과장 없이 담백하게)

[어조 규칙]
- 부드러운 존댓말, 명령·경고·과장 금지
- 질문으로 끝내지 않습니다

마커 외의 다른 설명·따옴표는 출력하지 마세요."""
    raw = _call_gemini(prompt)
    parts = _split_markers(raw, ("후킹", "프롬프트", "예시"))

    hook = parts["후킹"].strip() or topic
    prompt_body = parts["프롬프트"].strip() or raw
    example = parts["예시"].strip() or "(예시 생략)"

    text = (
        f"{hook}\n\n"
        "이거 그대로 복사해서 쓰세요 👇\n\n"
        "━━━━━━━━━━\n"
        f"{prompt_body}\n"
        "━━━━━━━━━━\n\n"
        "이렇게 나와요:\n"
        f"{example}\n\n"
        f"{closing_line}"
    )
    return _finalize(text, max_blocks=6)


def _gen_lead_post(topic, closing_line):
    """LEAD 글(맛보기+신청 유도)의 재료(후킹/필요한 이유/맛보기)를 Gemini로 생성한다."""
    prompt = f"""당신은 스레드(Threads) 계정 '소담 AI 랩'의 운영자 '소담쌤'입니다.

{PERSONA}

오늘의 프롬프트 나눔 주제: "{topic}"

이 주제로 "맛보기" 글을 씁니다. 전체 프롬프트를 다 공개하지 않고, 필요성과
핵심 일부만 보여줘서 "전체본을 받고 싶다"는 마음이 들게 씁니다.

[출력 형식 — 반드시 아래 마커를 정확히 그대로 사용]
[후킹]
(독자 상황을 짚는 한 줄. 25자 이내)
[이유]
(이 프롬프트/자료가 왜 필요한지 2~3줄)
[맛보기]
(핵심 내용의 아주 일부만 3줄. 전문을 주지 않고 일부러 궁금하게 남깁니다)

[어조 규칙]
- 부드러운 존댓말, 명령·경고·과장 금지
- 질문으로 끝내지 않습니다
- 전문용어 금지, 컴퓨터를 잘 모르는 사람도 읽을 수 있는 말로

마커 외의 다른 설명·따옴표는 출력하지 마세요."""
    raw = _call_gemini(prompt)
    parts = _split_markers(raw, ("후킹", "이유", "맛보기"))

    hook = parts["후킹"].strip() or topic
    reason = parts["이유"].strip()
    teaser = parts["맛보기"].strip()

    text = f"{hook}\n\n{reason}\n\n{teaser}\n\n{closing_line}"
    return _finalize(text, max_blocks=6)


def write_queued_prompt_post(state, now_kst, forced_type=None):
    """OPEN/LEAD 큐에서 오늘 쓸 글감을 뽑아 글을 작성한다.

    LEAD 요일인데 LEAD 큐가 비어 있으면 OPEN으로 자동 대체하고 경고 로그를 남긴다.
    반환: {"text", "post_type", "topic", "keyword", "cta_idx", "queue_left"}
    """
    prompt_queue.migrate_legacy_published_keywords(state, PROMPT_BANK, AREAS)

    post_type = forced_type or determine_prompt_type(now_kst)

    lead_keywords = notion_api.get_lead_ready_keywords()
    published = set(state.get("published_keywords", []))
    queues = prompt_queue.build_queues(PROMPT_BANK, AREAS, lead_keywords, published)

    if post_type == "lead" and not queues["lead_remaining"]:
        print("[경고] LEAD 큐가 비어 OPEN으로 대체 발행합니다. Notion 자료 추가 필요")
        post_type = "open"

    seq_key = f"{post_type}_seq"
    seq = state.get(seq_key, 0)

    if post_type == "lead":
        item, left, _wrapped = prompt_queue.pick_from_queue(
            queues["lead_all"], queues["lead_remaining"], seq, "LEAD")
    else:
        item, left, _wrapped = prompt_queue.pick_from_queue(
            queues["open_all"], queues["open_remaining"], seq, "OPEN")

    if item is None:
        raise RuntimeError(f"[{post_type.upper()} 큐] 글감이 하나도 없습니다. PROMPT_BANK 구성을 확인하세요")

    if post_type == "lead" and left <= 3:
        print(f"[알림] LEAD 큐 잔여 {left}개. Notion 자료를 보충하세요")

    weekday_name = _WEEKDAY_NAMES_KO[now_kst.weekday()]
    print(f"[아침발행] {weekday_name}요일 → {post_type.upper()} 타입 / "
          f"글감: {item['topic']} / {post_type.upper()} 큐 잔여 {left}개")

    closing_line, cta_idx = cta.pick_cta(post_type, keyword=item["keyword"])

    if post_type == "open":
        text = _gen_open_post(item["topic"], closing_line)
    else:
        text = _gen_lead_post(item["topic"], closing_line)

    state[seq_key] = seq + 1
    published.add(item["keyword"])
    state["published_keywords"] = sorted(published)

    return {
        "text": text,
        "post_type": post_type,
        "topic": item["topic"],
        "keyword": item["keyword"],
        "cta_idx": cta_idx,
        "queue_left": left,
    }


# ── 업무 활용법 ('howto') ─────────────────────────────────────

def write_howto_post(state, now_kst):
    date_str = now_kst.strftime("%Y-%m-%d")
    seed = int(hashlib.sha256(f"{date_str}-howto".encode()).hexdigest(), 16)
    all_topics = [t for area in AREAS for t in PROMPT_BANK[area]]
    topic, _keyword = all_topics[seed % len(all_topics)]

    hook_seed = int(hashlib.sha256(f"{date_str}-howto-hook".encode()).hexdigest(), 16)
    hook = HOOK_PATTERNS[hook_seed % len(HOOK_PATTERNS)]

    has_material = _get_has_material(state, date_str)
    cta_seq = state.get("cta_seq", 0)
    closing_line = pick_closing_line("howto", False, None, cta_seq, has_material)
    print(f"[마무리 문구] {closing_line}")

    prompt = f"""당신은 스레드(Threads) 계정 '소담 AI 랩'의 운영자 '소담쌤'입니다.

{PERSONA}

오늘의 업무 활용법 주제: "{topic}"

[글의 구조 — 반드시 이 4단으로]
1단락) 이런 상황 있으시죠 — 공감 가는 장면
2단락) 이렇게 해보세요 — AI로 해결하는 방법. 어느 서비스에 뭐라고 입력하는지까지 구체적으로
3단락) 이렇게 하면 — 얻는 결과나 절약되는 시간
4줄째) 마지막 줄은 정확히 이 문장으로 마무리하세요: "{closing_line}"
  "팔로우 해주세요" 같은 직접적인 요청은 쓰지 않습니다

[첫 줄 — 가장 중요]
첫 줄은 이 패턴을 참고해서: {hook}
배경 설명으로 시작하지 말고, 25자 이내로 짧게.

[사실성 규칙]
- 지어낸 통계·수치·사례를 사실인 것처럼 쓰지 않습니다
- 확신할 수 없는 부분은 일반적인 조언 톤으로 씁니다

[어조 규칙]
- 부드럽고 친근한 존댓말, 옆에서 알려주는 느낌
- 명령, 경고, 단정, 과장, 어그로 표현 금지
- 질문으로 끝내지 않습니다

[분량 규칙 — 반드시 지킬 것]
- 전체 200~280자 (공백 포함)
- **단락은 정확히 4개.**
- 각 단락은 짧게: 2문장 이내

[가독성 규칙]
- 한 줄은 20자 이내로 끊어서 씁니다
- 단락 사이에는 빈 줄을 넣어 시각적으로 구분합니다
- 이모지는 글 전체에 딱 1개만 사용
- 해시태그·링크·인사말("안녕하세요" 등) 금지
- 게시글 본문만 출력 (설명·따옴표 없이)"""

    text = _call_gemini(prompt)
    return _finalize(text, max_blocks=4)


# ── 메인 ─────────────────────────────────────────────────────

def main():
    config.validate()
    if not config.GEMINI_API_KEY:
        raise SystemExit("[설정 오류] GEMINI_API_KEY가 비어 있습니다. (aistudio.google.com에서 무료 발급)")

    now_kst = datetime.datetime.now(KST)
    date_str = now_kst.strftime("%Y-%m-%d")
    force = os.environ.get("FORCE_WINDOW", "").strip()

    state = load_posted()

    if force:
        key = date_str
        print(f"[강제 실행] 시간 판단 없이 즉시 발행합니다. (FORCE_WINDOW={force})")
    else:
        window = current_window(now_kst)
        if not window:
            print(f"[대기] 지금({now_kst.strftime('%H:%M')} KST)은 발행 시간대가 아닙니다.")
            return
        ok, key, reason = should_post_now(now_kst, state)
        print(f"[판단] ({window}) {reason}")
        if not ok:
            return

    content_type = None
    text = None
    extra_comments = []
    queue_result = None  # open/lead일 때만 채워짐 (발행 기록용 topic/keyword/cta_idx/queue_left)

    forced_prompt_type = os.environ.get("POST_TYPE", "").strip().lower()
    if forced_prompt_type in ("open", "lead"):
        # 테스트/수동 지정 목적: 혜택·업무활용법 판단을 건너뛰고 곧장 OPEN/LEAD로 발행
        print(f"[강제 지정] POST_TYPE={forced_prompt_type} — 혜택/활용법 판단 없이 곧장 진행")
        queue_result = write_queued_prompt_post(state, now_kst, forced_type=forced_prompt_type)
        text = queue_result["text"]
        content_type = queue_result["post_type"]
    else:
        benefit_item = pick_benefit_item(date_str)
        if benefit_item:
            benefit_text = write_benefit_post(benefit_item, now_kst)
            if benefit_text:
                content_type = "benefit"
                text = benefit_text
                state["used_titles"].append(benefit_item["title"])

        if not content_type:
            kind = POST_CYCLE[state["cycle_index"] % len(POST_CYCLE)]
            if kind == "prompt":
                queue_result = write_queued_prompt_post(state, now_kst)
                text = queue_result["text"]
                content_type = queue_result["post_type"]
            else:
                text = write_howto_post(state, now_kst)
                content_type = "howto"

    print(f"[유형] {content_type}")
    print(f"[초안] {text[:80]}...")

    post_id = threads_api.publish_text(text)
    print(f"[발행 완료] post id: {post_id}")

    append_posted_log({
        "date_kst": now_kst.isoformat(),
        "post_type": content_type,
        "topic": (queue_result or {}).get("topic", ""),
        "keyword": (queue_result or {}).get("keyword", ""),
        "cta_id": (queue_result or {}).get("cta_idx", ""),
        "queue_left": (queue_result or {}).get("queue_left", ""),
        "thread_id": post_id,
        "is_draft": False,
    })
    update_topic_doc(state, now_kst)

    if content_type == "prompt":
        for i, c in enumerate(extra_comments, start=2):
            try:
                time.sleep(3)
                threads_api.reply_to(post_id, c)
                print(f"[댓글 {i}] 발행 완료")
            except Exception as e:
                print(f"[경고] 댓글 {i} 발행 실패 (본문은 정상 발행됨): {e}")

    state["posted"][key] = {"time": now_kst.isoformat(), "post_id": post_id, "type": content_type}
    if content_type != "benefit":
        state["cycle_index"] = (state["cycle_index"] + 1) % len(POST_CYCLE)
    if content_type == "prompt":
        state["prompt_seq"] = state.get("prompt_seq", 0) + 1
    state["cta_seq"] = state.get("cta_seq", 0) + 1
    save_posted(state)


if __name__ == "__main__":
    main()
