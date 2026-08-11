# -*- coding: utf-8 -*-
"""환경변수 설정 로더.

GitHub Actions에서는 Secrets → 환경변수로 주입됩니다.
로컬 테스트 시에는 .env 파일을 만들어 사용할 수 있습니다.
"""
import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ── 필수 값 (GitHub Secrets에 등록) ──────────────────────────
THREADS_ACCESS_TOKEN = os.environ.get("THREADS_ACCESS_TOKEN", "")
NOTION_TOKEN = os.environ.get("NOTION_TOKEN", "")
NOTION_DATABASE_ID = os.environ.get("NOTION_DATABASE_ID", "")

# ── 선택 값 (기본값 있음) ────────────────────────────────────
# 구체적인 자료 키워드가 매칭되지 않고 이 단어만 댓글에 있으면 허브 페이지(NOTION_HUB_TITLE)를 보냄
DEFAULT_KEYWORD = os.environ.get("DEFAULT_KEYWORD", "신청")

# 기본 키워드만 매칭됐을 때 대신 보낼 허브 페이지의 노션 제목.
# 이 이름과 정확히 같은 페이지를 노션 DB에서 찾아 링크를 보낸다. 못 찾으면 아무것도 안 보냄
NOTION_HUB_TITLE = os.environ.get("NOTION_HUB_TITLE", "AI 프롬프트 5종 모음")

# 자동 대댓글 기본 문구. {title}=자료 제목, {link}=노션 공개 게시 링크(public_url)
# "팔로우해주세요" 같은 직접 요청 문구는 넣지 않는다 — 요청이 아니라 안내가 원칙.
# 처음 요청하는 사람에게만 comment_bot.FIRST_TIME_NOTE_LINES 중 한 줄이 추가로 붙는다.
REPLY_TEMPLATE = os.environ.get(
    "REPLY_TEMPLATE",
    "{title} 여기 있습니다 👇\n"
    "{link}",
)

# 최근 게시물 몇 개까지 댓글을 감시할지
POSTS_TO_CHECK = int(os.environ.get("POSTS_TO_CHECK", "5"))

# 노션 DB에서 키워드를 읽을 속성 이름
NOTION_KEYWORD_PROP = os.environ.get("NOTION_KEYWORD_PROP", "키워드")

# ── 자동 글 발행 (Gemini 무료 API) ───────────────────────────
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")

# ── 스팸 패턴 회피 (댓글 봇) ─────────────────────────────────
# 한 회차(10분)에 답장할 최대 개수 — 몰아서 발사 방지
MAX_REPLIES_PER_RUN = int(os.environ.get("MAX_REPLIES_PER_RUN", "4"))

# 답장 사이 대기 시간 범위(초)
REPLY_GAP_SECONDS = (20, 50)

THREADS_API_BASE = "https://graph.threads.net/v1.0"
STATE_FILE = os.path.join(os.path.dirname(__file__), "state", "replied.json")


def validate():
    missing = [
        name for name, val in [
            ("THREADS_ACCESS_TOKEN", THREADS_ACCESS_TOKEN),
            ("NOTION_TOKEN", NOTION_TOKEN),
            ("NOTION_DATABASE_ID", NOTION_DATABASE_ID),
        ] if not val
    ]
    if missing:
        raise SystemExit(f"[설정 오류] 다음 환경변수가 비어 있습니다: {', '.join(missing)}")
