#!/usr/bin/env python3
"""
Daily auto-update:
  1) stocks.json 로드
  2) yfinance로 모든 종목 시총 갱신 (원화 억원)
  3) GitHub Contents API로 stocks.json 직접 커밋 (로컬 git 없이도 작동)

매일 오전 6시에 스케줄러가 실행합니다.
수동 실행: python3 daily_update.py
"""

import os
import sys
import json
import base64
import datetime
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(BASE_DIR))

from server import _load_data, _save_data, _get_usdkrw, _fetch_marketcap_krw  # noqa: E402

REPO = "whysosary-dot/stock-valuation"
FILE_PATH = "stocks.json"
TOKEN_FILE = BASE_DIR / ".github_token"  # chmod 600
DEFAULT_BRANCH = "main"


def get_token() -> str:
    # 1) env 우선
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if tok:
        return tok.strip()
    # 2) .github_token 파일
    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text().strip()
    raise SystemExit(
        f"GitHub 토큰 없음: 환경변수 GITHUB_TOKEN 또는 {TOKEN_FILE} 파일에 PAT를 넣어주세요."
    )


def github_put_file(token: str, content_bytes: bytes, message: str):
    """GitHub Contents API로 파일 생성/업데이트 (자동 커밋)"""
    api = f"https://api.github.com/repos/{REPO}/contents/{FILE_PATH}"

    # 기존 파일의 sha 조회
    req = urllib.request.Request(
        f"{api}?ref={DEFAULT_BRANCH}",
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "stock-valuation-daily",
        },
    )
    sha = None
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            meta = json.loads(resp.read().decode("utf-8"))
            sha = meta.get("sha")
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise

    body = {
        "message": message,
        "content": base64.b64encode(content_bytes).decode("ascii"),
        "branch": DEFAULT_BRANCH,
    }
    if sha:
        body["sha"] = sha

    put = urllib.request.Request(
        api,
        method="PUT",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "stock-valuation-daily",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(put, timeout=30) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    return result.get("commit", {}).get("sha"), result.get("commit", {}).get("html_url")


def main():
    started = datetime.datetime.now()
    print(f"[{started:%Y-%m-%d %H:%M:%S}] 일일 시총 업데이트 시작")

    data = _load_data()
    stocks = data.get("stocks", [])
    if not stocks:
        print("  종목이 없습니다. 종료.")
        return 0

    usdkrw = _get_usdkrw()
    data["usdkrw"] = round(usdkrw, 2)
    print(f"  USDKRW: {usdkrw:.2f}")

    ok_count = 0
    fail = []
    for s in stocks:
        ticker = (s.get("ticker") or "").strip()
        if not ticker:
            fail.append(s.get("name", "?"))
            continue
        r = _fetch_marketcap_krw(ticker, usdkrw)
        if r["ok"]:
            s["market_cap_oku"] = r["market_cap_oku"]
            s["currency"] = r["currency"]
            s["price_native"] = r.get("price_native")
            ok_count += 1
            print(f"  ✓ {s.get('name','?'):10s} {ticker:12s} {r['market_cap_oku']:>14,.0f} 억원")
        else:
            fail.append(f"{s.get('name','?')}({ticker}): {r.get('error')}")
            print(f"  ✗ {s.get('name','?'):10s} {ticker:12s} — {r.get('error')}")

    data["updated_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    _save_data(data)

    print(f"\n  성공 {ok_count} / 실패 {len(fail)}")

    # GitHub API로 커밋
    try:
        token = get_token()
        content = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
        msg = f"📊 일일 시총 업데이트: {datetime.date.today().isoformat()}"
        commit_sha, commit_url = github_put_file(token, content, msg)
        print(f"  ✓ GitHub 커밋: {commit_sha[:7] if commit_sha else '?'}")
        if commit_url:
            print(f"    {commit_url}")
    except Exception as e:
        print(f"  ✗ GitHub 푸시 실패: {e}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
