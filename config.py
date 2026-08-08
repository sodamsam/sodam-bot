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
# 노션 페이지에 키워드가 지정 안 된 경우, 이 단어가 댓글에 있으면 최신 페이지를 보냄
DEFAULT_KEYWORD = os.environ.get("DEFAULT_KEYWORD", "신청")

# 자동 대댓글 문구. {title}=노션 페이지 제목, {url}=노션 링크
REPLY_TEMPLATE = os.environ.get(
    "REPLY_TEMPLATE",
    "요청하신 자료 보내드려요 😊\n📎 {title}\n{url}",
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

# 대댓글 문구 랜덤 변형 목록 ({title}, {url} 사용)
REPLY_TEMPLATES = [
    "요청하신 자료 보내드려요 😊\n📎 {title}\n{url}",
    "확인했어요! 아래 링크에서 받아가세요 🙌\n{title}\n{url}",
    "감사합니다 :) 요청하신 「{title}」 여기 있어요!\n{url}",
    "네! 자료 전달드립니다 📎\n{title}\n{url}",
    "댓글 감사해요 😊 「{title}」 링크 남겨드려요\n{url}",
    "여기요! 도움 되셨으면 좋겠어요 🙏\n📎 {title}\n{url}",
]

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
