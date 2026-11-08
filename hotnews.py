# -*- coding: utf-8 -*-
"""'지금 가장 화제인, 꼭 알아야 할 AI 소식' 선정.

한국어 뉴스만 보면 같은 AI 소식을 여러 매체가 다룬 경우가 드물어 '인기'를 가를 수 없다. 반면 AI 신제품·기능·
정책 같은 큰 소식은 영어권 매체 여러 곳이 동시에 다룬다. 그래서

  1) 구글 뉴스(영어, 최근 48시간)에서 AI 관련 기사를 넉넉히 모아
  2) 제목이 비슷한 기사끼리 한 '소식'으로 묶고, 서로 다른 매체 수를 인기로 본다 (최소 MIN_OUTLETS곳)
  3) 일반 독자에게 쓸모 있는 소식(출시·기능·요금·정책·보안)만 남기고 소송·투자·인수·주가·인사 소식은 뺀다
  4) 이미 다룬 소식은 건너뛰고 가장 많은 매체가 다룬 소식 1건을 고른다

고른 소식은 auto_post의 브리핑 파이프라인(한국어 카드 + 글, 사실 검증)으로 넘어간다.
근거는 영어 기사 제목들이라, 검증은 숫자와 제품·회사 이름(영문)이 근거에 있는지로 한다(find_fabrications_en).
"""
import datetime
import re

import feedparser
import requests

MIN_OUTLETS = 4
MAX_AGE_HOURS = 48

QUERIES = [
    "OpenAI ChatGPT when:2d",
    "Anthropic Claude when:2d",
    "Google Gemini when:2d",
    "Microsoft Copilot when:2d",
    "Meta AI when:2d",
    "AI regulation OR AI law OR AI safety when:2d",
    "AI free new feature launch when:2d",
]

# 단어 단위로 맞춘다("husband"의 "ban"처럼 단어 일부가 우연히 겹쳐 통과하는 일을 막기 위해 \b 경계를 쓴다)
_AI_RE = re.compile(r"\b(ai|chatgpt|openai|claude|anthropic|gemini|copilot|llama|grok|deepseek|chatbots?|"
                    r"artificial intelligence|sora|perplexity)\b", re.I)
# 독자가 쓰거나 알아둘 만한 소식의 신호 (출시·기능·요금·정책·보안)
_POSITIVE_RE = re.compile(
    r"\b(launch\w*|introduc\w*|unveil\w*|rolls? out|rolling out|rollout|announc\w*|releas\w*|availab\w*|"
    r"free|pricing|prices?|updates?|features?|modes?|tools?|apps?|agents?|assistants?|voice|video|images?|"
    r"adds?|added|rules?|laws?|bans?|banned|regulat\w*|safety|privacy|scams?|deepfakes?|warns?|warning|"
    r"security|vulnerab\w*|bypass\w*)\b", re.I)
# 일반 독자와 무관한 소식: 소송·사건사고·범죄·투자·인수·주가·인사
_NEGATIVE_RE = re.compile(
    r"\b(sues?|sued|suing|suits?|lawsuits?|court|judge|trial|charged?|charges|indict\w*|arrest\w*|alleg\w*|"
    r"accus\w*|murder\w*|kill\w*|poison\w*|plot|suicid\w*|deaths?|dies|died|dead|abus\w*|assault\w*|"
    r"harass\w*|scandal\w*|shooting|crash\w*|fraud|"
    r"funding|valuations?|raises|acquir\w*|acquisitions?|ipo|stocks?|shares|layoffs?|cfo|ceo|earnings|revenue|"
    r"investors?|billions?|trillions?|stake|mergers?|hires?|joins|resign\w*|steps down|wall street|antitrust|"
    r"settlements?|fined?|probe|investigat\w*|bankrupt\w*|election\w*|jumps to|market cap)\b", re.I)
_STOP = {"the", "and", "for", "with", "that", "this", "from", "its", "new", "how", "why", "what", "are", "you",
         "your", "will", "can", "has", "have", "after", "over", "into", "about", "more", "says", "say"}


def _tokens(title):
    return {w.lower() for w in re.findall(r"[0-9A-Za-z]{3,}", title)} - _STOP


def fetch_global(query, limit=60, max_age_hours=MAX_AGE_HOURS):
    """구글 뉴스(영어) RSS에서 (제목, 매체, 발행시각) 목록을 가져온다. 발행 시각을 모르는 기사는 제외."""
    url = ("https://news.google.com/rss/search?q=" + requests.utils.quote(query)
           + "&hl=en-US&gl=US&ceid=US:en")
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


