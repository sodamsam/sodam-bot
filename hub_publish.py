# -*- coding: utf-8 -*-
"""소담 허브가 넘긴 예약 글 발행기.

내 PC의 '소담 허브'가 hub_queue/<id>.json 을 이 저장소에 올리면,
GitHub Actions가 이 스크립트를 돌려 시각이 된 글을 스레드·인스타에 올린다.

  - 한 글은 딱 한 번만 시도한다. 시도 직전에 hub_queue/done/ 으로 옮겨 커밋·푸시부터 하므로
    중간에 멈춰도 같은 글이 두 번 올라가지 않는다(실패하면 허브에서 다시 보내면 된다).
  - DRY_RUN=yes 면 아무것도 올리지 않고 무엇을 올릴지만 출력한다.

예약 파일 형식:
  {"id": "...", "title": "...", "publish_at": "2026-11-16T19:00" 또는 "",
   "threads": {"parts": ["본문", "이어지는 댓글"...], "tag": "AI활용"},
   "instagram": {"headline": "카드 제목", "caption": "글",
                 "points": [{"title": "요점", "desc": "설명"}]}}   ← points 는 최대 3개, 없어도 됨
"""
import datetime
import glob
import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.abspath(__file__))
QUEUE_DIR = os.path.join(REPO, "hub_queue")
DONE_DIR = os.path.join(QUEUE_DIR, "done")
KST = datetime.timezone(datetime.timedelta(hours=9))


def dry_run():
    return os.environ.get("DRY_RUN", "").strip().lower() in ("1", "y", "yes", "true")


def is_due(job, now):
    """publish_at 이 비었거나 지났으면 올릴 차례다. 형식이 이상하면 올리지 않는다."""
    raw = (job.get("publish_at") or "").strip()
    if not raw:
        return True
    try:
        when = datetime.datetime.fromisoformat(raw)
    except ValueError:
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=KST)
    return now >= when


def validate(job):
    """올리기 전에 형식을 확인한다. 문제가 있으면 이유(문자열), 없으면 None."""
    t, i = job.get("threads"), job.get("instagram")
    if not t and not i:
        return "올릴 채널이 없습니다"
    if t:
        parts = t.get("parts") or []
        if not parts or any(not str(p).strip() for p in parts):
            return "스레드 글이 비어 있습니다"
        if any(len(str(p).strip()) > 500 for p in parts):
            return "스레드 글 한 조각이 500자를 넘습니다"
    images = job.get("images") or []
    if images and (len(images) > 10 or any(not str(u).startswith("https://") for u in images)):
        return "이미지는 https 주소로 10장까지만 쓸 수 있습니다"
    if i:
        if not str(i.get("caption", "")).strip() or not (images or str(i.get("headline", "")).strip()):
            return "인스타 글 또는 카드(제목·이미지)가 비어 있습니다"
        if len(i["caption"]) > 2200:
            return "인스타 글이 2,200자를 넘습니다"
    return None


def _git(*args, check=True):
    return subprocess.run(["git", *args], cwd=REPO, check=check, capture_output=True, text=True)


def _commit_push(message, *paths):
    branch = os.environ.get("GITHUB_REF_NAME", "main")
    _git("config", "user.name", "auto-post-bot")
    _git("config", "user.email", "bot@users.noreply.github.com")
    _git("add", "-A", "--", *paths)
    if _git("diff", "--cached", "--quiet", check=False).returncode == 0:
        return
    _git("commit", "-m", message)
    _git("pull", "--rebase", "origin", branch, check=False)
    _git("push", "origin", branch)


def claim(path, job):
    """예약 파일을 done/ 으로 옮기고 먼저 푸시한다 — 여기서 실패하면 발행을 시작하지 않는다."""
    os.makedirs(DONE_DIR, exist_ok=True)
    done_path = os.path.join(DONE_DIR, os.path.basename(path))
    job["results"] = {}
    job["started"] = datetime.datetime.now(KST).isoformat(timespec="seconds")
    with open(done_path, "w", encoding="utf-8") as f:
        json.dump(job, f, ensure_ascii=False, indent=2)
    os.remove(path)
    _commit_push("허브: 예약 글 발행 시작", "hub_queue")
    return done_path


