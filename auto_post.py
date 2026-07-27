# -*- coding: utf-8 -*-
"""매일 자동 글 발행 봇 (v1.5).

GitHub Actions가 발행 시간대(아침 7~9시 / 저녁 6~8시 KST) 동안 20분마다 실행하고,
이 스크립트는 날짜별로 정해지는 랜덤 목표 시각이 지났을 때 딱 1회 발행한다.

콘텐츠 파이프라인 (전부 무료):
  구글 뉴스 RSS(제목+요약) → Gemini 무료 API로 스레드 글 작성 → Threads API 발행

코너 구성 (시리즈성):
  - 아침: 뉴스 1개를 누구보다 쉽게 풀어주는 "아침 뉴스 해석"
  - 저녁: 오늘 바로 따라하는 "저녁 실전 활용팁" (화/목은 경제 소식 참고)

품질 원칙:
  - 뉴스는 딱 1개만 깊게 (산만함 방지 + 계정 태깅 일관성)
  - 제목/요약에 있는 사실만 언급 (추측 금지)
  - 부드러운 존댓말, 정보 제시 후 활용법으로 마무리 (질문 유도 없음)
"""
import hashlib
import json
import os
import re
import datetime
import requests
import feedparser

import config
import threads_api

KST = datetime.timezone(datetime.timedelta(hours=9))
POSTED_FILE = os.path.join(os.path.dirname(__file__), "state", "posted.json")

# 발행 시간대 정의 (KST 기준)
WINDOWS = {
    "morning": {"start_hour": 7, "offset_max_min": 59},   # 7:00 ~ 7:59 사이 랜덤
    "evening": {"start_hour": 19, "offset_max_min": 59},  # 19:00 ~ 19:59 사이 랜덤
}

GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "{model}:generateContent?key={key}"
)

# 계정 페르소나 — 모든 글의 기준이 되는 정체성
PERSONA = """[계정 정체성 — 소담 AI 랩 (@sodam_ai_lab)]
- AI가 어렵고 낯선 평범한 사람들(왕초보, 직장인, 소상공인, 부업 준비생)을 위한 계정
- '소담하다'는 이름처럼: 따뜻하고, 담백하고, 과하지 않게
- 옆에서 차근차근 알려주는 다정한 선생님의 목소리
- 전문용어를 자랑하지 않고, 누구나 이해할 수 있는 쉬운 말로
- 항상 "그래서 내가 오늘 뭘 해보면 되는지"까지 알려주는 실용성
- 독자를 가르치려 들지 않고, 함께 해보자고 권하는 태도"""

# 요일별 콘텐츠 앵글 — 매일 글의 결이 달라지도록 (0=월 ~ 6=일)
WEEKDAY_ANGLES = {
    0: "한 주를 시작하며 이번 주 주목할 AI 흐름 짚어주기",
    1: "직장인·자영업자가 업무에 바로 쓰는 AI 활용팁",
    2: "구체적인 AI 도구/기능 하나를 골라 실전 사용법 소개",
    3: "AI 쓸 때 흔히 하는 실수·주의사항 짚어주기",
    4: "이번 주 AI 소식 중 놓치면 아까운 것 정리",
    5: "주말에 가볍게 시도해볼 만한 AI 활용 아이디어",
    6: "다음 주를 준비하는 관점에서 AI 트렌드 한 가지",
}

# 첫 줄 공식 — 어그로 없이 '정보의 힘'으로 스크롤을 멈추게 하는 패턴 (날마다 랜덤)
# 공통 원칙: 배경 설명으로 시작하지 말고, 가장 놀랍고 구체적인 사실을 첫 줄에 바로 꺼낸다.
HOOK_PATTERNS = [
    '가장 놀라운 사실을 그대로 첫 줄에 (예: "AI로 찍어낸 영상, 이제 수익 내기 어려워진대요")',
    '변화의 결과를 먼저 (예: "이제 OO는 무료로 할 수 있게 됐어요")',
    '독자에게 직접 영향을 주는 지점부터 (예: "OO 하시는 분들, 이번 주부터 달라져요")',
    '숫자나 구체적 사실로 시작 (예: "이 작업, 10분이면 끝난다고 해요")',
    '기존 상식이 바뀐 지점을 짚기 (예: "지금까지 OO였는데, 이제 반대가 됐어요")',
    '가장 실용적인 결론을 먼저 (예: "이것만 알아두면 OO는 안 하셔도 돼요")',
]

