#!/usr/bin/env python3
"""Phase 1: FX rates + all market caps → save to _phase1_result.json (no history, no commit)
   Uses Yahoo Finance bulk quote API (v7/finance/quote) for fast batch fetching.
"""
import sys, json, datetime, time, os, base64, urllib.request
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

BASE_DIR = Path(__file__).parent.resolve()
for p in [BASE_DIR/"pylib", BASE_DIR/"pylibs"]:
    if p.exists(): sys.path.insert(0, str(p))

import yfinance.data as yfd

REPO = "whysosary-dot/invest-private"  # 비공개 데이터 리포
BRANCH = "main"
TOKEN_FILE = BASE_DIR / ".github_token"

def get_token():
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if tok: return tok.strip()
    if TOKEN_FILE.exists(): return TOKEN_FILE.read_text().strip()
    raise SystemExit("토큰 없음")

def gh_get_file(token):
    url = f"https://api.github.com/repos/{REPO}/contents/sv/stocks.json?ref={BRANCH}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "stock-valuation-daily",
    })
    with urllib.request.urlopen(req, timeout=20) as r:
        meta = json.loads(r.read().decode())
    sha = meta["sha"]
    content_b64 = meta.get("content", "").replace("\n", "")
    if content_b64:
        return json.loads(base64.b64decode(content_b64).decode("utf-8")), sha
    else:
        # Large file (>1MB): use Git Blobs API
        blob_url = f"https://api.github.com/repos/{REPO}/git/blobs/{sha}"
        blob_req = urllib.request.Request(blob_url, headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.raw+json",
            "User-Agent": "stock-valuation-daily",
        })
        with urllib.request.urlopen(blob_req, timeout=60) as r:
            raw = r.read()
        return json.loads(raw.decode("utf-8")), sha

def bulk_quotes(symbols):
    """Fetch market data for all symbols in one API call via Yahoo Finance v7 bulk quote."""
    yfdata = yfd.YfData()
    url = "https://query2.finance.yahoo.com/v7/finance/quote"
    result = yfdata.get_raw_json(url, params={"symbols": ",".join(symbols)}, timeout=30)
    quotes = result.get("quoteResponse", {}).get("result", [])
    return {q["symbol"]: q for q in quotes}

def fetch_fx_bulk(fx_symbols):
    """Fetch FX rates using the same bulk quote API."""
    quotes = bulk_quotes(fx_symbols)
    return {sym: (quotes[sym]["regularMarketPrice"] if sym in quotes else None) for sym in fx_symbols}

t0 = time.time()
print(f"[{datetime.datetime.now():%H:%M:%S}] Phase 1 시작 (FX + 시총 — bulk API)")
token = get_token()

FX_SYMS = {"USD": "KRW=X", "JPY": "JPYKRW=X", "HKD": "HKDKRW=X",
           "CNY": "CNYKRW=X", "TWD": "TWDKRW=X", "EUR": "EURKRW=X"}
FX_DEFAULTS = {"USD": 1400.0, "JPY": 9.0, "HKD": 180.0, "CNY": 195.0, "TWD": 46.0, "EUR": 1700.0}

# Parallel: GitHub blob fetch + FX rates + stock quotes
with ThreadPoolExecutor(max_workers=3) as ex:
    f_gh  = ex.submit(gh_get_file, token)
    f_fx  = ex.submit(fetch_fx_bulk, list(FX_SYMS.values()))
    data, gh_sha = f_gh.result()
    fx_raw = f_fx.result()

fx = {"KRW": 1.0}
for cur, sym in FX_SYMS.items():
    v = fx_raw.get(sym)
    fx[cur] = float(v) if v and float(v) > 0 else FX_DEFAULTS[cur]
fx["RMB"] = fx["CNY"]

stocks = data.get("stocks", [])
print(f"  ↓ GitHub {len(stocks)}개 (sha={gh_sha[:7]}) | USD={fx['USD']:.0f} JPY={fx['JPY']:.2f} TWD={fx['TWD']:.2f}")

# Bulk fetch all stock quotes in one call
tickers_with_idx = [(i, s.get("ticker", "").strip()) for i, s in enumerate(stocks) if s.get("ticker", "").strip()]
all_tickers = [tk for _, tk in tickers_with_idx]
print(f"  → bulk quote for {len(all_tickers)} tickers...")
qt0 = time.time()
quote_map = bulk_quotes(all_tickers)
print(f"  ← {len(quote_map)} quotes in {time.time()-qt0:.2f}s")

ok_n = 0
for i, ticker in tickers_with_idx:
    s = stocks[i].copy()
    q = quote_map.get(ticker)
    if not q:
        stocks[i] = s
        print(f"  ✗ {s.get('name','?'):10s} {ticker:12s} no quote")
        continue
    price  = q.get("regularMarketPrice")
    cur    = (q.get("currency") or "").upper() or "USD"
    mc_raw = q.get("marketCap")
    prev   = q.get("regularMarketPreviousClose")
    pcp    = q.get("regularMarketChangePercent")
    ov     = float(s.get("shares_override") or 0)
    if ov and price:
        mc = float(price) * ov
    elif mc_raw:
        mc = float(mc_raw)
    else:
        stocks[i] = s
        print(f"  ✗ {s.get('name','?'):10s} {ticker:12s} no marketCap")
        continue
    mc_krw = float(mc) if cur in ("KRW",) or ticker.endswith((".KS",".KQ")) else float(mc) * fx.get(cur, fx["USD"])
    s["market_cap_oku"]   = round(mc_krw / 1e8, 2)
    s["currency"]         = cur
    s["price_native"]     = round(float(price), 4) if price else None
    s["price_change_pct"] = round(float(pcp), 2) if pcp is not None else None
    stocks[i] = s
    ok_n += 1
    print(f"  ✓ {s.get('name','?'):10s} {ticker:12s} {mc_krw/1e8:,.0f}억")

data["stocks"] = stocks
data["usdkrw"]     = round(fx["USD"], 2)
data["_gh_sha"]    = gh_sha
data["updated_at"] = datetime.datetime.now().isoformat(timespec="seconds")

out = BASE_DIR / "_phase1_result.json"
out.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"\n  ✓ Phase 1 완료: {ok_n}/{len(stocks)} | {time.time()-t0:.1f}s → {out.name} 저장")
