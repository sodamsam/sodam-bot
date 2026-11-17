# -*- coding: utf-8 -*-
"""네이버 블로그 글 자동 준비 (하루 1편).

GitHub Actions에서 돌아 '오늘 올릴 블로그 글 + 이미지'를 만들어 naver_queue/ 에 쌓아 둔다.
실제 발행은 내 PC 크롬의 '소담 허브 네이버 도우미' 확장프로그램이 이 파일을 읽어서 한다
(네이버는 글쓰기 API가 없어 로그인된 내 브라우저에서만 올릴 수 있다).

소재를 고르는 기준
  - 짝수 날: 네이버 자동완성에 실제로 뜨는 검색어(예: "챗GPT 사용법 엑셀") → 따라 하는 방법 글 + 복붙 프롬프트 카드 4장
  - 홀수 날: 한국 뉴스에서 지금 많이 다루는 AI 소식(hotnews) → 쉬운 풀이 글 + 요점 카드 1장
            (기준을 넘는 소식이 없으면 그날도 검색어 글)

품질 원칙(fail-closed): 근거 없는 숫자·기관명·마감 문구가 남으면 한 번 다시 쓰게 하고, 그래도 남으면 그날은 만들지 않는다.

DRY_RUN=yes 면 previews/ 에만 저장하고 아무것도 올리지 않는다.
"""
import datetime
import html
import json
import os
import re
import subprocess
import sys

import requests

import config
import humanize

REPO = os.path.dirname(os.path.abspath(__file__))
QUEUE_DIR = os.path.join(REPO, "naver_queue")
STATE_FILE = os.path.join(REPO, "state", "naver_posted.json")
KST = datetime.timezone(datetime.timedelta(hours=9))
KEEP_IN_INDEX = 10

# 자동완성을 물어볼 씨앗 말
SEEDS = ["챗GPT 사용법", "챗GPT로", "챗GPT 프롬프트", "클로드 사용법", "제미나이 사용법",
         "AI 활용", "AI로", "챗GPT", "클로드", "제미나이"]
# 글감으로 좋은 말(업무·생활에서 바로 해볼 수 있는 것)
GOOD_WORDS = ("사용법", "방법", "프롬프트", "엑셀", "PPT", "ppt", "보고서", "요약", "번역", "회의록", "메일",
              "이미지", "사진", "글쓰기", "블로그", "한글", "문서", "기획서", "자기소개서", "영어", "공부",
              "활용", "설치", "가입", "시작", "초보", "꿀팁", "정리", "만들기")
# 사실 확인이 필요한 말(가격·요금)과 계정 성격에 안 맞는 말은 검색어 글에서 뺀다
BAD_WORDS = ("주가", "주식", "관련주", "투자", "코인", "탈옥", "우회", "크랙", "대필", "성인", "19", "해킹",
             "요금", "가격", "할인", "환불", "결제", "구독료", "무료", "오류", "먹통", "장애", "점검", "나무위키",
             "논란", "소송", "사망", "자살", "정치", "대통령", "선거", "로또", "당첨", "사주", "운세",
             "로그인", "로그아웃", "탈퇴", "책", "강의", "사이트")

CLAIM_PATTERN = re.compile(
    r"\d[\d,]*\s*(?:만\s*원|원|명|개소|개월|건|퍼센트|%|배)"
    r"|\d+\s*월\s*\d+\s*일|\d+\s*일\s*(?:까지|간)|\d{2,4}-\d{3,4}-\d{4}"
)
RISKY_WORDS = ("선착순", "마감 임박", "조기 마감", "오늘까지", "내일까지", "전액", "100%", "무조건", "보장")

FOOTER = "소담쌤 · 가장 쉬운 AI 활용법 · 왕초보도 오늘 바로 쓰는 AI 안내소"


def dry_run():
    return os.environ.get("DRY_RUN", "").strip().lower() in ("1", "y", "yes", "true")


# ── 상태 ─────────────────────────────────────────────────────

