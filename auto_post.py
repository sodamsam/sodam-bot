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
  - 부드러운 존댓말, 마지막은 댓글 유도 열린 질문
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
    "morning": {"start_hour": 7, "offset_max_min": 100},   # 7:00 ~ 8:40 사이 랜덤
    "evening": {"start_hour": 18, "offset_max_min": 100},  # 18:00 ~ 19:40 사이 랜덤
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

# 첫 줄 후킹 공식 — 부드럽지만 눈길이 가는 패턴들 (날마다 랜덤 지정)
HOOK_PATTERNS = [
    '뉴스의 핵심을 한 문장으로 요약해 던지기 (예: "이제 OO도 AI가 대신한다고 해요")',
    '독자에게 부드럽게 묻기 (예: "OO 해보신 적 있으세요?")',
    '몰랐던 사실에 공감하기 (예: "저도 이건 오늘 처음 알았는데요")',
    '변화의 신호를 짚기 (예: "요즘 OO 쪽 분위기가 달라지고 있어요")',
    '알아두면 좋은 정보로 시작 (예: "오늘 이건 알아두시면 좋을 것 같아요")',
    '독자와 관련짓기 (예: "OO 하시는 분들은 눈여겨볼 만한 소식이에요")',
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
    if 7 <= h < 9:
        return "morning"
    if 18 <= h < 20:
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


def collect_topics(window, now_kst, used_titles):
    """시간대에 맞는 뉴스 목록과 주제 설명을 반환."""
    is_economy_day = now_kst.weekday() in (1, 3)  # 화(1), 목(3)
    ai_news = fetch_news("AI 인공지능 when:1d", 8)
    ai_news = [t for t in ai_news if t["title"] not in used_titles][:4]

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

    if window == "morning":
        corner = """[오늘의 코너: 아침 뉴스 해석]
아침 글은 "뉴스 1개를 누구보다 쉽게 풀어주는 코너"입니다.

[글의 구조 — 반드시 이 3단으로]
1단락) 무슨 일이 있었나: 위 뉴스 중 **가장 흥미롭고 독자와 관련 깊은 것 딱 1개만** 골라, 처음 듣는 사람도 이해하게 쉽게 설명
2단락) 그래서 이게 왜 중요한가: 이 소식이 평범한 개인·소상공인·직장인의 일상과 일에 어떤 의미인지
3단락) 그럼 뭘 하면 좋은가: 오늘·이번 주에 시도해볼 수 있는 구체적인 행동이나 방향 1가지

여러 뉴스를 한 글에 섞지 않습니다. 딱 1개만 깊게 다룹니다."""
    else:
        corner = """[오늘의 코너: 저녁 실전 활용팁]
저녁 글은 "오늘 바로 따라할 수 있는 AI 활용법 1가지를 알려주는 코너"입니다.
뉴스는 참고 배경일 뿐, 중심은 실용적인 방법입니다.

[글의 구조 — 반드시 이 3단으로]
1단락) 이런 상황 있으시죠: 독자가 공감할 만한 일상·업무 속 불편함이나 궁금증 1가지
2단락) 이렇게 해보세요: AI로 해결하는 구체적인 방법. 어떤 도구에 뭐라고 입력하면 되는지까지 (짧은 예시 문구 포함 가능)
3단락) 이렇게 하면: 얻게 되는 결과나 절약되는 시간, 한 걸음 더 나아갈 방향

방법은 딱 1가지만, 무료로 누구나 지금 바로 할 수 있는 것으로 제시합니다."""

    prompt = f"""당신은 스레드(Threads) 계정 '소담 AI 랩'의 운영자 '소담쌤'입니다.

{PERSONA}

아래 최신 뉴스(제목+요약)를 참고해서 스레드 게시글 1개를 한국어로 작성하세요.

[오늘의 뉴스]
{chr(10).join(news_lines) if news_lines else '- (뉴스 없음: 일반 AI 활용 팁으로 작성)'}

[주제 방향]
{theme}
오늘의 앵글: {angle}

{corner}

[첫 줄]
첫 줄은 이 패턴으로 부드럽게 시작: {hook}
첫 줄은 스크롤을 멈추게 하는 가장 중요한 한 줄입니다. 숫자·의외성·질문 중 하나를 자연스럽게 담되, 경고·명령·자극적인 표현은 쓰지 않습니다.
첫 줄에서 약속한 내용은 본문에서 반드시 지킵니다.

[사실성 규칙 — 가장 중요]
- 위 뉴스의 제목과 요약에 실제로 적힌 내용만 사실로 언급합니다
- 제목/요약에 없는 수치, 날짜, 발표 내용, 세부 사항을 추측해서 쓰지 않습니다
- 확신할 수 없는 부분은 "~라고 해요", "~라는 소식이에요"처럼 전달하는 어조로 씁니다
- 뉴스 내용이 불충분하면 뉴스 언급을 줄이고 일반적인 AI 활용 팁 중심으로 작성합니다

[어조 규칙]
- 부드럽고 친근한 존댓말, 옆에서 알려주는 느낌
- 명령, 경고, 단정, 과장, 어그로 표현 금지
- 강요하지 않고 제안하는 톤 ("~해보시는 것도 좋아요")

[마무리 — 댓글 유도]
글의 마지막은 독자에게 묻는 가볍고 짧은 열린 질문 1개로 끝냅니다.
(예: "여러분은 어떻게 쓰고 계세요?", "이런 기능 써보고 싶으신가요?")
답하기 부담 없는 질문이어야 합니다.

[가독성 규칙]
- 전체 250~350자 (공백 포함)
- 한 문장은 한 줄에. 한 줄은 10~20자 이내로 짧게 끊습니다
- 3개 단락 사이에는 빈 줄을 넣어 시각적으로 구분합니다
- 어려운 용어는 쉬운 말로 바꿔 씁니다 (전문용어를 써야 하면 괄호로 짧게 풀이)
- 이모지는 글 전체에 딱 1개만 사용
- 해시태그·링크·인사말("안녕하세요" 등) 금지
- 게시글 본문만 출력 (설명·따옴표 없이)"""

    url = GEMINI_URL.format(model=config.GEMINI_MODEL, key=config.GEMINI_API_KEY)
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 800},
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
