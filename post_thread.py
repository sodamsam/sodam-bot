# -*- coding: utf-8 -*-
"""스레드 글 발행 스크립트.

GitHub Actions의 '글 발행' 워크플로우(수동 실행)에서 사용하거나,
로컬에서 직접 실행할 수 있습니다.

사용법:
  python post_thread.py "올릴 글 내용"
  또는 환경변수 POST_TEXT 에 글 내용을 넣고 실행
"""
import os
import sys
import config
import threads_api


def main():
    config.validate()
    text = ""
    if len(sys.argv) > 1:
        text = sys.argv[1]
    if not text:
        text = os.environ.get("POST_TEXT", "")
    text = text.strip()
    if not text:
        raise SystemExit("[오류] 발행할 글 내용이 비어 있습니다.")

    post_id = threads_api.publish_text(text)
    print(f"[발행 완료] post id: {post_id}")


if __name__ == "__main__":
    main()