def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"dates": {}, "used_keywords": [], "used_titles": []}


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ── 소재 고르기 ──────────────────────────────────────────────

def naver_suggest(query):
    """네이버 검색창 자동완성 목록(사람들이 실제로 치는 말)."""
    r = requests.get(
        "https://ac.search.naver.com/nx/ac",
        params={"q": query, "con": "1", "frm": "nv", "ans": "2", "r_format": "json", "r_enc": "UTF-8",
                "r_unicode": "0", "t_koreng": "1", "run": "2", "rev": "4", "q_enc": "UTF-8", "st": "100"},
        headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
    data = r.json()
    return [i[0] for i in data["items"][0]] if data.get("items") else []


def keyword_score(phrase):
    """검색어가 글감으로 얼마나 좋은지. 0 이하면 쓰지 않는다."""
    if any(b in phrase for b in BAD_WORDS):
        return 0
    if not 5 <= len(phrase) <= 24 or len(phrase.split()) < 2:
        return 0
    return 1 + sum(2 for g in GOOD_WORDS if g in phrase)


def pick_keyword(used_keywords, suggest=naver_suggest):
    """아직 안 쓴 검색어 중 점수가 가장 높은 것. 없으면 None."""
    used = {re.sub(r"\s", "", k) for k in used_keywords}
    best = None
    for order, seed in enumerate(SEEDS):
        try:
            phrases = suggest(seed)
        except Exception as e:
            print(f"[네이버글] '{seed}' 자동완성 실패(건너뜀): {e}")
            continue
        for rank, ph in enumerate(phrases):
            ph = re.sub(r"gpt", "GPT", ph.strip(), flags=re.I)   # 자동완성은 소문자로 온다
            if re.sub(r"\s", "", ph) in used:
                continue
            s = keyword_score(ph)
            if s <= 0:
                continue
            key = (-s, order, rank)
            if best is None or key < best[0]:
                best = (key, ph)
    return best[1] if best else None


# ── 글 쓰기 ──────────────────────────────────────────────────

PERSONA = """너는 '소담쌤'이다. 컴퓨터가 익숙하지 않은 40~60대에게 AI 활용법을 옆에서 차근차근 알려주는 다정한 강사다.
말투: 부드러운 존댓말, 짧은 문장, 어려운 용어는 바로 우리말로 풀이. 과장·낚시·겁주기 금지.
"저도 마흔 넘어 AI를 처음 배웠습니다"라는 사람의 눈높이로 쓴다."""
PERSONA = PERSONA + "\n\n" + humanize.rules_prompt()

HOWTO_PROMPT = PERSONA + """

네이버에서 사람들이 실제로 검색하는 말 「{keyword}」 로 들어온 사람이 끝까지 읽고 바로 따라 할 수 있는 블로그 글을 써라.

규칙
- 제목(title): 「{keyword}」의 핵심 낱말을 그대로 넣고 40자 이내. 나이·'교시' 같은 말은 넣지 않는다.
- intro: 이 글을 읽으면 무엇을 할 수 있게 되는지 3~4문장.
- sections: 3~4개. 각 section은 heading(소제목), paragraphs(문단 2~3개), steps(따라 하는 순서, 없으면 빈 배열).
  어느 버튼을 누르는지까지 건너뛰지 말고 적는다.
- prompt: 독자가 그대로 복사해 쓸 수 있는 AI 지시문 1개(4~8줄, 바꿀 곳은 [대괄호]).
- prompt_situation: 이 지시문을 언제 쓰는지 한 문장.
- example: 지시문을 넣었을 때 나오는 결과의 짧은 예시(5줄 이내).
- tip: 한 가지 더 알려줄 요령 1~2문장.
- closing: 오늘 해볼 일 한 가지로 마무리하는 2문장.
- tags: 해시태그용 낱말 6~8개(# 없이).
- card_points: 요점 3개. 각 항목은 title(14자 이내), desc(30자 이내).

절대 금지: 가격·요금·할인율·통계 수치·날짜·기간·기관 이름을 쓰지 마라(확인할 수 없는 사실이다).
화면 메뉴 이름이 확실하지 않으면 "화면 아래 입력칸"처럼 위치로 설명하라.

JSON 하나로만 답하라. 키: title, intro, sections, prompt, prompt_situation, example, tip, closing, tags, card_points"""

NEWS_PROMPT = PERSONA + """

아래 [소식]을 처음 듣는 사람도 이해하도록 풀어 쓰는 블로그 글을 써라.

규칙
- [소식]에 적힌 사실만 쓴다. 적혀 있지 않은 숫자·날짜·금액·기관 이름·신청 방법은 절대 지어내지 마라.
  자세한 내용이 [소식]에 없으면 "자세한 내용은 공식 안내를 확인해 주세요"라고 쓴다.
- 제목(title): 사람들이 검색할 낱말(제품·제도 이름)을 넣고 40자 이내.
- intro: 무슨 소식인지 3~4문장.
- sections: 3개. heading, paragraphs(문단 2~3개), steps(없으면 빈 배열).
  순서: 무슨 일인가 → 나에게 어떤 의미인가 → 오늘 해볼 수 있는 것.
- closing: 2문장.
- tags: 해시태그용 낱말 6~8개(# 없이).
- card_points: 요점 3개. 각 항목은 title(14자 이내), desc(30자 이내).

JSON 하나로만 답하라. 키: title, intro, sections, closing, tags, card_points

[소식]
{source}"""


def call_gemini_json(prompt):
    url = ("https://generativelanguage.googleapis.com/v1beta/models/"
           f"{config.GEMINI_MODEL}:generateContent")
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.6, "maxOutputTokens": 6000,
                             "responseMimeType": "application/json"},
    }
    r = requests.post(url, json=body, headers={"x-goog-api-key": config.GEMINI_API_KEY}, timeout=120)
    if not r.ok:
        raise RuntimeError(f"Gemini 호출 실패: {r.status_code} {r.text[:200]}")
    try:
        return json.loads(r.json()["candidates"][0]["content"]["parts"][0]["text"])
    except (KeyError, IndexError, ValueError) as e:
        raise RuntimeError(f"Gemini 응답 형식 오류: {e}")


