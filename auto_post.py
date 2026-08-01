# -*- coding: utf-8 -*-
"""매일 자동 글 발행 봇 (v1.6).

GitHub Actions가 발행 시간대(아침 7시대 / 저녁 19시대 KST) 동안 20분마다 실행하고,
이 스크립트는 그 시간대 범위 안에 들어와 있으면(랜덤 목표 시각을 기다리지 않고)
아직 오늘 발행 안 했을 때 바로 1회 발행한다. 아침/저녁 모두 동일한 방식.

콘텐츠 파이프라인 (전부 무료):
  - 아침: 구글 뉴스 RSS(사건 중심 키워드) → Gemini 무료 API로 스레드 글 작성
  - 저녁: 노션 "저녁 소재 노트" DB의 실제 경험 메모를 우선 사용
          (미사용 메모가 없으면 아침처럼 뉴스 기반 자동 생성으로 대체 발행)
  → Threads API 발행

코너 구성 (시리즈성):
  - 아침 "밤사이 AI 소식": 밤사이~오늘 있었던 AI 소식 1개를 쉽게 풀어주는 코너
  - 저녁 "AI로 이렇게 바뀌었어요": 노션에 적어둔 실제 AI 활용 경험을 후킹형 글로 다듬는 코너
    (반자동 — 노션에 쓸 미사용 메모가 없을 때만 뉴스 기반 "저녁 실전 활용팁"으로 대체)

품질 원칙:
  - 뉴스/메모는 딱 1개만 깊게 (산만함 방지 + 계정 태깅 일관성)
  - 뉴스 제목/요약, 노션 메모에 실제로 있는 사실만 언급 (추측 금지)
  - 부드러운 존댓말, 4050 독자 눈높이에 맞춘 쉬운 말
"""
import hashlib
import json
import os
import re
import datetime
import requests
import feedparser

import config
import notion_api
import threads_api

KST = datetime.timezone(datetime.timedelta(hours=9))
POSTED_FILE = os.path.join(os.path.dirname(__file__), "state", "posted.json")

