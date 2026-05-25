#!/usr/bin/env python3
"""
Daily auto-update (fast parallel version):
  1) GitHub에서 stocks.json 최신 불러오기 (유저가 웹에서 편집한 내용 포함)
  2) yfinance fast_info로 모든 종목 시총/통화 갱신 (병렬, ~23s)
     - 유저 입력 필드(target/safety/q1~q4 매출·영업이익)는 절대 건드리지 않음
  3) yfinance history(1wk)로 주봉 차트 데이터 갱신 (병렬, ~20s, 숫자배열 형식)
  4) Git Data API로 커밋 (1MB 초과 파일 대응)

수동 실행: python3 daily_update.py
"""

import os
import sys
import json
import base64
import datetime
import urllib.request
import urllib.error
import warnings
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

warnings.filterwarnings('ignore')

BASE_DIR = Path(__file__).parent.resolve()

# yfinance는 로컬 pylib 또는 시스템 패키지 사용
for _p in [BASE_DIR / "pylib", BASE_DIR / "pylibs", Path("/tmp/pypackages")]:
    if _p.exists():
        sys.path.insert(0, str(_p))

try:
    import yfinance as yf
except ImportError:
    print("[ERROR] yfinance 없음. pip install yfinance 실행 후 재시도.")
    sys.exit(1)

REPO = "whysosary-dot/stock-valuation"
BRANCH = "main"
TOKEN_FILE = BASE_DIR / ".github_token"

# 서버가 덮어쓰는 필드 (유저 입력은 보존)
SERVER_WRITE_FIELDS = {
    "market_cap_oku", "currency", "price_native", "price_change_pct",
    "naver_code", "price_history", "price_history_3y", "price_history_3m"
}


# ── GitHub helpers ──────────────────────────────────────────

def get_token() -> str:
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if tok:
        return tok.strip()
    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text().strip()
    raise SystemExit(f"GitHub 토큰 없음: {TOKEN_FILE}")


def _gh_req(token, method, path, body=None, timeout=30):
    url = f"https://api.github.com/repos/{REPO}/{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, method=method, data=data,
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "stock-valuation-daily",
            "Content-Type": "application/json",
        })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def gh_get_file(token: str):
    """GitHub에서 stocks.json 로드 (Contents API, ≤1MB 용)"""
    meta = _gh_req(token, "GET", f"contents/stocks.json?ref={BRANCH}", timeout=20)
    content = base64.b64decode(meta["content"]).decode("utf-8")
    return json.loads(content), meta["sha"]


def gh_commit_data_api(token: str, content_str: str, message: str) -> dict:
    """Git Data API로 커밋 — 파일 크기 무제한"""
    ref = _gh_req(token, "GET", f"git/ref/heads/{BRANCH}")
    commit_sha = ref["object"]["sha"]
    commit_obj = _gh_req(token, "GET", f"git/commits/{commit_sha}")
    tree_sha = commit_obj["tree"]["sha"]

    blob = _gh_req(token, "POST", "git/blobs", {
        "content": base64.b64encode(content_str.encode()).decode(),
        "encoding": "base64",
    })
    new_tree = _gh_req(token, "POST", "git/trees", {
        "base_tree": tree_sha,
        "tree": [{"path": "stocks.json", "mode": "100644", "type": "blob", "sha": blob["sha"]}],
    })
    new_commit = _gh_req(token, "POST", "git/commits", {
        "message": message,
        "tree": new_tree["sha"],
        "parents": [commit_sha],
    })
    _gh_req(token, "PATCH", f"git/refs/heads/{BRANCH}", {"sha": new_commit["sha"]})
    return new_commit


# ── Data fetchers ────────────────────────────────────────────

def _fetch_fx(sym: str, default: float) -> float:
    try:
        fi = yf.Ticker(sym).fast_info
        v = fi.get("lastPrice") or fi.get("last_price")
        if v and float(v) > 0:
            return float(v)
    except Exception:
        pass
    return default


def _fetch_marketcap(s: dict, fx: dict) -> tuple:
    """fast_info 기반 시총 조회 (병렬용). (stock_dict, ok, msg) 반환"""
    ticker = (s.get("ticker") or "").strip()
    if not ticker:
        return s, False, "ticker 없음"
    s = s.copy()
    try:
        fi = yf.Ticker(ticker).fast_info
        price = fi.get("lastPrice") or fi.get("last_price")
        cur = (fi.get("currency") or "").upper() or "USD"

        # 시총: fast_info.marketCap 우선, 없으면 price×shares
        mc = fi.get("marketCap") or fi.get("market_cap")
        if not mc:
            ov = float(s.get("shares_override") or 0)
            sh = ov or fi.get("shares") or fi.get("shares_outstanding")
            if sh and price:
                mc = float(price) * float(sh)
        if not mc:
            return s, False, "marketCap not found"

        # 원화 환산
        if cur == "KRW" or ticker.endswith(".KS") or ticker.endswith(".KQ"):
            mc_krw = float(mc)
        else:
            mc_krw = float(mc) * fx.get(cur, fx.get("USD", 1400.0))

        # 등락률
        pcp = None
        try:
            v = fi.get("regularMarketChangePercent") or fi.get("regular_market_change_percent")
            if v:
                pcp = round(float(v), 4)
        except Exception:
            pass

        s["market_cap_oku"] = round(mc_krw / 1e8, 2)
        s["currency"] = cur
        s["price_native"] = round(float(price), 4) if price else None
        s["price_change_pct"] = pcp
        return s, True, f"{mc_krw / 1e8:,.0f}억"
    except Exception as e:
        return s, False, str(e)


