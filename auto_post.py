# -*- coding: utf-8 -*-
"""매일 자동 글 발행 봇.

GitHub Actions가 발행 시간대(아침 7~9시 / 저녁 6~8시 KST) 동안 20분마다 실행하고,
이 스크립트는 날짜별로 정해지는 랜덤 목표 시각이 지났을 때 딱 1회 발행한다.

콘텐츠 파이프라인 (전부 무료):
  구글 뉴스 RSS(AI/경제) → Gemini 무료 API로 스레드 글 작성 → Threads API 발행

주제 규칙:
  - 아침: 오늘 꼭 알아야 할 AI 뉴스 + 활용 포인트
  - 저녁: AI 활용법 중심 (화/목요일은 경제뉴스 포함)
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

# 발행 시간대 정의 (KST 기준): (이름, 시작시각, 랜덤 오프셋 최대 분)
WINDOWS = {
    "morning": {"start_hour": 7, "offset_max_min": 100},   # 7:00 ~ 8:40 사이 랜덤
    "evening": {"start_hour": 18, "offset_max_min": 100},  # 18:00 ~ 19:40 사이 랜덤
}

GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "{model}:generateContent?key={key}"
)

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

# 첫 줄 후킹 공식 — 스크롤을 멈추게 하는 검증된 패턴들 (날마다 랜덤 지정)
HOOK_PATTERNS = [
    '"절대 ~하지 마세요"로 시작하는 경고형',
    '"제발 이것만은 해보세요"류의 간곡한 권유형',
    '"~해야 하는 이유 3가지"처럼 숫자를 내세운 넘버링형',
    '"소신발언 하나 하겠습니다"로 시작하는 소신발언형',
    '"아직도 ~하고 계세요?"처럼 찔리게 만드는 질문형',
    '"오늘 이 소식 모르면 손해예요"류의 정보 격차 자극형',
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
    # posted 기록은 최근 30일만 유지
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

    해시 기반이라 같은 날 같은 시간대에는 항상 같은 값 → 여러 번 실행돼도
    목표 시각이 흔들리지 않고, 날마다 발행 시각이 달라져 기계적 패턴을 피한다.
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

def fetch_news(query, limit=6):
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
            items.append(title)
        if len(items) >= limit:
            break
    return items


def collect_topics(window, now_kst, used_titles):
    """시간대에 맞는 뉴스 제목 목록과 주제 설명을 반환."""
    is_economy_day = now_kst.weekday() in (1, 3)  # 화(1), 목(3)
    ai_news = fetch_news("AI 인공지능 when:1d", 8)
    ai_news = [t for t in ai_news if t not in used_titles][:4]

    topics = list(ai_news)
    if window == "evening" and is_economy_day:
        econ = fetch_news("경제 금리 증시 when:1d", 6)
        econ = [t for t in econ if t not in used_titles][:2]
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
    # 날짜+시간대 해시로 오늘의 후킹 패턴을 고정 랜덤 선택 (재시도해도 동일)
    seed = hashlib.sha256(f"{now_kst.strftime('%Y-%m-%d')}-{window}-hook".encode()).hexdigest()
    hook = HOOK_PATTERNS[int(seed, 16) % len(HOOK_PATTERNS)]

    prompt = f"""당신은 한국의 AI 활용 정보 스레드(Threads) 계정 운영자입니다.
아래 최신 뉴스 제목들을 참고해서 스레드 게시글 1개를 한국어로 작성하세요.

[오늘의 뉴스 제목들]
{chr(10).join('- ' + t for t in topics) if topics else '- (뉴스 없음: 일반 AI 활용 팁으로 작성)'}

[주제 방향]
{theme}
오늘의 앵글: {angle}

[첫 줄 후킹 — 가장 중요]
첫 줄은 반드시 이 패턴으로: {hook}
스레드는 첫 줄에서 스크롤이 멈추느냐로 승부가 납니다. 첫 줄만 읽어도 계속 읽고 싶어야 합니다.

[작성 규칙]
- 뉴스 중 1~2개만 골라 핵심을 쉽게 풀고, 독자가 오늘 바로 써먹을 수 있는 활용 팁이나 시사점 1개를 반드시 포함
- 전체 250~350자 (공백 포함), 문장은 짧게 끊기
- 한 줄은 10~20자 이내로 짧게, 자주 줄바꿈해서 모바일에서 리듬감 있게 읽히도록
- 존댓말, 이모지는 딱 1개, 해시태그·링크·인사말 금지
- 과장/어그로 금지, 담백하고 신뢰감 있는 톤 (후킹은 첫 줄로 충분)
- 게시글 본문만 출력 (설명·따옴표 없이)"""
    url = GEMINI_URL.format(model=config.GEMINI_MODEL, key=config.GEMINI_API_KEY)
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.9, "maxOutputTokens": 800},
    }
    r = requests.post(url, json=body, timeout=60)
    if not r.ok:
        raise RuntimeError(f"Gemini 호출 실패: {r.status_code} {r.text[:300]}")
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
    print(f"[뉴스] {len(topics)}건 수집: {topics[:3]} ...")

    text = write_post(topics, theme, window, now_kst)
    print(f"[초안] {text[:80]}...")

    post_id = threads_api.publish_text(text)
    print(f"[발행 완료] post id: {post_id}")

    state["posted"][key] = {"time": now_kst.isoformat(), "post_id": post_id}
    state["used_titles"] += topics
    save_posted(state)


if __name__ == "__main__":
    main()