# ── 상태 관리 ────────────────────────────────────────────────

def load_posted():
    try:
        with open(POSTED_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"posted": {}, "used_titles": []}


def save_posted(state):
    os.makedirs(os.path.dirname(POSTED_FILE), exist_ok=True)
    state["used_titles"] = state["used_titles"][-100:]
    keys = sorted(state["posted"].keys())[-60:]
    state["posted"] = {k: state["posted"][k] for k in keys}
    with open(POSTED_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ── 시간 판단 ────────────────────────────────────────────────

def current_window(now_kst):
    """지금이 어느 발행 시간대인지 반환 (아니면 None)."""
    h = now_kst.hour
    if h == 7:
        return "morning"
    if h == 19:
        return "evening"
    return None


def target_minute_offset(date_str, window):
    """날짜+시간대별로 고정되는 랜덤 분 오프셋 (0~offset_max).

    같은 날 같은 시간대에는 항상 같은 값 → 여러 번 실행돼도 목표 시각이
    흔들리지 않고, 날마다 발행 시각이 달라져 기계적 패턴을 피한다.
    """
    seed = hashlib.sha256(f"{date_str}-{window}-sodam".encode()).hexdigest()
    return int(seed, 16) % (WINDOWS[window]["offset_max_min"] + 1)


def should_post_now(now_kst, window, state):
    date_str = now_kst.strftime("%Y-%m-%d")
    key = f"{date_str}-{window}"
    if key in state["posted"]:
        return False, key, "이미 발행함"
    offset = target_minute_offset(date_str, window)
    start = now_kst.replace(hour=WINDOWS[window]["start_hour"], minute=0, second=0, microsecond=0)
    target = start + datetime.timedelta(minutes=offset)
    if now_kst < target:
        return False, key, f"목표 시각({target.strftime('%H:%M')}) 전"
    return True, key, f"목표 시각({target.strftime('%H:%M')}) 도달"


# ── 뉴스 수집 (구글 뉴스 RSS, 무료·키 불필요) ────────────────

def _strip_html(text):
    return re.sub(r"<[^>]+>", " ", text or "").strip()


def fetch_news(query, limit=6):
    """구글 뉴스 RSS에서 (제목 + 기사 요약) 목록을 가져온다.

    요약까지 함께 전달해 AI가 제목만 보고 내용을 추측하는 일을 줄인다.
    """
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
        if published:
            pub_dt = datetime.datetime(*published[:6], tzinfo=datetime.timezone.utc)
            if (now - pub_dt).total_seconds() > 60 * 60 * 36:  # 36시간 이내만
                continue
        if title:
            summary = _strip_html(e.get("summary", ""))[:200]
            items.append({"title": title, "summary": summary})
        if len(items) >= limit:
            break
    return items


# 검색어 뱅크 — 매일 다른 조합으로 검색해 후보군을 넓히고 특정 소재 반복을 피한다
NEWS_QUERIES = [
    "AI 서비스 출시 when:1d",
    "AI 신기능 업데이트 when:1d",
    "챗GPT 활용 when:1d",
    "생성형 AI 트렌드 when:1d",
    "AI 스타트업 when:2d",
    "인공지능 규제 정책 when:2d",
    "AI 기업 도입 사례 when:2d",
]


def _pick_queries(date_str, window, n=3):
    """날짜+시간대 해시로 오늘 사용할 검색어 조합을 고정 선택 (매일 다르게)."""
    seed = int(hashlib.sha256(f"{date_str}-{window}-queries".encode()).hexdigest(), 16)
    pool = list(NEWS_QUERIES)
    picked = []
    for i in range(n):
        idx = (seed >> (i * 8)) % len(pool)
        picked.append(pool.pop(idx))
    return picked


def collect_topics(window, now_kst, used_titles):
    """시간대에 맞는 뉴스 목록과 주제 설명을 반환.

    검색어를 매일 다르게 조합해 특정 소재(예: 지자체 AI 교육)가
    계속 반복 노출되는 것을 피한다.
    """
    is_economy_day = now_kst.weekday() in (1, 3)  # 화(1), 목(3)
    date_str = now_kst.strftime("%Y-%m-%d")
    queries = _pick_queries(date_str, window, n=3)

    ai_news = []
    for q in queries:
        ai_news += fetch_news(q, 4)
    # 같은 회차에 중복 제목 제거 + 최근 사용한 제목 제외
    seen = set()
    dedup = []
    for t in ai_news:
        if t["title"] in seen or t["title"] in used_titles:
            continue
        seen.add(t["title"])
        dedup.append(t)
    ai_news = dedup[:6]

    topics = list(ai_news)
    if window == "evening" and is_economy_day:
        econ = fetch_news("경제 금리 증시 when:1d", 6)
        econ = [t for t in econ if t["title"] not in used_titles][:2]
        topics += econ
        theme = "AI 활용법 + 오늘의 경제 소식"
    elif window == "morning":
        theme = "오늘 꼭 알아야 할 AI 뉴스와 활용 포인트"
    else:
        theme = "일상·업무에 바로 쓰는 AI 활용법"
    return topics, theme


# ── 줄바꿈 후처리 (AI가 지시를 안 지켜도 코드로 보정) ────────

# 문장을 끊기 좋은 구두점 (이 뒤에서 우선적으로 줄을 나눈다)
BREAK_MARKS = ("。", ".", "!", "?", ",", "~")


def wrap_lines(text, limit=20):
    """한 줄이 limit자를 넘으면 어절(띄어쓰기) 단위로 자연스럽게 끊어 준다.

    - 단어 중간에서는 절대 끊지 않는다
    - 구두점(. , ? !) 뒤에서 끊는 것을 우선한다
    - 빈 줄(단락 구분)은 그대로 보존한다
    """
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
                # 구두점으로 끝나고 이미 적당한 길이면 여기서 끊는 게 자연스럽다
                if cur.endswith(BREAK_MARKS) and len(cur) >= limit * 0.6:
                    out_lines.append(cur)
                    cur = ""
        if cur:
            out_lines.append(cur)

    # 이모지 등 아주 짧은 조각이 혼자 줄에 남으면 앞 줄에 붙인다
    merged = []
    for l in out_lines:
        if (l and len(l) <= 3 and merged and merged[-1]
                and len(merged[-1]) + len(l) + 1 <= limit + 4):
            merged[-1] = merged[-1] + " " + l
        else:
            merged.append(l)

    # 연속된 빈 줄은 하나로 정리
    result = []
    for l in merged:
        if l == "" and result and result[-1] == "":
            continue
        result.append(l)
    return "\n".join(result).strip()


def trim_paragraphs(text, max_blocks=4):
    """단락이 너무 많으면 핵심만 남긴다.

    AI가 분량 지시를 어기고 길게 쓰는 경우를 코드로 보정한다.
    보존 우선순위: 첫 단락(훅) + 둘째 단락(뉴스 설명)
                 + 마지막 단락들(활용법 제시, 계정 각인 문구 등)
    중간의 늘어지는 부연 설명 단락을 걷어낸다.
    """
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
    if len(blocks) <= max_blocks:
        return "\n\n".join(blocks)
    head = blocks[:2]                 # 훅 + 무슨 일인지
    tail = blocks[-(max_blocks - 2):]  # 실행 제안(+계정 각인 문구)
    return "\n\n".join(head + tail)


# ── Gemini로 글 작성 (무료 등급) ─────────────────────────────

def write_post(topics, theme, window, now_kst):
    angle = WEEKDAY_ANGLES.get(now_kst.weekday(), "")
    # 날짜+시간대 해시로 오늘의 후킹 패턴을 고정 선택 (재시도해도 동일)
    seed = hashlib.sha256(f"{now_kst.strftime('%Y-%m-%d')}-{window}-hook".encode()).hexdigest()
    hook = HOOK_PATTERNS[int(seed, 16) % len(HOOK_PATTERNS)]

    news_lines = []
    for t in topics:
        line = f"- 제목: {t['title']}"
        if t.get("summary"):
            line += f"\n  요약: {t['summary']}"
        news_lines.append(line)

    # 팔로우 전환용 각인 문구 — 매번 넣으면 광고처럼 보이므로 주 2회만 (월/금 아침)
    show_identity = (window == "morning" and now_kst.weekday() in (0, 4))
    identity_rule = ("""
[계정 각인 — 오늘은 넣습니다]
글의 맨 마지막(3단락 뒤)에, 이 계정이 무엇을 하는 곳인지 알려주는 짧은 한 줄을 자연스럽게 넣으세요.
광고처럼 들리지 않게, 담백하게 한 줄이면 충분합니다.
(예: "AI 어렵지 않게 풀어드리는 이야기, 계속 올릴게요")
"팔로우 해주세요" 같은 직접적인 요청은 쓰지 않습니다.
""" if show_identity else "")

    if window == "morning":
        corner = """[오늘의 코너: 아침 뉴스 해석]
아침 글은 "뉴스 1개를 누구보다 쉽게 풀어주고, 그걸 어떻게 써먹을지까지 알려주는 코너"입니다.

[글의 구조 — 반드시 이 3단으로, 질문 없이 정보로 끝냅니다]
1단락) 무슨 일이 있었나: 위 뉴스 중 **가장 흥미롭고 독자와 관련 깊은 것 딱 1개만** 골라, 처음 듣는 사람도 이해하게 쉽게 설명
2단락) 그래서 이게 왜 중요한가: 이 소식이 평범한 개인·소상공인·직장인의 일상과 일에 어떤 의미인지
3단락) 그래서 이걸 어떻게 써먹으면 좋은가: 이 소식과 관련해서 독자가 오늘·이번 주에 바로 활용할 수 있는 구체적인 방법 (도구/서비스 이름, 실제로 어떻게 시작하면 되는지)

여러 뉴스를 한 글에 섞지 않습니다. 딱 1개만 깊게 다룹니다.
3단락이 이 글의 결론이자 마무리입니다. 질문으로 끝내지 않습니다."""
    else:
        corner = """[오늘의 코너: 저녁 실전 활용팁]
저녁 글은 "오늘 바로 따라할 수 있는 AI 활용법 1가지를 알려주는 코너"입니다.
뉴스는 참고 배경일 뿐, 중심은 실용적인 방법입니다.

[글의 구조 — 반드시 이 3단으로, 질문 없이 정보로 끝냅니다]
1단락) 이런 상황 있으시죠: 독자가 공감할 만한 일상·업무 속 불편함이나 궁금증 1가지
2단락) 이렇게 해보세요: AI로 해결하는 구체적인 방법. 어떤 도구에 뭐라고 입력하면 되는지까지 (짧은 예시 문구 포함 가능)
3단락) 이렇게 하면: 얻게 되는 결과나 절약되는 시간 — 이 글의 결론이자 마무리

방법은 딱 1가지만, 무료로 누구나 지금 바로 할 수 있는 것으로 제시합니다.
질문으로 끝내지 않습니다."""

    prompt = f"""당신은 스레드(Threads) 계정 '소담 AI 랩'의 운영자 '소담쌤'입니다.

{PERSONA}

아래 최신 뉴스(제목+요약)를 참고해서 스레드 게시글 1개를 한국어로 작성하세요.

[오늘의 뉴스]
{chr(10).join(news_lines) if news_lines else '- (뉴스 없음: 일반 AI 활용 팁으로 작성)'}

[주제 방향]
{theme}
오늘의 앵글: {angle}

{corner}
{identity_rule}
[첫 줄 — 가장 중요]
첫 줄은 이 패턴으로: {hook}

첫 줄에서 스크롤이 멈추지 않으면 아무도 나머지를 읽지 않습니다.
반드시 지킬 것:
- 배경 설명("요즘 ~가 달라지고 있어요", "최근 소식인데요")으로 시작하지 마세요. 힘이 없습니다.
- 이 글에서 **가장 놀랍거나 구체적인 사실 하나를 첫 줄에 바로** 꺼내세요.
- 나쁜 예: "요즘 유튜브 분위기가 달라지고 있어요" → 좋은 예: "AI로 찍어낸 영상, 이제 수익 내기 어려워진대요"
- 어그로·과장·경고는 쓰지 않되, 정보 자체의 힘으로 눈길을 끕니다.
- 첫 줄은 25자 이내로 짧게.
- 첫 줄에서 약속한 내용은 본문에서 반드시 지킵니다.

[사실성 규칙 — 가장 중요]
- 위 뉴스의 제목과 요약에 실제로 적힌 내용만 사실로 언급합니다
- 제목/요약에 없는 수치, 날짜, 발표 내용, 세부 사항을 추측해서 쓰지 않습니다
- 확신할 수 없는 부분은 "~라고 해요", "~라는 소식이에요"처럼 전달하는 어조로 씁니다
- 뉴스 내용이 불충분하면 뉴스 언급을 줄이고 일반적인 AI 활용 팁 중심으로 작성합니다

[어조 규칙]
- 부드럽고 친근한 존댓말, 옆에서 알려주는 느낌
- 명령, 경고, 단정, 과장, 어그로 표현 금지
- 강요하지 않고 제안하는 톤 ("~해보시는 것도 좋아요")

[마무리]
글은 질문 없이, 3단락의 정보/활용법 제시로 자연스럽게 끝납니다.
"~해보세요", "~하시면 좋아요"처럼 담백한 제안으로 마무리합니다.

[분량 규칙 — 반드시 지킬 것]
- 전체 200~280자 (공백 포함). 길면 지루해서 끝까지 안 읽습니다.
- **단락은 정확히 3개.** 그 이상 늘리지 마세요.
- 각 단락은 짧게: 2문장 이내
- 하고 싶은 말이 더 있어도 과감히 버리세요. 짧고 선명한 글이 더 많이 읽힙니다.

[가독성 규칙 — 줄바꿈이 핵심]
- **한 줄은 반드시 20자 이내.** 20자가 넘으면 문장 중간이라도 끊어서 줄바꿈하세요.
  나쁜 예: "인공지능으로 뚝딱 찍어낸 영상은 이제 돈을 벌기 어려워진다는 소식이 들려왔거든요."
  좋은 예:
    인공지능으로 뚝딱 찍어낸 영상은
    이제 돈 벌기 어려워졌대요.
- 의미가 끊기는 자연스러운 지점에서 줄을 나눕니다 (조사·어미 뒤)
- 3개 단락 사이에는 빈 줄을 넣어 시각적으로 구분합니다
- 어려운 용어는 쉬운 말로 바꿔 씁니다 (전문용어를 써야 하면 괄호로 짧게 풀이)
- 이모지는 글 전체에 딱 1개만 사용
- 해시태그·링크·인사말("안녕하세요" 등) 금지
- 게시글 본문만 출력 (설명·따옴표 없이)"""

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
    text = text.strip().strip('"').strip()
    text = trim_paragraphs(text, max_blocks=4)  # 단락 수 강제 (분량 통제)
    text = wrap_lines(text, limit=20)           # 줄바꿈 강제 보정
    if len(text) > 495:
        text = text[:492] + "…"
    return text


# ── 메인 ─────────────────────────────────────────────────────

def main():
    config.validate()
    if not config.GEMINI_API_KEY:
        raise SystemExit("[설정 오류] GEMINI_API_KEY가 비어 있습니다. (aistudio.google.com에서 무료 발급)")

    now_kst = datetime.datetime.now(KST)
    force = os.environ.get("FORCE_WINDOW", "").strip()  # 수동 테스트용: morning/evening

    if force in WINDOWS:
        window = force
        print(f"[강제 실행] {window} 발행을 즉시 진행합니다.")
    else:
        window = current_window(now_kst)
        if not window:
            print(f"[대기] 지금({now_kst.strftime('%H:%M')} KST)은 발행 시간대가 아닙니다.")
            return

    state = load_posted()

    if force in WINDOWS:
        key = f"{now_kst.strftime('%Y-%m-%d')}-{window}-force{now_kst.strftime('%H%M')}"
    else:
        ok, key, reason = should_post_now(now_kst, window, state)
        print(f"[판단] {reason}")
        if not ok:
            return

    topics, theme = collect_topics(window, now_kst, state["used_titles"])
    print(f"[뉴스] {len(topics)}건 수집: {[t['title'] for t in topics[:3]]} ...")

    text = write_post(topics, theme, window, now_kst)
    print(f"[초안] {text[:80]}...")

    post_id = threads_api.publish_text(text)
    print(f"[발행 완료] post id: {post_id}")

    state["posted"][key] = {"time": now_kst.isoformat(), "post_id": post_id}
    state["used_titles"] += [t["title"] for t in topics]
    save_posted(state)


if __name__ == "__main__":
    main()