def _fetch_history(ticker: str) -> tuple:
    """주봉 종가 숫자배열 반환 (renderSparkline 호환). (ticker, h1y, h3y, h3m) 반환"""
    try:
        t = yf.Ticker(ticker)
        h1 = t.history(period="1y", interval="1wk")
        h3 = t.history(period="3y", interval="1wk")
        h3m = t.history(period="3mo", interval="1d")
        r1 = [round(float(x), 4) for x in h1["Close"].dropna().tolist()] if not h1.empty else []
        r3 = [round(float(x), 4) for x in h3["Close"].dropna().tolist()] if not h3.empty else []
        r3m = [round(float(x), 4) for x in h3m["Close"].dropna().tolist()] if not h3m.empty else []
        return ticker, r1, r3, r3m
    except Exception:
        return ticker, [], [], []


# ── Main ─────────────────────────────────────────────────────

def main():
    t_start = datetime.datetime.now()
    print(f"[{t_start:%Y-%m-%d %H:%M:%S}] 일일 업데이트 시작")

    token = get_token()

    # GitHub + FX 병렬 로드
    with ThreadPoolExecutor(max_workers=5) as ex:
        f_gh  = ex.submit(gh_get_file, token)
        f_usd = ex.submit(_fetch_fx, "KRW=X",    1400.0)
        f_jpy = ex.submit(_fetch_fx, "JPYKRW=X",    9.0)
        f_hkd = ex.submit(_fetch_fx, "HKDKRW=X",  180.0)
        f_cny = ex.submit(_fetch_fx, "CNYKRW=X",  195.0)
        data, sha = f_gh.result()
        fx = {
            "KRW": 1.0,
            "USD": f_usd.result(),
            "JPY": f_jpy.result(),
            "HKD": f_hkd.result(),
            "CNY": f_cny.result(),
        }
        fx["RMB"] = fx["CNY"]

    stocks = data.get("stocks", [])
    usdkrw = fx["USD"]
    print(f"  ↓ GitHub {len(stocks)}개 (sha={sha[:7]}) | USD={usdkrw:.0f} JPY={fx['JPY']:.1f}")

    tickers_valid = [(s.get("ticker") or "").strip() for s in stocks]

    # 시총 + 가격이력 동시 병렬 실행 (workers=40)
    print(f"  ▶ 시총+이력 병렬 조회 ({len(stocks)}개, workers=40)…")
    mcap_results: dict = {}
    hist_results: dict = {}

    with ThreadPoolExecutor(max_workers=40) as ex:
        # 시총
        mcap_futs = {ex.submit(_fetch_marketcap, s.copy(), fx): i for i, s in enumerate(stocks)}
        # 가격이력
        hist_futs = {ex.submit(_fetch_history, t): t for t in tickers_valid if t}

        for fut in as_completed(list(mcap_futs) + list(hist_futs)):
            if fut in mcap_futs:
                i = mcap_futs[fut]
                try:
                    mcap_results[i] = fut.result()
                except Exception as e:
                    mcap_results[i] = (stocks[i].copy(), False, str(e))
            else:
                t = hist_futs[fut]
                try:
                    tk, h1, h3, h3m = fut.result()
                    hist_results[tk] = (h1, h3, h3m)
                except Exception:
                    hist_results[t] = ([], [], [])

    ok_count = 0
    fail = []
    for i, s in enumerate(stocks):
        # 시총 업데이트
        if i in mcap_results:
            s_up, ok, msg = mcap_results[i]
        else:
            s_up, ok, msg = s.copy(), False, "no result"

        # 가격이력 주입 (숫자배열, 주봉)
        ticker = (s_up.get("ticker") or "").strip()
        if ticker and ticker in hist_results:
            h1, h3, h3m = hist_results[ticker]
            s_up["price_history"] = h1
            s_up["price_history_3y"] = h3
            s_up["price_history_3m"] = h3m

        stocks[i] = s_up
        name = s_up.get("name", "?")
        h1c = len(s_up.get("price_history") or [])
        h3c = len(s_up.get("price_history_3y") or [])
        h3mc = len(s_up.get("price_history_3m") or [])
        if ok:
            ok_count += 1
            print(f"  ✓ {name:10s} {ticker:12s} {msg}  (1y:{h1c}pt 3y:{h3c}pt)")
        else:
            fail.append(f"{name}({ticker}): {msg}")
            print(f"  ✗ {name:10s} {ticker:12s} — {msg}")

    data["stocks"] = stocks
    data["usdkrw"] = round(usdkrw, 2)
    data["updated_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    e1 = (datetime.datetime.now() - t_start).total_seconds()
    print(f"\n  성공 {ok_count} / 실패 {len(fail)} | {e1:.1f}s")
    if fail:
        print("  실패 목록:", "; ".join(fail))

    # 로컬 백업
    content_str = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    try:
        (BASE_DIR / "stocks.json").write_text(content_str, encoding="utf-8")
    except Exception as e:
        print(f"  (로컬 백업 실패: {e})")

    # GitHub 커밋 (Git Data API — 파일 크기 무관)
    msg_commit = f"📊 일일 시총+이력 업데이트: {datetime.date.today().isoformat()}"
    try:
        commit = gh_commit_data_api(token, content_str, msg_commit)
    except Exception as e:
        print(f"  [ERROR] 커밋 실패: {e}")
        return 1

    sha_short = commit.get("sha", "?")[:7]
    total = (datetime.datetime.now() - t_start).total_seconds()
    print(f"  ✓ GitHub 커밋: {sha_short} | 총 {total:.1f}초")
    url = f"https://github.com/{REPO}/commit/{commit.get('sha', '')}"
    print(f"    {url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
