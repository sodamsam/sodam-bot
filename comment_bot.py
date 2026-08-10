# -*- coding: utf-8 -*-
"""댓글 감시 봇 (GitHub Actions가 10분마다 실행).

동작:
  1. 내 최근 게시물 N개의 댓글을 조회
  2. 아직 답장 안 한 댓글 중 키워드가 있으면
  3. 노션 DB에서 맞는 페이지를 찾아 대댓글로 링크 전송
  4. 답장한 댓글 ID를 state/replied.json에 기록 (중복 방지)
"""
import json
import os
import random
import time
import config
import threads_api
import notion_api


def load_state():
    try:
        with open(config.STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"replied_ids": []}


def save_state(state):
    os.makedirs(os.path.dirname(config.STATE_FILE), exist_ok=True)
    # 파일이 무한히 커지지 않도록 최근 2000개만 유지
    state["replied_ids"] = state["replied_ids"][-2000:]
    with open(config.STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def main():
    config.validate()
    state = load_state()
    replied = set(state["replied_ids"])

    me = threads_api.get_me()
    my_username = me.get("username", "")
    print(f"[봇 시작] 계정: @{my_username}")

    pages = notion_api.get_pages()
    print(f"[노션] 자료 {len(pages)}개 로드")
    if not pages:
        print("[안내] 노션 DB에 페이지가 없어 이번 회차는 종료합니다.")
        return

    posts = threads_api.get_recent_posts(limit=config.POSTS_TO_CHECK)
    print(f"[스레드] 최근 게시물 {len(posts)}개 확인")

    new_reply_count = 0
    reply_budget = config.MAX_REPLIES_PER_RUN  # 회차당 답장 상한 (스팸 패턴 회피)
    for post in posts:
        try:
            comments = threads_api.get_replies(post["id"])
        except Exception as e:
            print(f"  [오류] 댓글 조회 실패 (post {post['id']}): {e}")
            continue

        for c in comments:
            cid = c.get("id")
            if not cid or cid in replied:
                continue
            if c.get("username", "") == my_username:
                # 내 댓글(안내 대댓글 포함)은 건너뜀
                replied.add(cid)
                continue

            page = notion_api.match_page(c.get("text", ""), pages)
            if not page:
                continue

            if not page.get("public_url"):
                print(f"  [경고] '{page['title']}' 게시 꺼짐 → 전송 스킵. 노션에서 공유>게시를 켜주세요")
                continue

            if reply_budget <= 0:
                print("  [상한 도달] 이번 회차 답장 상한에 도달, 나머지는 다음 회차에 처리")
                break

            reply_text = config.REPLY_TEMPLATE.format(title=page["title"], link=page["public_url"])
            try:
                threads_api.publish_text(reply_text, reply_to_id=cid, wait_seconds=8)
                replied.add(cid)
                new_reply_count += 1
                reply_budget -= 1
                print(f"  [답장 완료] @{c.get('username')} ← {page['title']}")
                if reply_budget > 0:
                    time.sleep(random.randint(*config.REPLY_GAP_SECONDS))  # 답장 간 간격
            except Exception as e:
                print(f"  [오류] 답장 실패 (comment {cid}): {e}")

    state["replied_ids"] = list(replied)
    save_state(state)
    print(f"[봇 종료] 이번 회차 답장 수: {new_reply_count}")


if __name__ == "__main__":
    main()

    main()