def draft_text(d):
    """검사·글자 수 계산용으로 글 전체를 한 줄로 잇는다."""
    out = [str(d.get("title", "")), str(d.get("intro", ""))]
    for s in d.get("sections") or []:
        out.append(str(s.get("heading", "")))
        out += [str(p) for p in s.get("paragraphs") or []]
        out += [str(p) for p in s.get("steps") or []]
    for k in ("prompt", "example", "tip", "closing"):
        out.append(str(d.get(k, "")))
    for p in d.get("card_points") or []:
        out.append(f"{p.get('title', '')} {p.get('desc', '')}")
    return "\n".join(out)


def clean_draft(d):
    """초안의 모든 글에 AI 티 지우기(기계적 손질)를 적용한다. 복붙 지시문·예시는 그대로 둔다."""
    for k in ("title", "intro", "prompt_situation", "tip", "closing"):
        if isinstance(d.get(k), str):
            d[k] = humanize.clean(d[k])
    for s in d.get("sections") or []:
        if isinstance(s.get("heading"), str):
            s["heading"] = humanize.clean(s["heading"])
        s["paragraphs"] = [humanize.clean(str(p)) for p in s.get("paragraphs") or []]
        s["steps"] = [humanize.clean(str(p)) for p in s.get("steps") or []]
    return d


def reading_text(d):
    """독자가 읽는 글 부분만(지시문·예시·카드 요점 제외) — AI 티 검사용."""
    out = [str(d.get("intro", ""))]
    for s in d.get("sections") or []:
        out += [str(p) for p in s.get("paragraphs") or []]
    out += [str(d.get("tip", "")), str(d.get("closing", ""))]
    return "\n".join(out)


