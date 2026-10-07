#!/usr/bin/env python3
"""
📨 Telegram 탭 — 2단계: 언급 종목 + 한 줄 요약 + 밸류에이션 → telegram/data/<date>.json 푸시.

입력
  /tmp/tg/mentions_<date>.json   (tg_collect.py)
  /tmp/tg/summaries_<date>.json  (Claude 가 작성: {"KR:005930": "한 줄 요약", ...})
밸류에이션
  한국: m.stock.naver.com/api/stock/<code>/integration  (주가·등락·시총·PER·추정PER·PBR·EPS·배당·52주·외인)
  미국: api.stock.naver.com/stock/<TICKER>.O|.K|/basic   (같은 항목, USD)
공개 저장소에는 요약·채널명·건수·밸류에이션만 올라간다 (원문 발췌는 올리지 않는다).

사용: python3 tg_build.py [YYYY-MM-DD] [--dry-run]
"""
import sys, os, re, json, base64, time, urllib.request, urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

BASE = Path(__file__).parent.resolve()
REPO, BRANCH = "whysosary-dot/stock-valuation", "main"
KST = timezone(timedelta(hours=9))
DRY = "--dry-run" in sys.argv
WORK = Path(os.environ.get("TG_WORK", "/tmp/tg"))  # TG_WORK: tg_collect.py 와 동일하게
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36"}

date_arg = next((a for a in sys.argv[1:] if re.fullmatch(r"\d{4}-\d{2}-\d{2}", a)), None)
DATE = date_arg or datetime.now(KST).strftime("%Y-%m-%d")


def get_json(url, timeout=20):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
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


def mcap_krw(s):
    """'1,613조 5,729억' / '7,580조 5,532억원' → 억원 정수"""
    if not s:
        return None
    t = str(s).replace(",", "")
    jo = re.search(r"(\d+)조", t); eok = re.search(r"(\d+)억", t)
    v = (int(jo.group(1)) * 10000 if jo else 0) + (int(eok.group(1)) if eok else 0)
    return v or None


def _industry(d, b):
    try:
        ic = (d or {}).get("industryCompareInfo")
        if isinstance(ic, dict) and ic.get("industryName"):
            return ic["industryName"]
        ict = (b or {}).get("industryCodeType")
        if isinstance(ict, dict):
            return ict.get("industryGroupKor") or ict.get("name")
    except Exception:
        pass
    return None


def val_kr(code):
    d = get_json(f"https://m.stock.naver.com/api/stock/{code}/integration")
    b = get_json(f"https://m.stock.naver.com/api/stock/{code}/basic")
    if not d and not b:
        return {}
    info = {x.get("code"): x.get("value") for x in (d or {}).get("totalInfos", [])}
    out = {
        "price": num((b or {}).get("closePrice")) or num(info.get("lastClosePrice")),
        "chg": num((b or {}).get("fluctuationsRatio")),
        "mcap": mcap_krw(info.get("marketValue")),
        "per": num(info.get("per")), "cns_per": num(info.get("cnsPer")), "pbr": num(info.get("pbr")),
        "eps": num(info.get("eps")), "cns_eps": num(info.get("cnsEps")),
        "div": num(info.get("dividendYieldRatio")), "foreign": num(info.get("foreignRate")),
        "hi52": num(info.get("highPriceOf52Weeks")), "lo52": num(info.get("lowPriceOf52Weeks")),
        "industry": _industry(d, b),
        "cur": "KRW",
    }
    return out


def val_us(tk):
    b = None
    for suf in (".O", ".K", "", ".A"):
        b = get_json(f"https://api.stock.naver.com/stock/{tk}{suf}/basic")
        if b and b.get("closePrice"):
            break
        b = None
    if not b:
        return {}
    info = {x.get("code"): x.get("value") for x in b.get("stockItemTotalInfos", [])}
    return {
        "name_kr": b.get("stockName"), "price": num(b.get("closePrice")), "chg": num(b.get("fluctuationsRatio")),
        "mcap": (round(int(b["marketValueKrwRaw"]) / 1e8) if b.get("marketValueKrwRaw") else None),
        "mcap_usd": info.get("marketValue"),
        "per": num(info.get("per")), "pbr": num(info.get("pbr")), "eps": num(info.get("eps")),
        "div": num(info.get("dividendYieldRatio")),
        "hi52": num(info.get("highPriceOf52Weeks")), "lo52": num(info.get("lowPriceOf52Weeks")),
        "industry": info.get("industryGroupKor"), "exchange": (b.get("stockExchangeType") or {}).get("name"),
        "cur": "USD",
    }


