# -*- coding: utf-8 -*-
"""'한국 사람들이 지금 많이 찾는 AI 소식' 선정 (v2 — 한국 뉴스 + 실제 검색어 기준).

v1은 영어권 매체 수를 인기로 봤다가 "미국 AI 안전센터", "Gemini for Healthcare" 같은 한국 4050 독자와 먼
소식이 나가 팔로워가 빠졌다. 그래서 기준을 바꿨다.

  1) 한국 구글 뉴스에서 '독자가 궁금해하는 질문'에 맞춘 검색어로 후보를 모은다
     (챗GPT·클로드·제미나이 새 기능/무료/요금제, 네이버·카카오 AI, AI 지원금·교육 모집, AI 정책)
  2) 네이버 자동완성(사람들이 실제로 치는 검색어: "클로드 요금제", "제미나이 무료" …)과 겹치면 가산점
  3) 개인·소상공인이 쓰거나 신청할 수 있는 소식에 점수, 기업용·투자·소송·정치·사건은 제외
  4) 마감이 이미 지난 신청·모집 소식은 제외
  5) 같은 소식은 하나로 묶어 다룬 매체 수도 점수에 반영, 이미 올린 소식은 건너뜀

고른 소식은 auto_post의 브리핑 파이프라인(카드 + 글, 사실 검증)으로 넘어간다.
"""
import datetime
import re

import feedparser
import requests

MAX_AGE_HOURS = 120
MIN_SCORE = 6

# 독자가 검색창에 치는 질문에 맞춘 후보 수집 검색어
QUERIES = [
    "챗GPT 새 기능 OR 업데이트 OR 요금제 OR 무료 when:5d",
    "클로드 앤트로픽 새 기능 OR 출시 OR 무료 OR 요금제 when:5d",
    "제미나이 구글 새 기능 OR 무료 OR 요금제 OR 출시 when:5d",
    "코파일럿 OR 퍼플렉시티 OR 딥시크 OR 뤼튼 무료 OR 새 기능 when:5d",
    "네이버 AI OR 클로바X OR 카카오 AI OR 카나나 출시 OR 서비스 when:5d",
    "AI 앱 무료 이벤트 OR 프로모션 OR 할인 OR 무료 제공 when:5d",
    "AI 지원금 신청 OR 접수 when:6d",
    "소상공인 AI 지원 OR 바우처 신청 when:6d",
    "AI 교육 무료 수강생 모집 when:6d",
    "중장년 OR 시니어 OR 직장인 AI 교육 모집 when:6d",
    "AI 자격증 OR 시험 접수 OR 공모전 접수 when:6d",
    "AI 기본법 OR AI 정책 개인 OR 소상공인 when:6d",
    "AI 가이드라인 OR 개인정보 AI 이용자 when:6d",
]

# 자동완성으로 '지금 사람들이 무엇을 묻는지' 확인할 대표 검색어: (제품 판별 정규식, 표시 이름, 자동완성에 칠 말)
SEEDS = [
    (r"챗\s?gpt|chatgpt", "챗GPT", "챗GPT"),
    (r"클로드|claude", "클로드", "클로드"),
    (r"제미나이|gemini", "제미나이", "제미나이"),
    (r"ai\s?활용", "AI 활용", "AI 활용"),
    (r"ai\s?지원금", "AI 지원금", "AI 지원금"),
]

_AI_RE = re.compile(r"(?<![A-Za-z])AI(?![A-Za-z])|인공지능|생성형|챗\s?GPT|ChatGPT|클로드|Claude|제미나이|Gemini|코파일럿|Copilot|딥시크|퍼플렉시티|뤼튼|클로바|카나나", re.I)
_PRODUCT_RE = re.compile(r"챗\s?GPT|ChatGPT|오픈AI|OpenAI|클로드|Claude|앤트로픽|제미나이|Gemini|코파일럿|Copilot|퍼플렉시티|딥시크|뤼튼|클로바|카나나|네이버|카카오|갤럭시 AI|AI 오피스", re.I)

