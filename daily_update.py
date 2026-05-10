#!/usr/bin/env python3
"""
Daily auto-update:
  1) GitHub에서 stocks.json 최신 불러오기 (유저가 웹에서 편집한 내용 포함)
  2) yfinance로 모든 종목 시총/통화 갱신 (시총 관련 필드만 덮어씀)
     - 유저 입력 필드(target/safety/q1~q4 매출·영업이익)는 절대 건드리지 않음
  3) GitHub Contents API로 커밋 & 푸시

매일 오전 6시에 스케줄러가 실행합니다.
수동 실행: python3 daily_update.py
"""

import os
import sys
import json
import base64
import datetime
import urllib.request
import urllib.error
from pathlib import Path

BASE_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(BASE_DIR))

from server import _get_usdkrw, _fetch_marketcap_krw, _fetch_price_history  # noqa: E402

REPO = "whysosary-dot/stock-valuation"
FILE_PATH = "stocks.json"
BRANCH = "main"
TOKEN_FILE = BASE_DIR / ".github_token"

# 시총 업데이트가 덮어써도 되는 필드 (나머지는 유저 입력으로 간주하고 절대 건드리지 않음)
# shares_adjustment: 유증 등 yfinance 미반영 발행주식수 보정치 — 유저 입력으로 보존
SERVER_WRITE_FIELDS = {"market_cap_oku", "currency", "price_native", "price_change_pct", "naver_code", "price_history", "price_history_3y"}


def get_token() -> str:
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if tok:
        return tok.strip()
    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text().strip()
    raise SystemExit(
        f"GitHub 토큰 없음: 환경변수 GITHUB_TOKEN 또는 {TOKEN_FILE} 에 PAT를 넣어주세요."
    )


def gh_get_file(token: str):
    api = f"https://api.github.com/repos/{REPO}/contents/{FILE_PATH}?ref={BRANCH}"
    req = urllib.request.Request(
        api,
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "stock-valuation-daily",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        meta = json.loads(resp.read().decode("utf-8"))
    content = base64.b64decode(meta["content"]).decode("utf-8")
    return json.loads(content), meta["sha"]


def gh_put_file(token: str, content_bytes: bytes, sha: str, message: str):
    api = f"https://api.github.com/repos/{REPO}/contents/{FILE_PATH}"
    body = {
        "message": message,
        "content": base64.b64encode(content_bytes).decode("ascii"),
        "branch": BRANCH,
        "sha": sha,
    }
    put = urllib.request.Request(
        api, method="PUT",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "stock-valuation-daily",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(put, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main():
    started = datetime.datetime.now()
    print(f"[{started:%Y-%m-%d %H:%M:%S}] 일일 시총 업데이트 시작")

    token = get_token()

    # 1) GitHub에서 최신 불러오기 (유저 웹 편집 반영)
    data, sha = gh_get_file(token)
    stocks = data.get("stocks", [])
    if not stocks:
        print("  종목이 없습니다. 종료.")
        return 0
    print(f"  ↓ GitHub에서 {len(stocks)}개 종목 로드 (sha={sha[:7]})")

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
        adj = s.get("shares_adjustment") or 0
        override = s.get("shares_override") or 0
        r = _fetch_marketcap_krw(ticker, usdkrw, shares_adjustment=adj, shares_override=override)
        if r["ok"]:
            # ★ 시총 관련 필드만 덮어씀. target/safety/q1~q4 등 유저 입력은 유지
            s["market_cap_oku"] = r["market_cap_oku"]
            s["currency"] = r["currency"]
            s["price_native"] = r.get("price_native")
            s["price_change_pct"] = r.get("price_change_pct")
            new_nc = r.get("naver_code")
            # suffix 있는 코드만 저장 (suffix 없는 폴백으로 기존 값 덮어쓰기 방지)
            if new_nc and ('.' in new_nc or not s.get("naver_code")):
                s["naver_code"] = new_nc
            s["price_history"] = _fetch_price_history(ticker, '1y')
            s["price_history_3y"] = _fetch_price_history(ticker, '3y')
            ok_count += 1
            print(f"  ✓ {s.get('name','?'):10s} {ticker:12s} {r['market_cap_oku']:>14,.0f} 억원")
        else:
            fail.append(f"{s.get('name','?')}({ticker}): {r.get('error')}")
            print(f"  ✗ {s.get('name','?'):10s} {ticker:12s} — {r.get('error')}")

    data["updated_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    print(f"\n  성공 {ok_count} / 실패 {len(fail)}")

    # 2) 로컬 백업도 업데이트 (선택적, 서버 모드 병행용)
    try:
        local = BASE_DIR / "stocks.json"
        local.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except Exception as e:
        print(f"  (로컬 백업 실패 — 무시: {e})")

    # 3) GitHub에 PUT
    content = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    msg = f"📊 일일 시총 업데이트: {datetime.date.today().isoformat()}"
    try:
        result = gh_put_file(token, content, sha, msg)
    except urllib.error.HTTPError as e:
        # 409/422 = sha 충돌 (유저가 방금 저장) → 재시도
        if e.code in (409, 422):
            print("  ⟳ 충돌 감지 — 재로드 후 재시도")
            data2, sha2 = gh_get_file(token)
            # 유저 편집 필드는 새 버전을 우선, 서버 필드(market_cap 등)는 우리가 방금 받은 값으로
            by_ticker = { (s.get("ticker") or ""): s for s in data2.get("stocks", []) }
            for s in stocks:
                latest = by_ticker.get(s.get("ticker") or "")
                if latest:
                    for k, v in latest.items():
                        if k not in SERVER_WRITE_FIELDS:
                            s[k] = v
            data2["stocks"] = stocks
            data2["usdkrw"] = data["usdkrw"]
            data2["updated_at"] = data["updated_at"]
            content = (json.dumps(data2, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            result = gh_put_file(token, content, sha2, msg + " (retry)")
        else:
            raise

    commit = result.get("commit", {})
    print(f"  ✓ GitHub 커밋: {commit.get('sha','?')[:7]}")
    if commit.get("html_url"):
        print(f"    {commit['html_url']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