def check_shape(d, kind):
    """형식 문제를 찾는다. 문제가 없으면 빈 목록."""
    problems = []
    title = str(d.get("title", "")).strip()
    if not 8 <= len(title) <= 48:
        problems.append("제목 길이")
    sections = d.get("sections") or []
    if len(sections) < 3 or any(not s.get("heading") or not s.get("paragraphs") for s in sections):
        problems.append("소제목·문단 부족")
    if len(draft_text(d)) < 800:
        problems.append("본문이 너무 짧음")
    if kind == "howto" and len(str(d.get("prompt", "")).strip()) < 30:
        problems.append("복붙 지시문 없음")
    if len(d.get("card_points") or []) < 3:
        problems.append("요점 3개 없음")
    return problems


def find_claims(text, source_text=""):
    """근거(source_text)에 없는 숫자·마감·보장 표현을 찾는다."""
    norm = re.sub(r"[\s,]", "", source_text)
    found = [m.group() for m in CLAIM_PATTERN.finditer(text) if re.sub(r"[\s,]", "", m.group()) not in norm]
    found += [w for w in RISKY_WORDS if w in text and w not in source_text]
    return found


def write_post(kind, keyword=None, news=None, llm=call_gemini_json):
    """글 초안(dict)을 만든다. 두 번 안에 통과하지 못하면 None."""
    if kind == "howto":
        base, source = HOWTO_PROMPT.format(keyword=keyword), ""
    else:
        source = f"{news['title']}\n{news.get('summary', '')}"
        base = NEWS_PROMPT.format(source=source)
    extra = ""
    for attempt in (1, 2):
        try:
            d = llm(base + extra)
        except Exception as e:
            print(f"[네이버글] {attempt}차 작성 실패: {e}")
            continue
        if not isinstance(d, dict):
            continue
        d = clean_draft(d)
        problems = check_shape(d, kind)
        claims = find_claims(draft_text(d), source)
        if kind == "news":
            import auto_post
            claims = sorted(set(claims) | set(auto_post.find_fabrications(draft_text(d), source)))
        tells = humanize.find_tells(reading_text(d))
        if len(tells) >= 3:
            problems.append("AI 티가 많음(" + ", ".join(tells) + ")")
        if not problems and not claims:
            return d
        print(f"[네이버글] {attempt}차 초안 반려 — 형식: {problems} / 근거 없는 표현: {claims[:8]}")
        extra = ("\n\n[다시 쓰기] 앞선 초안에 문제가 있었다. "
                 + (f"형식 문제: {', '.join(problems)}. " if problems else "")
                 + (f"다음 표현은 근거가 없으니 빼라: {', '.join(claims[:12])}." if claims else ""))
    return None


# ── HTML 만들기 ──────────────────────────────────────────────

def _p(text):
    return "<p>" + html.escape(str(text).strip()).replace("\n", "<br>") + "</p>"


def _img(url):
    return f'<p><img src="{html.escape(url, quote=True)}" alt=""></p>' if url else ""


