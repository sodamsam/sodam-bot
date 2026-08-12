# -*- coding: utf-8 -*-
"""CTA(마무리 문구) 로테이션 — OPEN(팔로우 유도) / LEAD(신청 유도) 전용.

타입별 문구 풀에서 랜덤으로 고르되, 바로 직전에 쓴 문구가 연속으로 다시
나오지 않게 한다. 직전 사용 인덱스는 state/last_cta.json에 저장한다.
"""
import json
import os
import random

LAST_CTA_FILE = os.path.join(os.path.dirname(__file__), "state", "last_cta.json")

CTA_FOLLOW = [
    "이런 프롬프트, 매일 아침 7시에 하나씩 올려요. 팔로우해두시면 알아서 찾아갑니다 👋",
    "복사해서 그냥 쓰세요. 출처 안 밝히셔도 됩니다 😊 매일 아침 7시, 여기서 하나씩 나눠요",
    "저장해두고 필요할 때 꺼내 쓰세요. 내일 것도 궁금하시면 팔로우 👋",
    "오늘 이거 하나만 써보셔도 30분은 버실 거예요. 매일 아침 7시에 하나씩 올립니다",
    "어렵게 설명 안 해요. 복사해서 붙여넣기만 하시면 됩니다. 팔로우해두세요 👋",
    "AI 처음이셔도 괜찮아요. 이런 거 매일 하나씩, 아침 7시에 올려둘게요",
]

CTA_LEAD = [
    "표로 정리한 전체본은 댓글에 '{keyword}' 남겨주시면 바로 보내드려요 📮",
    "전체 정리본이 필요하시면 댓글에 '{keyword}' 한 단어만 남겨주세요. 자동으로 보내드립니다",
    "한 장으로 정리해뒀어요. 댓글에 '{keyword}' 남겨주시면 링크 보내드릴게요",
]

_POOLS = {"open": CTA_FOLLOW, "lead": CTA_LEAD}


def _load_last():
    try:
        with open(LAST_CTA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_last(data):
    os.makedirs(os.path.dirname(LAST_CTA_FILE), exist_ok=True)
    with open(LAST_CTA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def pick_cta(post_type, keyword=None):
    """post_type("open"|"lead")에 맞는 CTA 문구를 하나 골라 돌려준다.

    직전에 이 타입에서 썼던 문구와는 다른 것을 고른다(문구 풀이 1개뿐이면 그냥 그걸 씀).
    LEAD는 {keyword} 자리에 오늘 뽑힌 글감의 키워드를 채워 넣는다.
    반환: (완성된 문구 텍스트, 이번에 고른 인덱스)
    """
    pool = _POOLS[post_type]
    last = _load_last()
    last_idx = last.get(post_type)

    candidates = [i for i in range(len(pool)) if i != last_idx] or list(range(len(pool)))
    idx = random.choice(candidates)

    last[post_type] = idx
    _save_last(last)

    text = pool[idx]
    if post_type == "lead":
        text = text.format(keyword=keyword or "")

    print(f"[CTA 선택] 유형={post_type} 인덱스={idx}")
    return text, idx