# (태그, 정규식, 점수): 독자가 실제로 궁금해하는 것. 인스타 해시태그로도 쓴다.
TERMS = [
    ("AI무료", re.compile(r"무료|공짜|0원"), 3),
    ("AI요금제", re.compile(r"요금제|구독|가격|할인"), 3),
    ("AI이벤트", re.compile(r"프로모션|이벤트|혜택|경품"), 2),
    ("AI지원금", re.compile(r"지원금|지원사업|바우처|보조금"), 3),
    ("AI신청", re.compile(r"신청|접수|모집"), 3),
    ("AI교육", re.compile(r"교육|강좌|수강|캠프|클래스|특강"), 2),
    ("AI업데이트", re.compile(r"출시|공개|새 기능|신기능|업데이트|도입|오픈(?!\s?AI)|확대"), 2),
    ("AI활용법", re.compile(r"활용|사용법|가이드|프롬프트|꿀팁|하는 법|방법"), 2),
    ("AI정책", re.compile(r"AI 기본법|가이드라인|시행|개정|규제|제도"), 2),
]
_AUDIENCE_RE = re.compile(r"소상공인|자영업|개인|시니어|중장년|어르신|학생|직장인|주부|누구나|국민|시민|일반인|초보|우리 동네")
_ENTERPRISE_RE = re.compile(r"기업용|엔터프라이즈|B2B|대기업|임직원|부서별|제조업|공장|데이터센터|반도체|GPU|HBM|인프라|클라우드|API|SDK|개발자용|로보틱스")
# 의견·분석·전망·연구 기사는 독자가 바로 써볼 수 있는 정보가 아니다
_OPINION_RE = re.compile(r"해야\s?(한다|할|합니다)?|높여야|실효성|전망|예상|가능성|우려|논의|검토|진단|칼럼|사설|기고|연구|분석|조사 결과|설문|\d+\s?%")
# 한 지역 주민만 해당되는 소식("순천시, … 시민 75명 모집")은 대부분의 팔로워와 무관하다
_LOCAL_RE = re.compile(r"^[가-힣]{2,5}(시|군|구|도)(청|의회)?[,·\s]|^[가-힣]{2,4}(시|군|구)[가-힣]+(센터|재단|진흥원|공단|복지관|도서관|교육청)|[가-힣]{2,5}(시|군|구)\s?(시민|주민)|(시민|주민)\s?\d+\s?명")
# 독자와 무관하거나 위험한 소식은 점수와 상관없이 제외
_EXCLUDE_RE = re.compile(
    r"도박|카지노|토토|환금성|주가|급등|급락|목표주가|증시|코스피|코스닥|상장|인수합병|M&A|소송|사망|사고|성범죄|선거|"
    r"트럼프|백악관|전쟁|무인기|국방|군사|연예|결혼|이혼|부고|분양|부동산|투자 유치|펀딩|시리즈\s?[A-D]|IPO|"
    r"해킹|유출|랜섬|피싱|논란|폭로|고발|체포|구속|재판|사기")

PRODUCT_TAGS = [
    (re.compile(r"챗\s?GPT|ChatGPT|오픈AI|OpenAI", re.I), "챗GPT"),
    (re.compile(r"클로드|Claude|앤트로픽", re.I), "클로드"),
    (re.compile(r"제미나이|Gemini", re.I), "제미나이"),
    (re.compile(r"코파일럿|Copilot", re.I), "코파일럿"),
    (re.compile(r"네이버|클로바"), "네이버AI"),
    (re.compile(r"카카오|카나나"), "카카오AI"),
    (re.compile(r"딥시크"), "딥시크"),
]
_TOKEN_RE = re.compile(r"[0-9A-Za-z가-힣]{2,}")
_STOP = {"AI", "ai", "인공지능", "출시", "공개", "무료", "지원", "신청", "모집", "교육", "서비스", "새로운"}


def _tokens(title):
    return {w.lower() for w in _TOKEN_RE.findall(title)} - {w.lower() for w in _STOP}


def fetch_news_kr(query, limit=30, max_age_hours=MAX_AGE_HOURS):
    """한국 구글 뉴스 RSS에서 (제목, 매체, 발행시각) 목록. 발행 시각을 모르는 기사는 제외."""
    url = ("https://news.google.com/rss/search?q=" + requests.utils.quote(query)
           + "&hl=ko&gl=KR&ceid=KR:ko")
    feed = feedparser.parse(url)
    now = datetime.datetime.now(datetime.timezone.utc)
    out = []
    for e in feed.entries[:limit]:
        raw = e.get("title", "")
        m = re.search(r"\s-\s([^-]+)$", raw)
        source = m.group(1).strip() if m else ""
        title = re.sub(r"\s*-\s*[^-]+$", "", raw).strip()
        pp = e.get("published_parsed")
        if not pp or not title:
            continue
        dt = datetime.datetime(*pp[:6], tzinfo=datetime.timezone.utc)
        if (now - dt).total_seconds() > max_age_hours * 3600:
            continue
        out.append({"title": title, "source": source, "published_ts": dt.timestamp()})
    return out