def load_cta():
    """수익 연결 문구(허브의 '수익·벤치마킹' 탭에서 저장). 없으면 빈 값."""
    try:
        with open(os.path.join(REPO, "style", "cta.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def build_html(d, kind, images):
    """네이버 글쓰기 창에 붙여넣을 HTML. images 는 공개 URL 목록(검색어 글 4장, 소식 글 1장)."""
    img = (list(images) + [""] * 4)[:4]
    parts = [_p(d["intro"]), _img(img[0])]
    for s in d["sections"]:
        parts.append(f"<h3>{html.escape(str(s['heading']).strip())}</h3>")
        parts += [_p(p) for p in s.get("paragraphs") or []]
        steps = [str(x).strip() for x in s.get("steps") or [] if str(x).strip()]
        if steps:
            parts.append("<p>" + "<br>".join(
                html.escape(f"{n}. {re.sub(r'^\d+[.)]\s*', '', x)}") for n, x in enumerate(steps, 1)) + "</p>")
    if kind == "howto":
        parts.append("<h3>그대로 복사해서 쓰는 지시문</h3>")
        if d.get("prompt_situation"):
            parts.append(_p(d["prompt_situation"]))
        parts.append("<blockquote>" + html.escape(str(d["prompt"]).strip()).replace("\n", "<br>") + "</blockquote>")
        parts.append(_img(img[1]))
        if d.get("example"):
            parts += ["<h3>이렇게 나와요</h3>", _p(d["example"]), _img(img[2])]
        if d.get("tip"):
            parts += ["<h3>한 가지 더</h3>", _p(d["tip"])]
    parts += [_p(d.get("closing", "")), _img(img[3]) if kind == "howto" else "", _p(FOOTER)]
    cta = load_cta()
    if cta.get("text"):
        parts.append(_p(cta["text"] + ("\n" + cta["url"] if cta.get("url") else "")))
    return "\n".join(x for x in parts if x and x != "<p></p>")


def build_text(d, kind):
    """이미지 없이 글자만 있는 판(붙여넣기 실패 때 쓰는 대비용)."""
    lines = [str(d["intro"]).strip(), ""]
    for s in d["sections"]:
        lines += [f"■ {str(s['heading']).strip()}"] + [str(p).strip() for p in s.get("paragraphs") or []]
        lines += [f"{n}. {str(x).strip()}" for n, x in enumerate(s.get("steps") or [], 1)] + [""]
    if kind == "howto":
        lines += ["■ 그대로 복사해서 쓰는 지시문", str(d["prompt"]).strip(), ""]
        if d.get("example"):
            lines += ["■ 이렇게 나와요", str(d["example"]).strip(), ""]
        if d.get("tip"):
            lines += ["■ 한 가지 더", str(d["tip"]).strip(), ""]
    lines += [str(d.get("closing", "")).strip(), "", FOOTER]
    cta = load_cta()
    if cta.get("text"):
        lines += ["", cta["text"]] + ([cta["url"]] if cta.get("url") else [])
    return "\n".join(lines)


# ── 이미지 ───────────────────────────────────────────────────

def make_images(d, kind, post_id, now):
    """카드 이미지를 만들어 파일 경로 목록을 돌려준다(JPEG)."""
    import card_news
    out_dir = os.path.join(REPO, "previews" if dry_run() else "cards")
    points = [{"title": str(p.get("title", ""))[:20], "desc": str(p.get("desc", ""))[:40]}
              for p in (d.get("card_points") or [])[:3]]
    if kind == "howto":
        pngs = card_news.generate_prompt_slides(
            {"series": "소담쌤 따라 하기", "headline": d["title"], "situation": d.get("prompt_situation", ""),
             "prompt": d["prompt"], "example": d.get("example") or "지시문을 넣으면 정리된 결과가 바로 나옵니다.",
             "tip": d.get("tip", "")}, out_dir, stem=f"naver_{post_id}")
    else:
        png = os.path.join(out_dir, f"naver_{post_id}.png")
        card_news.generate_news_card(
            {"category": "소담쌤 AI 소식", "headline": d["title"], "points": points,
             "date": f"{now.month}월 {now.day}일"}, png)
        pngs = [png]
    jpgs = []
    for p in pngs:
        jpgs.append(card_news.save_jpeg_copy(p))
        os.remove(p)
    return jpgs


# ── 저장 ─────────────────────────────────────────────────────

def _git(*args, check=True):
    return subprocess.run(["git", *args], cwd=REPO, check=check, capture_output=True, text=True)


def _commit_push(message, *paths):
    branch = os.environ.get("GITHUB_REF_NAME", "main")
    _git("config", "user.name", "auto-post-bot")
    _git("config", "user.email", "bot@users.noreply.github.com")
    _git("add", "-A", "--", *paths)
    if _git("diff", "--cached", "--quiet", check=False).returncode == 0:
        return
    _git("commit", "-m", message)
    _git("pull", "--rebase", "origin", branch, check=False)
    _git("push", "origin", branch)


def update_index(entry):
    path = os.path.join(QUEUE_DIR, "index.json")
    try:
        with open(path, encoding="utf-8") as f:
            items = json.load(f).get("items", [])
    except (OSError, ValueError):
        items = []
    items = [i for i in items if i["id"] != entry["id"]] + [entry]
    items = sorted(items, key=lambda i: i["date"])[-KEEP_IN_INDEX:]
    save_json(path, {"items": items})


def main():
    now = datetime.datetime.now(KST)
    date = now.date().isoformat()
    state = load_state()
    if date in state["dates"] and not dry_run():
        print(f"[네이버글] 오늘({date}) 글은 이미 준비되어 있습니다.")
        return 0
    if not config.GEMINI_API_KEY:
        print("[네이버글] GEMINI_API_KEY 가 없어 글을 만들 수 없습니다.")
        return 1
    # 글이 안 만들어지는 날 20분마다 계속 다시 시도하지 않도록 하루 3번까지만 해본다
    tried = state.setdefault("tried", {})
    if tried.get(date, 0) >= 3 and not dry_run():
        print(f"[네이버글] 오늘은 이미 {tried[date]}번 시도했습니다. 내일 다시 합니다.")
        return 0
    if not dry_run():
        state["tried"] = {date: tried.get(date, 0) + 1}
        save_json(STATE_FILE, state)
        _commit_push("봇: 네이버 글 시도 기록", "state/naver_posted.json")

    kind, keyword, news, draft = "howto", None, None, None
    if now.day % 2 == 1:
        try:
            import hotnews
            news = hotnews.pick_hot_item(used_titles=state["used_titles"], interest=hotnews.search_interest())
        except Exception as e:
            print(f"[네이버글] 소식 고르기 실패 → 검색어 글로 진행: {e}")
        if news:
            draft = write_post("news", news=news)
            kind = "news" if draft else "howto"
    if not draft:
        keyword = pick_keyword(state["used_keywords"])
        if not keyword:
            print("[네이버글] 쓸 만한 새 검색어가 없습니다. 오늘은 건너뜁니다.")
            return 0
        print(f"[네이버글] 검색어 선정: {keyword}")
        draft = write_post("howto", keyword=keyword)
    if not draft:
        print("[네이버글] 기준을 통과한 글이 없어 오늘은 만들지 않습니다.")
        return 0

    post_id = date
    files = make_images(draft, kind, post_id, now)
    if dry_run():
        urls = [os.path.relpath(f, REPO).replace(os.sep, "/") for f in files]
    else:
        import auto_post
        urls = auto_post._publish_card_files(files)

    tags = [re.sub(r"[#\s]", "", str(t)) for t in (draft.get("tags") or [])][:8]
    post = {
        "id": post_id, "date": date, "kind": kind, "keyword": keyword or "",
        "source_title": (news or {}).get("title", ""),
        "title": str(draft["title"]).strip(),
        "html": build_html(draft, kind, urls),
        "text": build_text(draft, kind),
        "tags": [t for t in tags if t],
        "images": urls,
        "created": now.isoformat(timespec="seconds"),
    }
    if dry_run():
        save_json(os.path.join(REPO, "previews", f"naver_{post_id}.json"), post)
        print(f"[네이버글][미리보기] 「{post['title']}」 → previews/naver_{post_id}.json")
        return 0

    save_json(os.path.join(QUEUE_DIR, f"{post_id}.json"), post)
    update_index({"id": post_id, "date": date, "title": post["title"], "kind": kind, "file": f"{post_id}.json"})
    state["dates"][date] = {"id": post_id, "title": post["title"], "kind": kind, "keyword": keyword or ""}
    if keyword:
        state["used_keywords"].append(keyword)
    state["used_titles"] = (state["used_titles"] + [post["title"], post["source_title"]])[-200:]
    state["used_titles"] = [t for t in state["used_titles"] if t]
    save_json(STATE_FILE, state)
    _commit_push("봇: 네이버 블로그 글 준비", "naver_queue", "state/naver_posted.json")
    print(f"[네이버글] 준비 완료: 「{post['title']}」 ({kind}, 이미지 {len(urls)}장)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
