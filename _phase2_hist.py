#!/usr/bin/env python3
"""Phase 2: Load _phase1_result.json, fetch history for ALL tickers in batches, save _phase2_result.json + commit"""
import sys, json, datetime, time, os, base64, urllib.request
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE_DIR = Path(__file__).parent.resolve()
for p in [BASE_DIR/"pylib", BASE_DIR/"pylibs"]:
    if p.exists(): sys.path.insert(0, str(p))

import yfinance as yf

REPO = "whysosary-dot/invest-private"  # 비공개 데이터 리포
BRANCH = "main"
TOKEN_FILE = BASE_DIR / ".github_token"

def get_token():
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if tok: return tok.strip()
    if TOKEN_FILE.exists(): return TOKEN_FILE.read_text().strip()
    raise SystemExit("토큰 없음")

def fetch_history(ticker):
    try:
        t = yf.Ticker(ticker)
        h1  = t.history(period="1y",  interval="1wk")
        h3  = t.history(period="3y",  interval="1wk")
        h3m = t.history(period="3mo", interval="1d")
        r1  = [round(float(x),4) for x in h1["Close"].dropna().tolist()]  if not h1.empty  else []
        r3  = [round(float(x),4) for x in h3["Close"].dropna().tolist()]  if not h3.empty  else []
        r3m = [round(float(x),4) for x in h3m["Close"].dropna().tolist()] if not h3m.empty else []
        return ticker, r1, r3, r3m
    except: return ticker, [], [], []

def gh_commit_data_api(token, content_str, message):
    def req(method, path, body=None):
        url = f"https://api.github.com/repos/{REPO}/{path}"
        data = json.dumps(body).encode() if body else None
        r = urllib.request.Request(url, method=method, data=data, headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "stock-valuation-daily",
            "Content-Type": "application/json",
        })
        with urllib.request.urlopen(r, timeout=30) as resp:
            return json.loads(resp.read().decode())
    ref = req("GET", f"git/ref/heads/{BRANCH}")
    commit_sha = ref["object"]["sha"]
    commit_obj = req("GET", f"git/commits/{commit_sha}")
    tree_sha = commit_obj["tree"]["sha"]
    blob = req("POST", "git/blobs", {"content": base64.b64encode(content_str.encode()).decode(), "encoding": "base64"})
    new_tree = req("POST", "git/trees", {"base_tree": tree_sha, "tree": [{"path":"sv/stocks.json","mode":"100644","type":"blob","sha":blob["sha"]}]})
    new_commit = req("POST", "git/commits", {"message": message, "tree": new_tree["sha"], "parents": [commit_sha]})
    req("PATCH", f"git/refs/heads/{BRANCH}", {"sha": new_commit["sha"]})
    return new_commit

BATCH = int(sys.argv[1]) if len(sys.argv) > 1 else 0  # 0=first half, 1=second half, 2=all+commit

t0 = time.time()
src = BASE_DIR / "_phase1_result.json"
if not src.exists():
    print("[ERROR] _phase1_result.json not found — run phase1 first")
    sys.exit(1)

data = json.loads(src.read_text(encoding="utf-8"))
stocks = data.get("stocks", [])
tickers = [(i, (s.get("ticker") or "").strip()) for i, s in enumerate(stocks) if (s.get("ticker") or "").strip()]

# batch selection
mid = len(tickers)//2
if BATCH == 0:
    batch = tickers[:mid]
    batch_label = f"batch A (0~{mid-1})"
elif BATCH == 1:
    batch = tickers[mid:]
    batch_label = f"batch B ({mid}~{len(tickers)-1})"
else:
    batch = tickers
    batch_label = "all"

print(f"[{datetime.datetime.now():%H:%M:%S}] Phase 2 {batch_label} ({len(batch)} tickers) 이력 조회...")

with ThreadPoolExecutor(max_workers=6) as ex:
    futs = {ex.submit(fetch_history, tk): (i, tk) for i, tk in batch}
    for fut in as_completed(futs):
        i, tk = futs[fut]
        ticker, h1, h3, h3m = fut.result()
        stocks[i]["price_history"]    = h1
        stocks[i]["price_history_3y"] = h3
        stocks[i]["price_history_3m"] = h3m
        print(f"  ✓ {tk:14s} 1y:{len(h1)} 3y:{len(h3)} 3m:{len(h3m)}")

data["stocks"] = stocks
t1 = time.time()
print(f"  이력 완료: {t1-t0:.1f}s")

# save intermediate
out_name = "_phase2a_result.json" if BATCH == 0 else ("_phase2b_result.json" if BATCH == 1 else "_phase2_result.json")
out = BASE_DIR / out_name
out.write_text(json.dumps(data, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
print(f"  저장: {out_name}")

# if BATCH==1 or all, merge with phase2a and commit
if BATCH == 1:
    part_a = BASE_DIR / "_phase2a_result.json"
    if part_a.exists():
        data_a = json.loads(part_a.read_text(encoding="utf-8"))
        stocks_a = data_a.get("stocks", [])
        # merge: take price_history from part_a for first half, from current for second half
        for i in range(mid):
            if i < len(stocks_a) and i < len(stocks):
                stocks[i]["price_history"]    = stocks_a[i].get("price_history", [])
                stocks[i]["price_history_3y"] = stocks_a[i].get("price_history_3y", [])
                stocks[i]["price_history_3m"] = stocks_a[i].get("price_history_3m", [])
        data["stocks"] = stocks
        print(f"  A+B 병합 완료")

if BATCH in (1, 2):
    token = get_token()
    content_str = json.dumps(data, ensure_ascii=False, indent=2)+"\n"
    msg = f"📊 일일 시총+이력 업데이트: {datetime.date.today().isoformat()}"
    try:
        commit = gh_commit_data_api(token, content_str, msg)
        sha_short = commit.get("sha","?")[:7]
        total = time.time()-t0
        print(f"  ✓ GitHub 커밋: {sha_short} | 총 {total:.1f}초")
        print(f"    https://github.com/{REPO}/commit/{commit.get('sha','')}")
        # also update local stocks.json
        (BASE_DIR/"stocks.json").write_text(content_str, encoding="utf-8")
    except Exception as e:
        print(f"  [ERROR] 커밋 실패: {e}")
        sys.exit(1)