def make_card_urls(job):
    """글에 붙일 이미지 주소 목록. job["images"](이미 공개된 카드들)가 있으면 그대로 쓰고,
    없으면 제목·요점으로 소담쌤 카드 한 장을 만든다. 둘 다 없으면 빈 목록."""
    if job.get("images"):
        return list(job["images"])[:10]
    url = make_card_url(job)
    return [url] if url else []


def make_card_url(job):
    """글에 붙일 소담쌤 카드 이미지를 만들어 공개 주소를 돌려준다. 카드 정보가 없으면 None."""
    card = job.get("card") or job.get("instagram") or {}
    headline = str(card.get("headline", "")).strip()
    if not headline:
        return None
    import auto_post
    import card_news
    today = datetime.datetime.now(KST)
    png = os.path.join(REPO, "cards", f"hub_{job['id']}.png")
    card_news.generate_news_card(
        {"category": "소담쌤 AI 한 장", "headline": headline,
         "points": (card.get("points") or [])[:3],
         "date": f"{today.month}월 {today.day}일"}, png)
    jpg = card_news.save_jpeg_copy(png)
    os.remove(png)
    return auto_post._publish_card_files([jpg])[0]


def publish_threads(t, card_urls=()):
    import threads_api
    parts = [str(p).strip() for p in t["parts"]]
    tag = t.get("tag") or None
    card_urls = list(card_urls or []) if t.get("with_card", True) else []
    if len(card_urls) >= 2:
        post_id = threads_api.publish_carousel(card_urls, parts[0], topic_tag=tag)
    elif card_urls:
        post_id = threads_api.publish_image(card_urls[0], parts[0], topic_tag=tag)
    else:
        post_id = threads_api.publish_text(parts[0], topic_tag=tag)
    last = post_id
    for extra in parts[1:]:
        last = threads_api.reply_to(last, extra)
    return post_id


def publish_instagram(i, card_urls):
    import instagram_api
    if not instagram_api.enabled():
        raise RuntimeError("인스타 토큰이 설정되어 있지 않습니다")
    if not card_urls:
        raise RuntimeError("카드 이미지를 만들지 못했습니다")
    if len(card_urls) >= 2:
        return instagram_api.publish_carousel(card_urls, i["caption"])
    return instagram_api.publish_image(card_urls[0], i["caption"])


def run_job(path, job):
    problem = validate(job)
    if dry_run():
        print(f"[미리보기] {job.get('title')} → {problem or '올릴 수 있음'}")
        return
    done_path = claim(path, job)
    results = {}
    if problem:
        results = {c: {"ok": False, "error": problem} for c in ("threads", "instagram") if job.get(c)}
    else:
        card_urls, card_error = [], ""
        try:
            card_urls = make_card_urls(job)
        except Exception as e:  # 카드가 안 만들어져도 스레드 글은 올린다
            card_error = str(e)[:200]
            print(f"[허브][경고] 카드 이미지 실패: {e}")
        for name, fn in (("threads", lambda: publish_threads(job["threads"], card_urls)),
                         ("instagram", lambda: publish_instagram(job["instagram"], card_urls))):
            if not job.get(name):
                continue
            try:
                results[name] = {"ok": True, "post_id": str(fn())}
                print(f"[허브] {name} 발행 완료: {job.get('title')}")
            except Exception as e:  # 한 채널이 실패해도 다른 채널은 올린다
                results[name] = {"ok": False, "error": str(e)[:300]}
                print(f"[허브][실패] {name}: {e}")
    job["results"] = results
    job["finished"] = datetime.datetime.now(KST).isoformat(timespec="seconds")
    with open(done_path, "w", encoding="utf-8") as f:
        json.dump(job, f, ensure_ascii=False, indent=2)
    _commit_push("허브: 예약 글 발행 결과 기록", "hub_queue")


def main():
    now = datetime.datetime.now(KST)
    paths = sorted(glob.glob(os.path.join(QUEUE_DIR, "*.json")))
    if not paths:
        print("[허브] 예약된 글이 없습니다.")
        return 0
    for path in paths:
        try:
            with open(path, encoding="utf-8") as f:
                job = json.load(f)
        except (OSError, ValueError) as e:
            print(f"[허브][건너뜀] {os.path.basename(path)} 를 읽지 못했습니다: {e}")
            continue
        if not is_due(job, now):
            print(f"[허브] 아직 시각 전: {job.get('title')} ({job.get('publish_at')})")
            continue
        run_job(path, job)
    return 0


if __name__ == "__main__":
    sys.exit(main())