def gh(path, tok, method="GET", body=None):
    url = f"https://api.github.com/repos/{REPO}/contents/{path}"
    if method == "GET":
        url += f"?ref={BRANCH}&t={time.time()}"
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body else None,
                                 headers={"Authorization": f"token {tok}", "Accept": "application/vnd.github+json",
                                          "User-Agent": "tg-build", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 404 and method == "GET":
            return None
        raise SystemExit(f"GitHub {method} {path}: {e.code} {e.read()[:200]}")


def put(path, obj, msg, tok):
    cur = gh(path, tok)
    body = {"message": msg, "branch": BRANCH,
            "content": base64.b64encode((json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n").encode()).decode()}
    if cur:
        body["sha"] = cur["sha"]
    r = gh(path, tok, "PUT", body)
    print(f"  ✓ {path}  {r['commit']['sha'][:7]}")


def main():
    mp = WORK / f"mentions_{DATE}.json"
    if not mp.exists():
        raise SystemExit(f"없음: {mp} — tg_collect.py 먼저")
    m = json.loads(mp.read_text())
    sp = WORK / f"summaries_{DATE}.json"
    summ = json.loads(sp.read_text()) if sp.exists() else {}
    if not summ:
        print("⚠ 요약 파일 없음 — 요약 없이 진행")

    stocks = m["stocks"]
    print(f"{DATE} · 종목 {len(stocks)} · 요약 {sum(1 for s in stocks if summ.get(s['key']))}건 · 밸류에이션 수집…")

    def work(s):
        v = val_kr(s["code"]) if s["mkt"] == "KR" else val_us(s["code"])
        return s["key"], v
    with ThreadPoolExecutor(6) as ex:
        vals = dict(ex.map(work, stocks))

    # 최근 30일 등장 이력 → 신규 여부
    tok = None if DRY else (os.environ.get("GITHUB_TOKEN") or (BASE / ".github_token").read_text().strip())
    idx = None
    if tok:
        cur = gh("telegram/data/index.json", tok)
        if cur:
            idx = json.loads(base64.b64decode(cur["content"]).decode())
    seen = {}
    for e in ((idx or {}).get("dates") or []):
        if e["date"] >= DATE:
            continue
        for k in e.get("keys", []):
            seen[k] = max(seen.get(k, ""), e["date"])

    # 요약이 "오탐"으로 시작하는 종목(이름이 다른 뜻으로 매칭된 것)은 카드에서 뺀다. '독립 언급 아님'은 남긴다.
    false_pos = [s for s in stocks if (summ.get(s["key"]) or "").strip().startswith("오탐")]
    if false_pos:
        print(f"  오탐 제외 {len(false_pos)}건: " + ", ".join(s["name"] for s in false_pos[:12]) + (" …" if len(false_pos) > 12 else ""))
    stocks = [s for s in stocks if not (summ.get(s["key"]) or "").strip().startswith("오탐")]

    items = []
    for s in stocks:
        v = vals.get(s["key"]) or {}
        pos52 = None
        if v.get("price") and v.get("hi52") and v.get("lo52") and v["hi52"] > v["lo52"]:
            pos52 = round((v["price"] - v["lo52"]) / (v["hi52"] - v["lo52"]) * 100)
        items.append({
            "key": s["key"], "mkt": s["mkt"], "code": s["code"], "name": s["name"], "market": s.get("market") or v.get("exchange"),
            "mentions": s["mentions"], "channels": s["channels"],
            "summary": (summ.get(s["key"]) or "").strip(),
            "last_seen": seen.get(s["key"]), "new": s["key"] not in seen,
            "val": {**v, "pos52": pos52},
        })

    out = {"date": DATE, "built_at": datetime.now(KST).isoformat(timespec="seconds"),
           "total_msgs": m.get("total_msgs"), "channels": m.get("channels"),
           "counts": {"stocks": len(items), "kr": sum(1 for i in items if i["mkt"] == "KR"),
                      "us": sum(1 for i in items if i["mkt"] == "US"), "new": sum(1 for i in items if i["new"]),
                      "summarized": sum(1 for i in items if i["summary"])},
           "items": items}
    print(f"  신규 {out['counts']['new']} · 밸류 확보 {sum(1 for i in items if i['val'].get('price'))}/{len(items)}")

    if DRY:
        (WORK / f"telegram_{DATE}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
        print(f"(dry-run) → {WORK}/telegram_{DATE}.json")
        return 0

    put(f"telegram/data/{DATE}.json", out, f"telegram: {DATE} 채널 언급 종목 {len(items)}", tok)
    dates = [e for e in ((idx or {}).get("dates") or []) if e["date"] != DATE]
    dates.append({"date": DATE, "stocks": len(items), "new": out["counts"]["new"], "msgs": m.get("total_msgs"),
                  "keys": [i["key"] for i in items]})
    dates.sort(key=lambda e: e["date"], reverse=True)
    put("telegram/data/index.json", {"updated_at": out["built_at"], "dates": dates[:90]}, f"telegram: index {DATE}", tok)
    print("\n완료 → https://whysosary-dot.github.io/stock-valuation/#telegram")
    return 0


if __name__ == "__main__":
    sys.exit(main())