def search_interest(fetcher=None):
    """네이버 자동완성에서 '사람들이 실제로 치는 검색어'를 읽는다. 반환: {표시이름: {연관 단어, ...}}. 실패하면 {}."""
    result = {}
    for seed_re, name, query in SEEDS:
        try:
            if fetcher:
                phrases = fetcher(query)
            else:
                r = requests.get(
                    "https://ac.search.naver.com/nx/ac",
                    params={"q": query, "con": "1", "frm": "nv", "ans": "2", "r_format": "json", "r_enc": "UTF-8",
                            "r_unicode": "0", "t_koreng": "1", "run": "2", "rev": "4", "q_enc": "UTF-8", "st": "100"},
                    headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
                data = r.json()
                phrases = [i[0] for i in data["items"][0]] if data.get("items") else []
        except Exception as e:
            print(f"[검색어] '{query}' 자동완성 조회 실패(건너뜀): {e}")
            continue
        words = set()
        for ph in phrases:
            rest = re.sub(seed_re, "", ph.lower(), count=1, flags=re.I).strip()
            words |= {w for w in rest.split() if len(w) >= 2}
        if words:
            result[name] = words
    return result


def expired(text, today):
    """'~까지/마감/접수' 문맥의 날짜가 이미 지났으면 True (지난 신청 소식을 올리지 않기 위해)."""
    if not re.search(r"마감|까지|접수|모집|신청", text):
        return False
    dates = []
    for mo, dy in re.findall(r"(\d{1,2})월\s?(\d{1,2})일", text):
        try:
            d = datetime.date(today.year, int(mo), int(dy))
        except ValueError:
            continue
        if d < today and (today - d).days > 180:
            d = d.replace(year=today.year + 1)       # 11월에 보이는 "1월 5일까지"는 내년이다
        dates.append(d)
    return bool(dates) and max(dates) < today


def _matched_terms(title):
    return [(tag, pts) for tag, rx, pts in TERMS if rx.search(title)]


def score_item(title, interest=None, outlets=1):
    """독자 관심도 점수와 근거. 제외 대상이면 (0, [...])."""
    if _EXCLUDE_RE.search(title) or not _AI_RE.search(title):
        return 0, []
    why = []
    score = 0
    if _PRODUCT_RE.search(title):
        score += 2
        why.append("제품")
    terms = _matched_terms(title)
    gained = min(sum(p for _, p in terms), 8)
    score += gained
    why += [t for t, _ in terms]
    if _AUDIENCE_RE.search(title):
        score += 2
        why.append("개인·소상공인 대상")
    if _ENTERPRISE_RE.search(title):
        score -= 3
        why.append("기업용(감점)")
    if _OPINION_RE.search(title):
        score -= 3
        why.append("의견·분석(감점)")
    if _LOCAL_RE.search(title):
        score -= 4
        why.append("특정 지역 한정(감점)")
    if outlets > 1:
        score += min(outlets - 1, 2)     # 보도자료 한 건이 여러 매체에 퍼진 경우가 많아 가산점을 작게 둔다
        why.append(f"매체 {outlets}곳")
    if interest:
        low = title.lower()
        bonus = 0
        for seed_re, name, _q in SEEDS:
            if re.search(seed_re, low, re.I):
                hit = [w for w in interest.get(name, ()) if w in low]
                if hit:
                    bonus += 2
                    why.append(f"검색어 '{name} {hit[0]}'")
        score += min(bonus, 4)
    return score, why


_SUPPORT_RE = re.compile(r"지원금|지원사업|도입 지원|바우처|보조금|교육|모집|신청|접수|자격증|공모전|정책|기본법|가이드라인|제도|시행|소상공인|무료 이벤트")


def kind_of(title):
    """'tool'(챗GPT·클로드·제미나이 등 도구 소식) 또는 'support'(지원·교육·정책 소식)."""
    if _PRODUCT_RE.search(title):
        return "tool"
    return "support" if _SUPPORT_RE.search(title) else "tool"


def opposite(kind):
    return {"tool": "support", "support": "tool"}.get(kind)


def cluster(items):
    """제목이 비슷한 기사를 한 소식으로 묶는다."""
    clusters = []
    for it in sorted(items, key=lambda x: -x["published_ts"]):
        toks = _tokens(it["title"])
        for c in clusters:
            overlap = len(toks & c["tokens"])
            if overlap >= 3 and overlap / max(1, min(len(toks), len(c["tokens"]))) >= 0.4:
                c["items"].append(it)
                c["tokens"] |= toks
                break
        else:
            clusters.append({"tokens": set(toks), "items": [it]})
    return clusters


def _already_covered(tokens, used_titles):
    for t in used_titles:
        ut = _tokens(t)
        overlap = len(tokens & ut)
        if overlap >= 3 and overlap / max(1, min(len(tokens), len(ut))) >= 0.4:
            return True
    return False


def _outlet_label(ranked):
    names = [n for n, _ in ranked]
    if len(names) <= 2:
        return " · ".join(names)
    return f"{names[0]} · {names[1]} 외 {len(names) - 2}곳"


def hashtags(text, limit=8):
    """소식 내용에서 인스타 해시태그를 만든다(사람들이 검색하는 말 위주). 항상 #소담AI랩 포함."""
    tags = []
    for rx, name in PRODUCT_TAGS:
        if rx.search(text):
            tags.append(name)
    for tag, rx, _ in TERMS:
        if rx.search(text) and tag not in tags:
            tags.append(tag)
    if re.search(r"소상공인|자영업", text):
        tags.append("소상공인AI")
    if re.search(r"시니어|중장년|어르신", text):
        tags.append("시니어AI")
    tags = tags[:limit - 1] + ["소담AI랩"]
    return " ".join("#" + t for t in tags)


def build_item(c, score, why):
    rep = max((i for i in c["items"] if score_item(i["title"])[0] > 0), key=lambda i: score_item(i["title"])[0],
              default=c["items"][0])
    counts = {}
    for i in c["items"]:
        if i["source"]:
            counts[i["source"]] = counts.get(i["source"], 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: -kv[1])
    others, seen = [], {rep["title"]}
    for i in sorted(c["items"], key=lambda x: -x["published_ts"]):
        if i["title"] not in seen and len(others) < 4:
            seen.add(i["title"])
            others.append(i["title"][:140])
    return {
        "title": rep["title"],
        "summary": " / ".join(others),
        "published_ts": max(i["published_ts"] for i in c["items"]),
        "source": _outlet_label(ranked),
        "related": [],
        "lang": "ko",
        "outlets": len(ranked),
        "score": score,
        "why": why,
        "tags": hashtags(rep["title"]),
        "kind": kind_of(rep["title"]),
    }


def pick_hot_item(used_titles=(), fetcher=None, interest=None, today=None, min_score=MIN_SCORE, prefer_kind=None):
    """독자 관심도 점수가 가장 높은 소식 1건. 기준을 넘는 소식이 없으면 None.

    prefer_kind('tool'/'support')를 주면 그 종류가 1위와 4점 이내로 비슷할 때 그쪽을 먼저 고른다(도구 소식과
    지원·정책 소식이 번갈아 나가게 하려는 것).
    """
    fetcher = fetcher or fetch_news_kr
    today = today or datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).date()
    if interest is None:
        interest = search_interest()

    items, seen = [], set()
    for q in QUERIES:
        try:
            got = fetcher(q)
        except Exception as e:      # 한 검색어가 실패해도 나머지로 진행
            print(f"[소식] '{q[:30]}' 수집 실패: {e}")
            continue
        for g in got:
            key = (g["title"], g["source"])
            if key not in seen:
                seen.add(key)
                items.append(g)

    candidates = []
    skipped_expired = 0
    for c in cluster(items):
        if _already_covered(c["tokens"], used_titles):
            continue
        outlets = len({i["source"] for i in c["items"] if i["source"]})
        best = None
        for i in c["items"]:
            if expired(f"{i['title']} {i.get('summary', '')}", today):
                continue
            s, why = score_item(i["title"], interest=interest, outlets=outlets)
            if best is None or s > best[0]:
                best = (s, why)
        if best is None:
            skipped_expired += 1
            continue
        if best[0] >= min_score:
            candidates.append((best[0], max(i["published_ts"] for i in c["items"]), c, best[1]))

    if not candidates:
        print(f"[소식] 기준({min_score}점) 이상인 소식 없음(검토 {len(items)}건, 마감 지난 것 {skipped_expired}건 제외) → 기존 방식으로 진행")
        return None
    candidates.sort(key=lambda t: (-t[0], -t[1]))
    top = candidates[0][0]
    if prefer_kind:
        near = [t for t in candidates if t[0] >= top - 4 and
                kind_of(max(t[2]["items"], key=lambda i: score_item(i["title"])[0])["title"]) == prefer_kind]
        if near:
            candidates = near
    score, _, c, why = candidates[0]
    item = build_item(c, score, why)
    print(f"[소식] 선정: 「{item['title']}」 점수 {score} ({', '.join(why)})")
    return item


def news_due(state, today, gap_days=2):
    """직전 뉴스 카드로부터 gap_days일 이상 지났는지 (하루 걸러 한 번까지만)."""
    last = (state.get("last_news_date") or "")
    if not last:
        return True
    d0 = datetime.date.fromisoformat(last)
    return (today - d0).days >= gap_days
