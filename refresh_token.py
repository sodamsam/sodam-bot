# -*- coding: utf-8 -*-
"""스레드 장기 액세스 토큰 갱신 도우미.

장기 토큰은 60일 유효합니다. 만료 전에(발급 24시간 이후~만료 전) 실행하면
새 토큰이 출력됩니다. 출력된 토큰을 GitHub Secrets의
THREADS_ACCESS_TOKEN 값으로 교체해 주세요.

사용법:
  python refresh_token.py
"""
import requests
import config


def main():
    if not config.THREADS_ACCESS_TOKEN:
        raise SystemExit("[오류] THREADS_ACCESS_TOKEN이 비어 있습니다.")
    r = requests.get(
        "https://graph.threads.net/refresh_access_token",
        params={
            "grant_type": "th_refresh_token",
            "access_token": config.THREADS_ACCESS_TOKEN,
        },
        timeout=30,
    )
    if not r.ok:
        raise SystemExit(f"[오류] 갱신 실패: {r.status_code} {r.text[:300]}")
    data = r.json()
    print("=== 새 토큰 (GitHub Secrets에 교체 등록하세요) ===")
    print(data.get("access_token"))
    print(f"유효기간(초): {data.get('expires_in')} (약 {int(data.get('expires_in', 0)) // 86400}일)")


if __name__ == "__main__":
    main()
