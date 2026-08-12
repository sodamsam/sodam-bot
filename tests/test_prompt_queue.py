# -*- coding: utf-8 -*-
"""글감 큐 분리(OPEN/LEAD) 로직 테스트."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import prompt_queue

TEST_BANK = {
    "office": [("회의록 정리", "회의"), ("메일 답장", "메일"), ("보고서 요약", "요약")],
    "biz": [("상세페이지", "상세"), ("리뷰 답글", "리뷰"), ("홍보 문구", "홍보")],
    "life": [("식단 짜기", "식단"), ("장보기", "장보기"), ("숙제", "숙제")],
}
TEST_AREAS = ["office", "biz", "life"]


def test_ordered_topics_cycles_office_biz_life():
    """office→biz→life가 한 바퀴씩 돌아가며 나열된다 (기존 prompt_seq 순서와 동일)."""
    topics = prompt_queue.ordered_topics(TEST_BANK, TEST_AREAS)
    keywords = [t["keyword"] for t in topics]
    assert keywords == ["회의", "상세", "식단", "메일", "리뷰", "장보기", "요약", "홍보", "숙제"]


def test_build_queues_splits_by_lead_keyword():
    """노션에 등록+게시된 키워드만 LEAD 큐로, 나머지는 전부 OPEN 큐로 들어간다."""
    lead_keywords = {"회의", "상세", "식단"}
    queues = prompt_queue.build_queues(TEST_BANK, TEST_AREAS, lead_keywords, published_keywords=set())

    assert [t["keyword"] for t in queues["lead_all"]] == ["회의", "상세", "식단"]
    assert [t["keyword"] for t in queues["open_all"]] == ["메일", "리뷰", "장보기", "요약", "홍보", "숙제"]
    assert queues["lead_remaining"] == queues["lead_all"]
    assert queues["open_remaining"] == queues["open_all"]


def test_build_queues_excludes_published_keywords():
    """이미 발행된 키워드는 두 큐의 '잔여' 목록에서 빠진다 (전체 목록에는 남아있음)."""
    lead_keywords = {"회의", "상세", "식단"}
    published = {"회의", "메일"}
    queues = prompt_queue.build_queues(TEST_BANK, TEST_AREAS, lead_keywords, published_keywords=published)

    assert [t["keyword"] for t in queues["lead_remaining"]] == ["상세", "식단"]
    assert [t["keyword"] for t in queues["open_remaining"]] == ["리뷰", "장보기", "요약", "홍보", "숙제"]
    # 전체 목록은 발행 여부와 무관하게 그대로 유지된다
    assert [t["keyword"] for t in queues["lead_all"]] == ["회의", "상세", "식단"]


def test_pick_from_queue_returns_next_remaining_item():
    """남은 글감이 있으면 순서상 맨 앞 것을 뽑는다."""
    remaining = [{"keyword": "상세"}, {"keyword": "식단"}]
    all_items = [{"keyword": "회의"}, {"keyword": "상세"}, {"keyword": "식단"}]

    chosen, left, wrapped = prompt_queue.pick_from_queue(all_items, remaining, seq=5, queue_label="LEAD")
    assert chosen == {"keyword": "상세"}
    assert left == 1
    assert wrapped is False


def test_pick_from_queue_wraps_when_remaining_empty():
    """잔여가 0이면 전체 큐에서 seq % len으로 다시 뽑고, 재사용했다는 표시(wrapped=True)를 준다."""
    all_items = [{"keyword": "회의"}, {"keyword": "상세"}, {"keyword": "식단"}]

    chosen, left, wrapped = prompt_queue.pick_from_queue(all_items, [], seq=4, queue_label="OPEN")
    assert chosen == all_items[4 % 3]
    assert wrapped is True


def test_pick_from_queue_returns_none_when_queue_itself_empty():
    """큐 자체(office/biz/life 어디에도 해당 키워드가 없음)가 비어있으면 None."""
    chosen, left, wrapped = prompt_queue.pick_from_queue([], [], seq=0, queue_label="LEAD")
    assert chosen is None
    assert wrapped is True


def test_migrate_legacy_published_keywords_uses_prompt_seq():
    """기존 prompt_seq=3이면 처음 3개(회의/상세/식단)가 published_keywords에 들어간다."""
    state = {"prompt_seq": 3}
    prompt_queue.migrate_legacy_published_keywords(state, TEST_BANK, TEST_AREAS)

    assert state["published_keywords"] == ["상세", "식단", "회의"]
    assert state["published_keywords_migrated"] is True
    # prompt_seq 자체는 롤백 대비를 위해 건드리지 않는다
    assert state["prompt_seq"] == 3


def test_migrate_is_idempotent():
    """이미 마이그레이션된 state는 다시 실행해도 덮어쓰지 않는다."""
    state = {
        "prompt_seq": 3,
        "published_keywords": ["회의", "상세", "식단", "메일"],  # 이후 실제 발행으로 늘어난 상태
        "published_keywords_migrated": True,
    }
    prompt_queue.migrate_legacy_published_keywords(state, TEST_BANK, TEST_AREAS)
    assert state["published_keywords"] == ["회의", "상세", "식단", "메일"]
