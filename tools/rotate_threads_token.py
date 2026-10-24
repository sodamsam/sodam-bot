# -*- coding: utf-8 -*-
"""Threads 장기 토큰(60일)을 자동으로 연장하고 GitHub Secret(THREADS_ACCESS_TOKEN)을 교체한다.

GitHub Actions(refresh_token.yml)에서 2주마다 실행된다. 필요한 환경변수:
  THREADS_ACCESS_TOKEN  현재 토큰 (아직 만료 전이어야 함)
  GH_ADMIN_TOKEN        이 저장소 Secrets를 수정할 수 있는 GitHub 토큰 (Fine-grained PAT, Secrets: Read and write)
  GITHUB_REPOSITORY     owner/repo (Actions가 자동으로 넣어줌)

새 토큰 값은 로그에 절대 출력하지 않는다(::add-mask:: 로 가린다).
"""
import os
import subprocess
import sys

import requests

THREADS_REFRESH_URL = "https://graph.threads.net/refresh_access_token"
THREADS_ME_URL = "https://graph.threads.net/v1.0/me"


def refresh_token(token):
    """토큰을 연장해 (새 토큰, 유효 초)를 돌려준다. 실패하면 RuntimeError."""
    r = requests.get(THREADS_REFRESH_URL,
                     params={"grant_type": "th_refresh_token", "access_token": token}, timeout=30)
    if not r.ok:
        raise RuntimeError(f"토큰 연장 실패: {r.status_code} {r.text[:200]}")
    data = r.json()
    new_token = data.get("access_token")
    if not new_token:
        raise RuntimeError("토큰 연장 응답에 access_token이 없습니다")
    return new_token, int(data.get("expires_in", 0))


def token_works(token):
    r = requests.get(THREADS_ME_URL, params={"fields": "id", "access_token": token}, timeout=30)
    return r.ok


def store_secret(new_token, repo, admin_token):
    """gh CLI로 Secret을 교체한다. 값은 표준입력으로만 전달해 명령줄·로그에 남기지 않는다."""
    env = dict(os.environ, GH_TOKEN=admin_token)
    result = subprocess.run(
        ["gh", "secret", "set", "THREADS_ACCESS_TOKEN", "--repo", repo],
        input=new_token, text=True, capture_output=True, env=env)
    if result.returncode != 0:
        raise RuntimeError(f"Secret 교체 실패: {result.stderr.strip()[:200]}")


def main():
    token = os.environ.get("THREADS_ACCESS_TOKEN", "")
    admin = os.environ.get("GH_ADMIN_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")

    if not token:
        print("::error::THREADS_ACCESS_TOKEN이 비어 있습니다")
        return 1
    if not admin:
        print("::error::GH_ADMIN_TOKEN Secret이 없어 토큰을 자동 교체할 수 없습니다. "
              "docs/07_운영_자동화.md의 '권한 토큰 만들기'를 따라 등록해 주세요")
        return 1

    try:
        new_token, expires_in = refresh_token(token)
        print(f"::add-mask::{new_token}")
        if not token_works(new_token):
            raise RuntimeError("연장된 새 토큰이 /me 호출에서 거부되어 Secret을 교체하지 않습니다")
        store_secret(new_token, repo, admin)
    except RuntimeError as e:
        print(f"::error::{e}")
        return 1

    print(f"[완료] THREADS_ACCESS_TOKEN 교체 완료 — 유효기간 약 {expires_in // 86400}일")
    return 0


if __name__ == "__main__":
    sys.exit(main())
