# -*- coding: utf-8 -*-
"""글감 큐 분리 (OPEN / LEAD) — auto_post.PROMPT_BANK를 두 개의 큐로 나눠 관리한다.

배경:
  예전에는 prompt_seq 하나로 60개 글감(office/biz/life 각 20개)을 순서대로 돌렸다.
  이제 요일에 따라 OPEN(프롬프트 전문 공개)과 LEAD(맛보기+신청 유도)로 나뉘는데,
  LEAD는 노션 자료 DB에 실제로 등록되고 "웹에 게시"까지 된 키워드만 써야 신청받을
  자료가 있는 상태가 된다. 그래서 큐 자체를 두 개로 쪼갠다.

  두 큐 모두 office→biz→life 순환 순서(ordered_topics)는 그대로 유지한다.
"""

# ── 큐 구성 ──────────────────────────────────────────────────

def ordered_topics(prompt_bank, areas):
    """PROMPT_BANK를 office→biz→life가 한 바퀴씩 돌아가는 순서로 나열한다.

    기존 prompt_seq가 진행되던 순서(area = seq % 3, idx = seq // 3)와 동일한 순서를 만든다.
    반환: [{"area": str, "topic": str, "keyword": str}, ...]
    """
    counts = {area: len(prompt_bank[area]) for area in areas}
    max_len = max(counts.values()) if counts else 0
    result = []
    for idx in range(max_len):
        for area in areas:
            if idx < counts[area]:
                topic, keyword = prompt_bank[area][idx]
                result.append({"area": area, "topic": topic, "keyword": keyword})
    return result


def build_queues(prompt_bank, areas, lead_keywords, published_keywords):
    """전체 글감을 LEAD 큐(노션에 등록+게시된 키워드)와 OPEN 큐(나머지)로 나눈다.

    이미 발행된 키워드(published_keywords)는 두 큐 모두에서 제외한 "잔여" 목록도 함께 반환한다.
    """
    all_topics = ordered_topics(prompt_bank, areas)
    lead_all = [t for t in all_topics if t["keyword"] in lead_keywords]
    open_all = [t for t in all_topics if t["keyword"] not in lead_keywords]
    lead_remaining = [t for t in lead_all if t["keyword"] not in published_keywords]
    open_remaining = [t for t in open_all if t["keyword"] not in published_keywords]
    return {
        "lead_all": lead_all,
        "open_all": open_all,
        "lead_remaining": lead_remaining,
        "open_remaining": open_remaining,
    }


# ── 큐에서 다음 글감 뽑기 ─────────────────────────────────────

def pick_from_queue(queue_all, queue_remaining, seq, queue_label):
    """큐에서 다음 글감 1개를 뽑는다.

    아직 발행 안 한 글감이 남아있으면 그중 맨 앞(순환 순서상 다음 차례)을 준다.
    하나도 안 남았으면 큐를 처음부터 다시 돌리며 경고 로그를 남긴다(글감 재사용).
    큐 자체가 비어있으면(office/biz/life 어디에도 해당 글감이 없음) None을 반환한다.

    반환: (뽑힌 글감 dict | None, 이번 픽 이후 남은 개수, 큐를 다시 돌린 것인지 여부)
    """
    if queue_remaining:
        chosen = queue_remaining[0]
        left_after = len(queue_remaining) - 1
        return chosen, left_after, False

    if not queue_all:
        return None, 0, True

    print(f"[경고] {queue_label} 큐가 비어 처음부터 다시 돌립니다. Notion 자료 추가 필요")
    idx = seq % len(queue_all)
    chosen = queue_all[idx]
    return chosen, len(queue_all), True


# ── 상태 마이그레이션 ────────────────────────────────────────

def migrate_legacy_published_keywords(state, prompt_bank, areas):
    """예전 prompt_seq 방식으로 이미 발행된 글감의 키워드를 published_keywords 집합에 채워 넣는다.

    한 번만 실행하면 되므로 state에 완료 플래그(published_keywords_migrated)를 남긴다.
    기존 prompt_seq 값 자체는 롤백 대비를 위해 건드리지 않고 그대로 둔다.
    """
    if state.get("published_keywords_migrated"):
        return

    legacy_seq = state.get("prompt_seq", 0)
    topics = ordered_topics(prompt_bank, areas)
    published = set(state.get("published_keywords", []))
    for i in range(min(legacy_seq, len(topics))):
        published.add(topics[i]["keyword"])

    state["published_keywords"] = sorted(published)
    state["published_keywords_migrated"] = True
    print(f"[큐 마이그레이션] 기존 prompt_seq={legacy_seq} 기준 발행완료 키워드 {sorted(published)} 반영")


# ── 글감 목록 문서 생성 (docs/글감_발행목록.md) ────────────────

def _material_status(keyword, lead_keywords, registered_keywords):
    """이 키워드의 노션 자료 상태를 사람이 읽을 문구로 돌려준다."""
    if keyword in lead_keywords:
        return "✅ 게시됨 (LEAD 가능)"
    if keyword in registered_keywords:
        return "🔶 등록만 됨 (미게시)"
    return "❌ 미등록"


def _render_queue_table(items, lead_keywords, registered_keywords):
    lines = ["| 순서 | 영역 | 글감 | 키워드 | 노션 자료 상태 |", "|---|---|---|---|---|"]
    for i, t in enumerate(items, start=1):
        status = _material_status(t["keyword"], lead_keywords, registered_keywords)
        lines.append(f"| {i} | {t['area']} | {t['topic']} | {t['keyword']} | {status} |")
    return "\n".join(lines)


def render_topic_doc(prompt_bank, areas, lead_keywords, registered_keywords,
                      published_keywords, generated_at, published_log=None):
    """OPEN/LEAD 큐 현황을 docs/글감_발행목록.md용 마크다운 문자열로 만든다.

    published_log: [{"date_kst", "post_type", "topic", "keyword"}, ...] (data/posted_log.csv에서 읽어온 것).
    """
    queues = build_queues(prompt_bank, areas, lead_keywords, published_keywords)
    published_log = published_log or []

    parts = [
        "# 글감 발행 목록",
        "",
        f"_자동 생성됨 — 마지막 갱신: {generated_at}_",
        "",
        f"## LEAD 큐 (신청 유도, 수·토·일) — 잔여 {len(queues['lead_remaining'])}개 / 전체 {len(queues['lead_all'])}개",
        "",
        _render_queue_table(queues["lead_all"], lead_keywords, registered_keywords),
        "",
        f"## OPEN 큐 (프롬프트 공개, 월·화·목·금) — 잔여 {len(queues['open_remaining'])}개 / 전체 {len(queues['open_all'])}개",
        "",
        _render_queue_table(queues["open_all"], lead_keywords, registered_keywords),
        "",
        "## 발행 완료",
        "",
    ]

    if published_log:
        parts.append("| 날짜 | 타입 | 글감 | 키워드 |")
        parts.append("|---|---|---|---|")
        for row in sorted(published_log, key=lambda r: r.get("date_kst", ""), reverse=True):
            parts.append(
                f"| {row.get('date_kst', '')} | {row.get('post_type', '')} "
                f"| {row.get('topic', '')} | {row.get('keyword', '')} |"
            )
    else:
        parts.append("_아직 발행 기록이 없습니다._")

    parts.append("")
    return "\n".join(parts)
