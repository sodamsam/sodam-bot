# -*- coding: utf-8 -*-
"""소담봇 상태 확인 (매일 밤 GitHub Actions에서 실행).

"워크플로가 초록 체크인데 실제로는 아무것도 안 올라간" 상황을 잡아내기 위한 점검이다.
이상이 있으면 종료 코드 1로 끝나 GitHub이 실패 알림 메일을 보낸다.

점검 항목
  1) 오늘 아침 글이 실제로 발행됐는가 (state/posted.json의 오늘 날짜 키)  → 없으면 실패
  1-2) 오늘 저녁 코너 글이 발행됐는가 (날짜-evening 키)               → 없으면 경고만
  2) Threads 토큰이 살아있는가 (GET /me)                            → 안 되면 실패
  3) 댓글봇이 제때 도는가 (마지막 완료 실행이 너무 오래됐는지)       → 경고만
"""
import datetime
import json
import os
import sys
from zoneinfo import ZoneInfo

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import corners  # noqa: E402

KST = ZoneInfo("Asia/Seoul")
POSTED_FILE = os.path.join(ROOT, "state", "posted.json")
COMMENT_BOT_STALE_HOURS = 1.5


def check_posted_today(state, now_kst):
    """오늘 날짜로 발행 기록이 있는지. (ok, 메시지)"""
    today = now_kst.strftime("%Y-%m-%d")
    entry = (state.get("posted") or {}).get(today)
    if entry:
        return True, f"오늘({today}) 발행 확인: {entry.get('type', '?')} / post_id={entry.get('post_id', '?')}"
    return False, f"오늘({today}) 발행 기록이 없습니다. 발행 시간대(07~09시, 18~20시)가 모두 지났는데 글이 안 올라갔어요"


def check_evening_posted(state, now_kst):
    """오늘 저녁 코너 글이 발행됐는지. 아침 글을 놓쳐 저녁에 대신 발행한 날은 저녁 코너가 쉬는 게 정상이다."""
    today = now_kst.strftime("%Y-%m-%d")
    entry = (state.get("posted") or {}).get(f"{today}-evening")
    if entry and entry.get("type") in corners.EVENING_KINDS:
        return True, f"오늘({today}) 저녁 코너 발행 확인: {entry['type']}"
    if entry:
        return False, (f"오늘({today}) 저녁 코너를 만들지 못해 대체 글({entry.get('type')})이 나갔습니다. "
                       "반복되면 Actions 로그의 [저녁 코너] 줄을 확인하세요")
    return False, f"오늘({today}) 저녁 코너 발행 기록이 없습니다 (아침 글을 놓쳐 저녁에 대신 발행한 날이면 정상)"


def check_comment_bot_recency(last_run_iso, now_utc, max_hours=COMMENT_BOT_STALE_HOURS):
    """댓글봇 마지막 완료 실행이 max_hours 이내인지. (ok, 메시지)"""
    if not last_run_iso:
        return False, "댓글봇 실행 기록을 찾지 못했습니다"
    last = datetime.datetime.fromisoformat(last_run_iso.replace("Z", "+00:00"))
    hours = (now_utc - last).total_seconds() / 3600
    if hours <= max_hours:
        return True, f"댓글봇 마지막 실행 {hours:.1f}시간 전 (정상)"
    return False, (f"댓글봇 마지막 실행이 {hours:.1f}시간 전입니다. GitHub 무료 스케줄이 밀리고 있는 것이니 "
                   "cron-job.org에 댓글봇 호출(10분 간격)을 추가하세요 (docs/07_운영_자동화.md)")


def fetch_last_comment_bot_run(repo, gh_token):
    url = f"https://api.github.com/repos/{repo}/actions/workflows/comment_bot.yml/runs"
    headers = {"Accept": "application/vnd.github+json"}
    if gh_token:
        headers["Authorization"] = f"Bearer {gh_token}"
    r = requests.get(url, headers=headers, params={"per_page": 1, "status": "completed"}, timeout=30)
    r.raise_for_status()
    runs = r.json().get("workflow_runs", [])
    return runs[0]["created_at"] if runs else None


def check_threads_token(token):
    """GET /me 로 토큰이 실제로 통하는지. (ok, 메시지)"""
    if not token:
        return False, "THREADS_ACCESS_TOKEN이 비어 있습니다"
    r = requests.get("https://graph.threads.net/v1.0/me",
                     params={"fields": "id,username", "access_token": token}, timeout=30)
    if r.ok:
        return True, f"Threads 토큰 정상 (@{r.json().get('username', '?')})"
    return False, f"Threads 토큰 오류: {r.status_code} {r.text[:200]}"


def main():
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    now_kst = now_utc.astimezone(KST)
    failures, warnings, lines = [], [], []

    try:
        with open(POSTED_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        state = {}
    ok, msg = check_posted_today(state, now_kst)
    (lines if ok else failures).append(msg)

    ok, msg = check_evening_posted(state, now_kst)
    (lines if ok else warnings).append(msg)

    ok, msg = check_threads_token(os.environ.get("THREADS_ACCESS_TOKEN", ""))
    (lines if ok else failures).append(msg)

    repo = os.environ.get("GITHUB_REPOSITORY", "sodamsam/sodam-bot")
    try:
        last_run = fetch_last_comment_bot_run(repo, os.environ.get("GITHUB_TOKEN", ""))
        ok, msg = check_comment_bot_recency(last_run, now_utc)
    except Exception as e:
        ok, msg = False, f"댓글봇 실행 기록 조회 실패: {e}"
    (lines if ok else warnings).append(msg)

    for m in lines:
        print(f"[정상] {m}")
    for m in warnings:
        print(f"::warning::{m}")
    for m in failures:
        print(f"::error::{m}")

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("## 소담봇 상태 확인\n")
            f.writelines(f"- ✅ {m}\n" for m in lines)
            f.writelines(f"- ⚠️ {m}\n" for m in warnings)
            f.writelines(f"- ❌ {m}\n" for m in failures)

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
