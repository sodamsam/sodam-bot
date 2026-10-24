# -*- coding: utf-8 -*-
"""댓글 감시 봇 (GitHub Actions가 10분마다 실행).

동작:
  1. 내 최근 게시물 N개의 댓글을 조회
  2. 아직 답장 안 한 댓글 중 키워드가 있으면
  3. 노션 DB에서 맞는 페이지를 찾아 대댓글로 링크 전송
  4. 답장한 댓글 ID를 state/replied.json에 기록 (중복 방지)
"""
import csv
import json
import os
import random
import time
import config
import corners
import threads_api
import notion_api

# 처음 자료를 요청한 사람에게만 기본 안내문 뒤에 덧붙이는 문구.
# "팔로우해주세요" 같은 직접 요청이 아니라, 언제 뭐가 올라오는지 사실만 안내한다.
FIRST_TIME_NOTE_LINES = [
    "매일 아침 7시에 이런 프롬프트 하나씩 올리고 있어요 🙂",
    "이런 자료, 매일 아침 하나씩 새로 올라와요",
    "매일 아침 7시마다 새 프롬프트가 하나씩 올라옵니다",
]


def load_state():
    """답장 기록을 불러온다. replied_ids(댓글 ID 중복방지)와
    replied_usernames(첫 요청자 판별용) 두 목록을 관리한다."""
    try:
        with open(config.STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    data.setdefault("replied_ids", [])
    data.setdefault("replied_usernames", [])
    return data


def save_state(state):
    os.makedirs(os.path.dirname(config.STATE_FILE), exist_ok=True)
    # 파일이 무한히 커지지 않도록 최근 2000개만 유지
    state["replied_ids"] = state["replied_ids"][-2000:]
    state["replied_usernames"] = state["replied_usernames"][-2000:]
    with open(config.STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def load_post_keywords():
    """발행 기록(data/posted_log.csv)에서 {게시물 ID: 그 글의 키워드}를 읽는다.

    "신청"처럼 여러 자료가 같이 쓰는 단어를 댓글로 남겼을 때, 어느 글에 남긴 것인지에 따라
    올바른 자료를 고르기 위한 단서다. 파일이 없거나 깨져 있으면 빈 딕셔너리(=예전 동작).
    """
    path = os.path.join(os.path.dirname(os.path.abspath(config.STATE_FILE)), "..", "data", "posted_log.csv")
    mapping = {}
    try:
        with open(os.path.normpath(path), "r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                tid, kw = (row.get("thread_id") or "").strip(), (row.get("keyword") or "").strip()
                if tid and kw:
                    mapping[tid] = kw
    except (FileNotFoundError, OSError, csv.Error):
        pass
    return mapping


# 정보성 글(브리핑·정부지원 혜택)에는 노션 자료 자동답변을 달지 않는다.
# 이 글들에서 독자가 남기는 "신청"은 '지원사업 신청'이라는 뜻인데, 그대로 두면 허브(프롬프트 모음)가 엉뚱하게 답장된다.
NO_NOTION_REPLY_POST_TYPES = ("briefing", "benefit") + corners.EVENING_KINDS


def load_post_types():
    """발행 기록(data/posted_log.csv)에서 {게시물 ID: 글 종류(open/howto/briefing/benefit …)}를 읽는다."""
    path = os.path.join(os.path.dirname(os.path.abspath(config.STATE_FILE)), "..", "data", "posted_log.csv")
    mapping = {}
    try:
        with open(os.path.normpath(path), "r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                tid, kind = (row.get("thread_id") or "").strip(), (row.get("post_type") or "").strip()
                if tid and kind:
                    mapping[tid] = kind
    except (FileNotFoundError, OSError, csv.Error):
        pass
    return mapping


def build_reply_text(page, is_first_time):
    """자료 안내 대댓글 문구를 만든다. 처음 요청한 사람에게만 안내 한 줄을 더 붙인다."""
    text = config.REPLY_TEMPLATE.format(title=page["title"], link=page["public_url"])
    if is_first_time:
        text += "\n\n" + random.choice(FIRST_TIME_NOTE_LINES)
    return text


def main():
    config.validate()
    state = load_state()
    replied_list = list(state["replied_ids"])     # 순서를 유지해야 save_state의 "최근 2000개" 자르기가 맞다
    replied = set(replied_list)
    replied_usernames = list(state["replied_usernames"])
    replied_usernames_set = set(replied_usernames)

    me = threads_api.get_me()
    my_username = me.get("username", "")
    print(f"[봇 시작] 계정: @{my_username}")

    pages = notion_api.get_pages()
    print(f"[노션] 자료 {len(pages)}개 로드")
    if not pages:
        print("::warning::노션 DB에 페이지가 없어 이번 회차는 종료합니다.")
        return
    hub = notion_api.find_hub(pages)
    if not hub:
        print(f"::warning::허브 페이지 '{config.NOTION_HUB_TITLE}'를 노션 DB에서 찾지 못했습니다. "
              f"'{config.DEFAULT_KEYWORD}' 댓글에 자료를 보낼 수 없어요 (제목이 정확히 같은지 확인)")
    elif not hub.get("public_url"):
        print(f"::warning::허브 페이지 '{hub['title']}'가 웹에 게시되어 있지 않습니다. 노션에서 공유 > 게시를 켜주세요")

    post_keywords = load_post_keywords()
    post_types = load_post_types()
    posts = threads_api.get_recent_posts(limit=config.POSTS_TO_CHECK)
    print(f"[스레드] 최근 게시물 {len(posts)}개 확인")

    stats = {"댓글": 0, "이미 처리": 0, "내 댓글": 0, "키워드 없음": 0, "게시 꺼짐": 0, "상한 보류": 0, "발송 실패": 0}
    new_reply_count = 0
    reply_budget = config.MAX_REPLIES_PER_RUN  # 회차당 답장 상한 (스팸 패턴 회피)
    for post in posts:
        if post_types.get(str(post["id"])) in NO_NOTION_REPLY_POST_TYPES:
            continue  # 브리핑·혜택 글은 노션 자동답변 대상이 아님
        try:
            comments = threads_api.get_replies(post["id"])
        except Exception as e:
            print(f"[댓글 조회 스킵] 게시물 {post['id']} 조회 실패 → 건너뜀 ({e})")
            continue

        post_keyword = post_keywords.get(str(post["id"]))
        for c in comments:
            stats["댓글"] += 1
            cid = c.get("id")
            if not cid or cid in replied:
                stats["이미 처리"] += 1
                continue
            if c.get("username", "") == my_username:
                # 내 댓글(안내 대댓글 포함)은 건너뜀
                replied.add(cid)
                replied_list.append(cid)
                stats["내 댓글"] += 1
                continue

            page = notion_api.match_page(c.get("text", ""), pages, post_keyword=post_keyword)
            if not page:
                stats["키워드 없음"] += 1
                if config.DEFAULT_KEYWORD and config.DEFAULT_KEYWORD in (c.get("text") or ""):
                    # 신청 댓글인데 보낼 자료가 없는 경우는 조용히 넘기지 않고 로그에 크게 남긴다
                    print(f"  [미응답] @{c.get('username', '')}의 '{config.DEFAULT_KEYWORD}' 댓글에 보낼 자료를 찾지 못함 (comment {cid})")
                continue

            if not page.get("public_url"):
                stats["게시 꺼짐"] += 1
                print(f"::warning::'{page['title']}' 게시 꺼짐 → 전송 스킵. 노션에서 공유>게시를 켜주세요 (comment {cid})")
                continue

            if reply_budget <= 0:
                stats["상한 보류"] += 1
                continue

            username = c.get("username", "")
            is_first_time = bool(username) and username not in replied_usernames_set
            reply_text = build_reply_text(page, is_first_time)
            try:
                threads_api.publish_text(reply_text, reply_to_id=cid, wait_seconds=8)
                replied.add(cid)
                replied_list.append(cid)
                if username and username not in replied_usernames_set:
                    replied_usernames_set.add(username)
                    replied_usernames.append(username)
                new_reply_count += 1
                reply_budget -= 1
                print(f"  [답장 완료] @{username} ← {page['title']} (첫 요청={is_first_time})")
                if reply_budget > 0:
                    time.sleep(random.randint(*config.REPLY_GAP_SECONDS))  # 답장 간 간격
            except Exception as e:
                stats["발송 실패"] += 1
                print(f"::error::답장 실패 (comment {cid}): {e}")

    state["replied_ids"] = replied_list
    state["replied_usernames"] = replied_usernames
    save_state(state)
    if stats["상한 보류"]:
        print(f"[상한 도달] 이번 회차 답장 상한({config.MAX_REPLIES_PER_RUN}개)에 도달, {stats['상한 보류']}건은 다음 회차에 처리")
    print("[요약] " + ", ".join(f"{k} {v}" for k, v in stats.items()))
    print(f"[봇 종료] 이번 회차 답장 수: {new_reply_count}")


if __name__ == "__main__":
    main()
