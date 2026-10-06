#!/usr/bin/env python3
"""
Stock Valuation 밸류에이션 갱신 — invest-private sv/stocks.json 의 각 종목에
val = {per, cns_per, pbr, eps, div, foreign, hi52, lo52, asof} 를 채운다 (네이버 증권).
  한국(.KS/.KQ/6자리): m.stock.naver.com/api/stock/<code>/integration
  미국(그 외 영문 티커): api.stock.naver.com/stock/<TICKER>.O|.K|/basic
  일본 등 기타: 건너뜀 (val 유지)
주가·시총·등락률은 기존 업데이트 스크립트가 담당하므로 건드리지 않는다.
사용: python3 val_update.py [--dry-run]
"""
import sys, re, json, base64, time, datetime, urllib.request, urllib.error
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

BASE = Path(__file__).parent.resolve()
REPO, BRANCH, PATH = "whysosary-dot/invest-private", "main", "sv/stocks.json"
DRY = "--dry-run" in sys.argv
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36"}


def tok():
    import os
    t = os.environ.get("GH_PAT") or os.environ.get("GITHUB_TOKEN")
    if t:
        return t.strip()
    return (BASE / ".github_token").read_text().strip()


def gj(url):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


def num(s):
    if s is None:
        return None
    s = re.sub(r"[^\d.\-]", "", str(s).replace(",", ""))
    try:
        return float(s) if s not in ("", "-", ".") else None
    except ValueError:
        return None


KEYS = (("per", "per"), ("cns_per", "cnsPer"), ("pbr", "pbr"), ("eps", "eps"), ("cns_eps", "cnsEps"),
        ("div", "dividendYieldRatio"), ("foreign", "foreignRate"), ("hi52", "highPriceOf52Weeks"), ("lo52", "lowPriceOf52Weeks"))


def val_kr(code):
    d = gj(f"https://m.stock.naver.com/api/stock/{code}/integration")
    if not d:
        return None
    info = {x.get("code"): x.get("value") for x in d.get("totalInfos", [])}
    return {k: num(info.get(src)) for k, src in KEYS if num(info.get(src)) is not None}


def val_us(tk):
    for suf in (".O", ".K", "", ".A"):
        b = gj(f"https://api.stock.naver.com/stock/{tk}{suf}/basic")
        if b and b.get("closePrice"):
            info = {x.get("code"): x.get("value") for x in b.get("stockItemTotalInfos", [])}
            return {k: num(info.get(src)) for k, src in KEYS if num(info.get(src)) is not None}
    return None


def val_world(code):
    """일본(.T)·홍콩(.HK) 등 — 네이버 해외주식은 로이터 코드를 그대로 받는다"""
    b = gj(f"https://api.stock.naver.com/stock/{code}/basic")
    if b and b.get("closePrice"):
        info = {x.get("code"): x.get("value") for x in b.get("stockItemTotalInfos", [])}
        return {k: num(info.get(src)) for k, src in KEYS if num(info.get(src)) is not None}
    return None


def fetch(s):
    t = str(s.get("ticker") or "")
    code = t.split(".")[0]
    if re.fullmatch(r"\d{6}", code) or t.endswith((".KS", ".KQ")):
        return val_kr(code)
    if "." in t:                       # 7974.T, 9992.HK, 2408.TW, HY9H.F ...
        return val_world(t)
    if re.fullmatch(r"[A-Z][A-Z0-9\-]{0,6}", t):   # 미국
        return val_us(t)
    return None


def main():
    H = {"Authorization": f"token {tok()}", "Accept": "application/vnd.github+json", "User-Agent": "sv", "Content-Type": "application/json"}
    m = json.loads(urllib.request.urlopen(urllib.request.Request(f"https://api.github.com/repos/{REPO}/contents/{PATH}?ref={BRANCH}&t={time.time()}", headers=H), timeout=30).read())
    data = json.loads(base64.b64decode(m["content"]).decode())
    stocks = data.get("stocks", [])
    asof = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    with ThreadPoolExecutor(6) as ex:
        vals = list(ex.map(fetch, stocks))
    ok = 0
    for s, v in zip(stocks, vals):
        if v:
            v["asof"] = asof
            s["val"] = v
            ok += 1
    print(f"밸류에이션 {ok}/{len(stocks)} 갱신 ({asof})")
    for s, v in list(zip(stocks, vals))[:5]:
        print(" ", s.get("name"), v)
    if DRY:
        return 0
    body = {"message": f"val: 밸류에이션 갱신 {ok}/{len(stocks)} ({asof})", "branch": BRANCH, "sha": m["sha"],
            "content": base64.b64encode((json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode()).decode()}
    r = json.loads(urllib.request.urlopen(urllib.request.Request(f"https://api.github.com/repos/{REPO}/contents/{PATH}", method="PUT", data=json.dumps(body).encode(), headers=H), timeout=60).read())
    print("  ✓ 커밋", r["commit"]["sha"][:7])
    return 0


if __name__ == "__main__":
    sys.exit(main())