# 발행 시간대 정의 (KST 기준): 이 시(hour) 범위 안이면 즉시 발행 대상
WINDOWS = {
    "morning": {"hour": 7},   # 07:00 ~ 07:59
    "evening": {"hour": 19},  # 19:00 ~ 19:59
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
    '공감가는 상황으로 바로 들어가기 (예: "어제저녁, ~하다가 진짜 답답했거든요")',
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
#
# 아침/저녁 모두 동일한 방식: 해당 시(hour) 범위 안에 들어와 있고
# 오늘 아직 발행하지 않았다면, 랜덤 목표 시각을 기다리지 않고 바로 발행한다.
# (예전에는 날짜별 랜덤 분(分) 오프셋만큼 기다렸다가 발행했지만, 그 방식은 폐기했다.)

def current_window(now_kst):
    """지금이 어느 발행 시간대인지 반환 (아니면 None)."""
    h = now_kst.hour
    if h == WINDOWS["morning"]["hour"]:
        return "morning"
    if h == WINDOWS["evening"]["hour"]:
        return "evening"
    return None


def should_post_now(now_kst, window, state):
    """오늘 이 시간대에 아직 발행 안 했으면 True — 범위 안이면 즉시 발행."""
    date_str = now_kst.strftime("%Y-%m-%d")
    key = f"{date_str}-{window}"
    if key in state["posted"]:
        return False, key, "이미 발행함"
    hour = WINDOWS[window]["hour"]
    return True, key, f"발행 시간대({hour:02d}시대) 진입 — 즉시 발행"


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


# 검색어 뱅크 — "밤사이 AI 소식" 코너 기준, 트렌드어 대신 사건 중심 키워드로 구성
# (정책/출시/규제/지원/빅테크 발표처럼 "무슨 일이 있었는지"가 분명한 소식 위주)
NEWS_QUERIES = [
    "AI 정책 발표 when:1d",
    "AI 서비스 출시 when:1d",
    "AI 규제 when:2d",
    "정부 AI 지원 when:2d",
    "빅테크 AI 업데이트 when:1d",
]


def _pick_queries(date_str, window, n=3):
    """날짜+시간대 해시로 오늘 사용할 검색어 조합을 고정 선택 (매일 다르게, 재시도해도 동일)."""
    seed = int(hashlib.sha256(f"{date_str}-{window}-queries".encode()).hexdigest(), 16)
    pool = list(NEWS_QUERIES)
    picked = []
    for i in range(min(n, len(pool))):
        idx = (seed >> (i * 8)) % len(pool)
        picked.append(pool.pop(idx))
    return picked


def collect_topics(window, now_kst, used_titles):
    """시간대에 맞는 뉴스 목록과 주제 설명을 반환.

    검색어를 매일 다르게 조합해 특정 소재가 계속 반복 노출되는 것을 피한다.
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
        theme = "밤사이 있었던 AI 소식과 4050에게 중요한 이유"
    else:
        theme = "일상·업무에 바로 쓰는 AI 활용법 (노션에 쓸 실제 경험 메모가 없을 때의 대체 발행)"
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
    보존 우선순위: 첫 단락(훅) + 둘째 단락(설명) + 마지막 단락들(결론/마무리)
    중간의 늘어지는 부연 설명 단락을 걷어낸다.
    """
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
    if len(blocks) <= max_blocks:
        return "\n\n".join(blocks)
    head = blocks[:2]                 # 훅 + 무슨 일인지
    tail = blocks[-(max_blocks - 2):]  # 결론 + 마무리
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
    text = trim_paragraphs(text, max_blocks=max_blocks)  # 단락 수 강제 (분량 통제)
    text = wrap_lines(text, limit=20)                    # 줄바꿈 강제 보정
    if len(text) > 495:
        text = text[:492] + "…"
    return text


# ── 아침 "밤사이 AI 소식" / 저녁 대체발행 "저녁 실전 활용팁" ──

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

    block_count = 4 if window == "morning" else 3

    # 팔로우 전환용 각인 문구 — 매번 넣으면 광고처럼 보이므로 주 2회만 (월/금 아침)
    show_identity = (window == "morning" and now_kst.weekday() in (0, 4))
    identity_rule = ("""
[계정 각인 — 오늘은 넣습니다]
마지막 단락(따뜻한 마무리) 안에, 이 계정이 무엇을 하는 곳인지 알려주는 짧은 한 마디를 자연스럽게 녹여 넣으세요.
광고처럼 들리지 않게, 담백하게 한 문장이면 충분합니다.
(예: "AI 어렵지 않게 풀어드리는 이야기, 계속 올릴게요. 오늘 하루도 힘내세요!")
"팔로우 해주세요" 같은 직접적인 요청은 쓰지 않습니다.
""" if show_identity else "")

    if window == "morning":
        corner = """[오늘의 코너: 밤사이 AI 소식]
아침 글은 "밤사이 있었던 AI 소식 1개를 누구보다 쉽게 풀어주는 코너"입니다.

[글의 구조 — 반드시 이 4단으로, 질문 없이 따뜻한 응원으로 끝냅니다]
1단락) 공감형 후킹 오프닝: 통계·수치를 나열하며 시작하지 말고, 독자의 마음을 툭 건드리는 공감형 한 줄로 시작
2단락) 무슨 일이 있었나: 위 뉴스 중 **가장 흥미롭고 독자와 관련 깊은 것 딱 1개만** 골라, 처음 듣는 사람도 이해하게 쉽게 설명
3단락) 왜 중요한가: 이 소식이 4050 독자(직장인·소상공인·부업 준비생)의 일상과 일에 왜 중요한지 한 줄로
4단락) 따뜻한 마무리: 오늘 하루를 응원하는 따뜻한 문장으로 마무리

여러 뉴스를 한 글에 섞지 않습니다. 딱 1개만 깊게 다룹니다.
4단락이 이 글의 결론이자 마무리입니다. 질문으로 끝내지 않습니다."""
    else:
        corner = """[오늘의 코너: 저녁 실전 활용팁]
저녁 글은 "오늘 바로 따라할 수 있는 AI 활용법 1가지를 알려주는 코너"입니다.
(노션에 쓸 실제 경험 메모가 없을 때만 사용하는 대체 발행입니다.)
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
- 통계나 수치를 나열하며 시작하지 마세요. 딱딱하고 힘이 없습니다.
- 배경 설명("요즘 ~가 달라지고 있어요", "최근 소식인데요")으로 시작하지 마세요.
- 이 글에서 **가장 공감 가거나 놀라운 사실 하나를 첫 줄에 바로** 꺼내세요.
- 나쁜 예: "요즘 유튜브 분위기가 달라지고 있어요" → 좋은 예: "AI로 찍어낸 영상, 이제 수익 내기 어려워진대요"
- 어그로·과장·경고는 쓰지 않되, 공감과 정보의 힘으로 눈길을 끕니다.
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
위 코너 구조에서 정한 마지막 단락 방식대로 자연스럽게 마무리합니다. 질문으로 끝내지 않습니다.

[분량 규칙 — 반드시 지킬 것]
- 전체 200~280자 (공백 포함). 길면 지루해서 끝까지 안 읽습니다.
- **단락은 정확히 {block_count}개.** 그 이상 늘리지 마세요.
- 각 단락은 짧게: 2문장 이내
- 하고 싶은 말이 더 있어도 과감히 버리세요. 짧고 선명한 글이 더 많이 읽힙니다.

[가독성 규칙 — 줄바꿈이 핵심]
- **한 줄은 반드시 20자 이내.** 20자가 넘으면 문장 중간이라도 끊어서 줄바꿈하세요.
  나쁜 예: "인공지능으로 뚝딱 찍어낸 영상은 이제 돈을 벌기 어려워진다는 소식이 들려왔거든요."
  좋은 예:
    인공지능으로 뚝딱 찍어낸 영상은
    이제 돈 벌기 어려워졌대요.
- 의미가 끊기는 자연스러운 지점에서 줄을 나눕니다 (조사·어미 뒤)
- 단락 사이에는 빈 줄을 넣어 시각적으로 구분합니다
- 어려운 용어는 쉬운 말로 바꿔 씁니다 (전문용어를 써야 하면 괄호로 짧게 풀이)
- 이모지는 글 전체에 딱 1개만 사용
- 해시태그·링크·인사말("안녕하세요" 등) 금지
- 게시글 본문만 출력 (설명·따옴표 없이)"""

    text = _call_gemini(prompt)
    return _finalize(text, max_blocks=4)


# ── 저녁 "AI로 이렇게 바뀌었어요" (노션 소재 기반) ────────────

def write_evening_post_from_note(note, now_kst):
    """노션 '저녁 소재 노트'의 실제 경험 메모를 뼈대 삼아 후킹형 글로 다듬는다."""
    seed = hashlib.sha256(f"{now_kst.strftime('%Y-%m-%d')}-evening-note-hook".encode()).hexdigest()
    hook = HOOK_PATTERNS[int(seed, 16) % len(HOOK_PATTERNS)]

    note_lines = [
        f"- 제목: {note['이름']}" if note.get("이름") else None,
        f"- 상황: {note['상황']}" if note.get("상황") else None,
        f"- AI 활용: {note['ai활용']}" if note.get("ai활용") else None,
        f"- 변화/결과: {note['변화결과']}" if note.get("변화결과") else None,
    ]
    note_text = "\n".join(l for l in note_lines if l)

    prompt = f"""당신은 스레드(Threads) 계정 '소담 AI 랩'의 운영자 '소담쌤'입니다.

{PERSONA}

아래는 소담쌤이 실제로 겪은 AI 활용 경험 메모입니다. 이 메모에 있는 사실만 바탕으로
스레드 게시글 1개를 한국어로 작성하세요. 메모에 없는 내용은 절대 지어내지 않습니다.

[오늘의 소재 — 실제 경험 메모]
{note_text}

[오늘의 코너: AI로 이렇게 바뀌었어요]
저녁 글은 "소담쌤이 실제로 겪은 AI 활용 경험을 진솔하게 나누는 코너"입니다.
뉴스가 아니라 실제 경험이라는 점이 이 코너의 힘입니다. 과장하지 않고 담백하게, 하지만 구체적으로 씁니다.

[글의 구조 — 반드시 이 4단으로, 질문 없이 정보로 끝냅니다]
1단락) 구체적 상황: 위 '상황'을 바탕으로, 독자가 공감할 만한 그날의 장면을 구체적으로 묘사
2단락) AI로 무엇을 했는지: 위 'AI 활용'을 바탕으로, 실제로 어떤 도구를 어떻게 활용했는지
3단락) 실제로 달라진 점: 위 '변화/결과'를 바탕으로, 그래서 무엇이 어떻게 달라졌는지
4단락) 오늘 바로 써볼 수 있는 실행 팁: 독자가 오늘 저녁 바로 따라해볼 수 있는 구체적인 한 걸음

4단락이 이 글의 결론이자 마무리입니다. 질문으로 끝내지 않습니다.

[첫 줄 — 가장 중요]
첫 줄은 이 패턴을 참고해 위 '상황'에서 가장 공감 가는 지점을 그대로 꺼내세요: {hook}
배경 설명으로 시작하지 말고, 그날 있었던 일 자체로 바로 들어가세요.
첫 줄은 25자 이내로 짧게.

[사실성 규칙 — 가장 중요]
- 위 메모(상황 / AI 활용 / 변화·결과)에 실제로 적힌 내용만 사실로 언급합니다
- 메모에 없는 수치, 도구 이름, 결과를 추측해서 지어내지 않습니다
- 메모 내용이 짧으면 억지로 부풀리지 말고, 있는 그대로 담백하게 씁니다

[어조 규칙]
- 부드럽고 친근한 존댓말, 옆에서 알려주는 느낌. 자랑이 아니라 나눔의 태도
- 명령, 경고, 단정, 과장, 어그로 표현 금지
- 4050 독자가 "나도 오늘 해볼 수 있겠다"고 느끼게

[분량 규칙 — 반드시 지킬 것]
- 전체 200~280자 (공백 포함). 길면 지루해서 끝까지 안 읽습니다.
- **단락은 정확히 4개.** 그 이상 늘리지 마세요.
- 각 단락은 짧게: 2문장 이내

[가독성 규칙 — 줄바꿈이 핵심]
- **한 줄은 반드시 20자 이내.** 20자가 넘으면 문장 중간이라도 끊어서 줄바꿈하세요.
- 의미가 끊기는 자연스러운 지점에서 줄을 나눕니다 (조사·어미 뒤)
- 4개 단락 사이에는 빈 줄을 넣어 시각적으로 구분합니다
- 어려운 용어는 쉬운 말로 바꿔 씁니다 (전문용어를 써야 하면 괄호로 짧게 풀이)
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

    # 저녁 시간대는 노션 "저녁 소재 노트"의 실제 경험 메모를 우선 사용한다.
    evening_note = notion_api.get_unused_evening_note() if window == "evening" else None
    topics = []

    if evening_note:
        print(f"[저녁 소재] 노션 메모 사용: {evening_note['이름']} ({evening_note.get('날짜') or '날짜 없음'})")
        text = write_evening_post_from_note(evening_note, now_kst)
    else:
        if window == "evening":
            print("[저녁 소재] 미사용 노션 메모 없음 → 뉴스 기반 자동 생성으로 대체합니다.")
        topics, theme = collect_topics(window, now_kst, state["used_titles"])
        print(f"[뉴스] {len(topics)}건 수집: {[t['title'] for t in topics[:3]]} ...")
        text = write_post(topics, theme, window, now_kst)

    print(f"[초안] {text[:80]}...")

    post_id = threads_api.publish_text(text)
    print(f"[발행 완료] post id: {post_id}")

    if evening_note:
        try:
            notion_api.mark_evening_note_used(evening_note["page_id"])
            print(f"[노션] 사용여부 업데이트 완료: {evening_note['이름']}")
        except Exception as e:
            print(f"[경고] 발행은 완료됐지만 노션 사용여부 업데이트에 실패했습니다: {e}")

    state["posted"][key] = {"time": now_kst.isoformat(), "post_id": post_id}
    state["used_titles"] += [t["title"] for t in topics]
    save_posted(state)


if __name__ == "__main__":
    main()
