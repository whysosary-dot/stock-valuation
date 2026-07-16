#!/usr/bin/env python3
"""
Parallelized daily update - fetches all stocks concurrently to complete within timeout.
"""
import os, sys, json, base64, datetime, urllib.request, urllib.error
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(BASE_DIR))
from server import _get_usdkrw, _fetch_marketcap_krw, _fetch_price_history

REPO = "whysosary-dot/invest-private"  # 비공개 데이터 리포
FILE_PATH = "sv/stocks.json"
BRANCH = "main"
TOKEN_FILE = BASE_DIR / ".github_token"
SERVER_WRITE_FIELDS = {"market_cap_oku","currency","price_native","price_change_pct","naver_code","price_history","price_history_3y"}

def get_token():
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if tok: return tok.strip()
    if TOKEN_FILE.exists(): return TOKEN_FILE.read_text().strip()
    raise SystemExit("GitHub 토큰 없음")

def gh_get_file(token):
    api = f"https://api.github.com/repos/{REPO}/contents/{FILE_PATH}?ref={BRANCH}"
    req = urllib.request.Request(api, headers={
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "stock-valuation-daily",
    })
    with urllib.request.urlopen(req, timeout=20) as resp:
        meta = json.loads(resp.read().decode())
    content = base64.b64decode(meta["content"]).decode()
    return json.loads(content), meta["sha"]

def gh_put_file(token, content_bytes, sha, message):
    api = f"https://api.github.com/repos/{REPO}/contents/{FILE_PATH}"
    body = {"message": message, "content": base64.b64encode(content_bytes).decode("ascii"), "branch": BRANCH, "sha": sha}
    put = urllib.request.Request(api, method="PUT", data=json.dumps(body).encode(),
        headers={"Authorization": f"token {token}", "Accept": "application/vnd.github+json",
                 "User-Agent": "stock-valuation-daily", "Content-Type": "application/json"})
    with urllib.request.urlopen(put, timeout=30) as resp:
        return json.loads(resp.read().decode())

def fetch_one(s, usdkrw):
    ticker = (s.get("ticker") or "").strip()
    if not ticker:
        return s, False, "no ticker"
    adj = s.get("shares_adjustment") or 0
    override = s.get("shares_override") or 0
    r = _fetch_marketcap_krw(ticker, usdkrw, shares_adjustment=adj, shares_override=override)
    if not r.get("ok"):
        return s, False, r.get("error", "fetch failed")
    s["market_cap_oku"] = r["market_cap_oku"]
    s["currency"] = r["currency"]
    s["price_native"] = r.get("price_native")
    s["price_change_pct"] = r.get("price_change_pct")
    new_nc = r.get("naver_code")
    if new_nc and ('.' in new_nc or not s.get("naver_code")):
        s["naver_code"] = new_nc
    s["price_history"] = _fetch_price_history(ticker, '1y')
    s["price_history_3y"] = _fetch_price_history(ticker, '3y')
    return s, True, f"{r['market_cap_oku']:,.0f} 억원"

def main():
    print(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 일일 시총 업데이트 시작 (병렬 모드)")
    token = get_token()
    data, sha = gh_get_file(token)
    stocks = data.get("stocks", [])
    print(f"  ↓ GitHub에서 {len(stocks)}개 종목 로드 (sha={sha[:7]})")

    usdkrw = _get_usdkrw()
    data["usdkrw"] = round(usdkrw, 2)
    print(f"  USDKRW: {usdkrw:.2f}")

    ok_count = 0
    fail = []
    results = {}

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(fetch_one, s, usdkrw): i for i, s in enumerate(stocks)}
        for future in as_completed(futures):
            idx = futures[future]
            try:
                s, ok, msg = future.result()
                results[idx] = (s, ok, msg)
            except Exception as e:
                results[idx] = (stocks[idx], False, str(e))

    for i, s in enumerate(stocks):
        upd_s, ok, msg = results[i]
        stocks[i] = upd_s
        name = s.get('name', '?')
        ticker = s.get('ticker', '?')
        if ok:
            ok_count += 1
            print(f"  ✓ {name:12s} {ticker:12s} {msg}")
        else:
            fail.append(f"{name}({ticker}): {msg}")
            print(f"  ✗ {name:12s} {ticker:12s} — {msg}")

    data["updated_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    print(f"\n  성공 {ok_count} / 실패 {len(fail)}")
    if fail:
        for f in fail: print(f"    - {f}")

    try:
        local = BASE_DIR / "stocks.json"
        local.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("  로컬 백업 저장 완료")
    except Exception as e:
        print(f"  (로컬 백업 실패: {e})")

    content = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    msg = f"📊 일일 시총 업데이트: {datetime.date.today().isoformat()}"
    try:
        result = gh_put_file(token, content, sha, msg)
    except urllib.error.HTTPError as e:
        if e.code in (409, 422):
            print("  ⟳ 충돌 감지 — 재시도")
            data2, sha2 = gh_get_file(token)
            by_ticker = {(s.get("ticker") or ""): s for s in data2.get("stocks", [])}
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
    sha_short = commit.get("sha", "?")[:7]
    print(f"  ✓ GitHub 커밋: {sha_short}")
    if commit.get("html_url"):
        print(f"    {commit['html_url']}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