def audience_ok(title):
    """일반 독자가 쓰거나 알아둘 AI 소식인가 (AI 관련 + 쓸모 신호 있음 + 소송·사건·투자류 아님)."""
    if not _AI_RE.search(title):
        return False
    if _NEGATIVE_RE.search(title):
        return False
    return bool(_POSITIVE_RE.search(title))


def cluster(items):
    """제목이 비슷한 기사를 한 소식으로 묶는다. 반환: [{"tokens", "items"}...] (최신순으로 대표가 앞)."""
    clusters = []
    for it in sorted(items, key=lambda x: -x["published_ts"]):
        toks = _tokens(it["title"])
        for c in clusters:
            overlap = len(toks & c["tokens"])
            if overlap >= 3 and overlap / max(1, min(len(toks), len(c["tokens"]))) >= 0.45:
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
        if overlap >= 3 and overlap / max(1, min(len(tokens), len(ut))) >= 0.45:
            return True
    return False


def _outlet_label(outlets_by_count):
    names = [n for n, _ in outlets_by_count]
    if len(names) <= 2:
        return " · ".join(names)
    return f"{names[0]} · {names[1]} 외 {len(names) - 2}곳"


def build_item(c):
    """묶음 하나를 브리핑 파이프라인이 받는 소재(dict)로 만든다."""
    good = [i for i in c["items"] if audience_ok(i["title"])]
    rep = max(good, key=lambda i: i["published_ts"])
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
        "lang": "en",
        "outlets": len(ranked),
    }


def pick_hot_item(used_titles=(), fetcher=None, min_outlets=MIN_OUTLETS):
    """가장 많은 매체가 다룬 '쓸모 있는' AI 소식 1건. 기준을 넘는 소식이 없으면 None."""
    fetcher = fetcher or fetch_global
    items, seen = [], set()
    for q in QUERIES:
        try:
            got = fetcher(q)
        except Exception as e:      # 한 검색어가 실패해도 나머지로 진행
            print(f"[핫뉴스] '{q}' 수집 실패: {e}")
            continue
        for g in got:
            key = (g["title"], g["source"])      # 같은 제목을 여러 매체가 실은 경우도 매체별로 센다(인기 신호)
            if key not in seen:
                seen.add(key)
                items.append(g)

    candidates = []
    for c in cluster(items):
        outlets = {i["source"] for i in c["items"] if i["source"]}
        if len(outlets) < min_outlets:
            continue
        if not any(audience_ok(i["title"]) for i in c["items"]):
            continue
        if _already_covered(c["tokens"], used_titles):
            continue
        candidates.append((len(outlets), max(i["published_ts"] for i in c["items"]), c))
    if not candidates:
        print(f"[핫뉴스] 매체 {min_outlets}곳 이상이 다룬 쓸모 있는 소식 없음(검토 {len(items)}건) → 기존 방식으로 진행")
        return None
    candidates.sort(key=lambda t: (-t[0], -t[1]))
    item = build_item(candidates[0][2])
    print(f"[핫뉴스] 소재 선정: 「{item['title']}」 ({item['outlets']}개 매체가 보도)")
    return item


_ALLOWED_LATIN = {"ai", "app", "pc", "pdf", "sns", "url", "api"}


def find_fabrications_en(text, source_text):
    """영어 기사 제목을 근거로 쓴 한국어 글의 검증: 숫자와 영문 이름은 근거에 있는 것만 허용한다."""
    problems = []
    norm = lambda s: s.replace(",", "")
    src_nums = {norm(n) for n in re.findall(r"\d+(?:[.,]\d+)?", source_text)}
    for n in re.findall(r"\d+(?:[.,]\d+)?", text):
        if norm(n) not in src_nums:
            problems.append(n)
    low = source_text.lower()
    for w in set(re.findall(r"[A-Za-z][A-Za-z0-9\-]{2,}", text)):
        if w.lower() not in low and w.lower() not in _ALLOWED_LATIN:
            problems.append(w)
    return problems


def news_due(state, today, gap_days=2):
    """직전 뉴스 카드로부터 gap_days일 이상 지났는지 (하루 걸러 한 번까지만)."""
    last = (state.get("last_news_date") or "")
    if not last:
        return True
    d0 = datetime.date.fromisoformat(last)
    return (today - d0).days >= gap_days
