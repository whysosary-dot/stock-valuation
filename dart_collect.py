#!/usr/bin/env python3
"""
DART 탭 공시 수집기 — 한국(DART) + 미국(SEC EDGAR) + 일본(TDnet) 관심기업 공시를 모아
dart-monitoring 저장소의 data/disclosures.json 으로 푸시한다.

왜 이게 필요한가: GitHub Pages 에는 서버가 없어 브라우저가 DART 를 직접 못 부르고(CORS),
페이지가 쓰던 공개 CORS 프록시(corsproxy.io / allorigins)는 2026-09 현재 둘 다 죽었다.
그래서 맥에서 주기적으로 수집해 정적 JSON 으로 내려놓고, 페이지는 그걸 읽는다.

관심기업은 dart-monitoring/watchlist.json (페이지의 ☁️ 동기화가 쓰는 파일):
  한국: {"corp_code":"00808022", "corp_name":"메지온", "stock_code":"140410"}
  미국: {"corp_code":"US:NVDA",  "corp_name":"NVDA", "corp_cls":"US"}
  일본: {"corp_code":"JP:6524",  "corp_name":"6524", "corp_cls":"JP"}

출력 항목은 DART list.json 의 필드 모양을 그대로 따른다 (페이지 렌더러가 그 모양을 기대함).
해외 항목은 pblntf_ty 를 "US"/"JP" 로, rcept_no 를 "US:<accession>"/"JP:<pdf명>" 으로, url 을 추가로 넣는다.

사용: python3 dart_collect.py [--days 90] [--dry-run]
"""
import sys, re, json, base64, datetime, hashlib, time, urllib.request, urllib.error, html as htmlmod
from pathlib import Path

BASE = Path(__file__).parent.resolve()
REPO = "whysosary-dot/dart-monitoring"
BRANCH = "main"
OUT_PATH = "data/disclosures.json"
DRY = "--dry-run" in sys.argv
DAYS = 90
if "--days" in sys.argv:
    DAYS = int(sys.argv[sys.argv.index("--days") + 1])
JP_DAYS = 7   # TDnet 은 일별 HTML 을 긁어야 해서 짧게

DART_KEY_FILE = BASE / ".dart_api_key"
UA_SEC = "DartMonitoring/1.0 (admin@example.com)"      # SEC 는 식별용 UA 필수 (개인 이메일 대신 placeholder)
UA_WEB = "Mozilla/5.0 (Macintosh) dart-monitoring"


# ───────────────────────── 공통 ─────────────────────────
def token():
    import os
    t = os.environ.get("GH_PAT") or os.environ.get("GITHUB_TOKEN")   # GitHub Actions (타 저장소 푸시엔 PAT 필요)
    if t:
        return t.strip()
    f = BASE / ".github_token"
    if not f.exists():
        raise SystemExit(f"깃허브 토큰 없음: {f}")
    return f.read_text().strip()


def dart_key():
    import os
    if os.environ.get("DART_API_KEY"):          # GitHub Actions secret
        return os.environ["DART_API_KEY"].strip()
    if not DART_KEY_FILE.exists():
        raise SystemExit(f"DART API 키 파일 없음: {DART_KEY_FILE}\n"
                         f"  → 이 파일에 OpenDART 인증키 한 줄만 넣어두면 된다.")
    return DART_KEY_FILE.read_text().strip()


def http(url, ua=UA_WEB, timeout=30, retries=2):
    last = None
    for i in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept-Encoding": "identity"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:
            last = e
            time.sleep(1 + i)
    raise last


def gh_get(path, tok):
    req = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}/contents/{path}?ref={BRANCH}&t={time.time()}",
        headers={"Authorization": f"token {tok}", "Accept": "application/vnd.github+json", "User-Agent": "sv"})
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            j = json.loads(r.read().decode())
        return json.loads(base64.b64decode(j["content"]).decode("utf-8")), j["sha"]
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None, None
        raise


def gh_put(path, obj, sha, msg, tok):
    body = {"message": msg, "branch": BRANCH,
            "content": base64.b64encode((json.dumps(obj, ensure_ascii=False) + "\n").encode()).decode()}
    if sha:
        body["sha"] = sha
    req = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}/contents/{path}", method="PUT",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"token {tok}", "Accept": "application/vnd.github+json",
                 "User-Agent": "sv", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def ymd(d):
    return d.strftime("%Y%m%d")


# ───────────────────────── 한국: DART ─────────────────────────
def collect_kr(watch, key, bgn, end):
    items, errs = [], []
    for w in watch:
        cc = w["corp_code"]
        page = 1
        while True:
            url = (f"https://opendart.fss.or.kr/api/list.json?crtfc_key={key}&corp_code={cc}"
                   f"&bgn_de={bgn}&end_de={end}&page_no={page}&page_count=100&sort=date&sort_mth=desc")
            try:
                d = json.loads(http(url, timeout=25).decode())
            except Exception as e:
                errs.append(f"DART {w.get('corp_name', cc)}: {str(e)[:60]}")
                break
            st = d.get("status")
            if st == "013":
                break                       # 조회 결과 없음
            if st != "000":
                errs.append(f"DART {w.get('corp_name', cc)}: {st} {d.get('message', '')[:50]}")
                break
            items.extend(d.get("list") or [])
            if page >= int(d.get("total_page") or 1):
                break
            page += 1
        time.sleep(0.15)
    return items, errs


# ───────────────────────── 미국: SEC EDGAR ─────────────────────────
_cik_cache = None
def sec_cik(ticker):
    global _cik_cache
    if _cik_cache is None:
        tk = json.loads(http("https://www.sec.gov/files/company_tickers.json", ua=UA_SEC).decode())
        _cik_cache = {v["ticker"].upper(): (v["cik_str"], v["title"]) for v in tk.values()}
    return _cik_cache.get(ticker.upper())


FORM_DESC = {
    "8-K": "수시보고 (Current Report)", "10-Q": "분기보고서", "10-K": "연차보고서",
    "4": "내부자 거래 (Form 4)", "3": "내부자 최초 보유 (Form 3)", "S-1": "증권신고서",
    "S-3": "일괄등록 신고", "424B4": "투자설명서", "SC 13D": "5% 이상 지분 (적극)",
    "SC 13G": "5% 이상 지분 (수동)", "DEF 14A": "주주총회 위임장", "13F-HR": "기관 보유 현황",
    "6-K": "외국기업 수시보고", "20-F": "외국기업 연차보고서",
}

def collect_us(watch, bgn, end):
    items, errs = [], []
    for w in watch:
        tkr = w["corp_code"].split(":", 1)[1]
        try:
            hit = sec_cik(tkr)
            if not hit:
                errs.append(f"EDGAR {tkr}: CIK 못 찾음")
                continue
            cik, title = hit
            sub = json.loads(http(f"https://data.sec.gov/submissions/CIK{cik:010d}.json", ua=UA_SEC).decode())
            f = sub["filings"]["recent"]
            n = len(f["accessionNumber"])
            for i in range(n):
                dt = f["filingDate"][i].replace("-", "")
                if dt < bgn or dt > end:
                    continue
                form = f["form"][i]
                acc = f["accessionNumber"][i]
                doc = f["primaryDocument"][i] if i < len(f.get("primaryDocument", [])) else ""
                desc = FORM_DESC.get(form, "")
                url = (f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/{doc}"
                       if doc else f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}")
                items.append({
                    "rcept_no": f"US:{acc}", "corp_code": w["corp_code"], "corp_cls": "US",
                    "corp_name": title, "stock_code": tkr,
                    "report_nm": f"[{form}] {desc}".strip(), "rcept_dt": dt,
                    "flr_nm": title, "pblntf_ty": "US", "rm": "", "url": url,
                })
        except Exception as e:
            errs.append(f"EDGAR {tkr}: {str(e)[:60]}")
        time.sleep(0.3)   # SEC 예의: 초당 10회 미만
    return items, errs


# ───────────────────────── 일본: TDnet ─────────────────────────
def tdnet_day(datestr):
    """하루치 적시개시 전부 (페이지 001~)"""
    out = []
    page = 1
    while page <= 20:
        url = f"https://www.release.tdnet.info/inbs/I_list_{page:03d}_{datestr}.html"
        try:
            html = http(url, timeout=25).decode("utf-8", "replace")
        except Exception:
            break
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S)
        got = 0
        for r in rows:
            cells = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", c)).strip()
                     for c in re.findall(r"<td[^>]*>(.*?)</td>", r, re.S)]
            if len(cells) < 4 or not re.fullmatch(r"\d{5}", cells[1]):
                continue
            pdf = re.findall(r'href="([^"]+\.pdf)"', r)
            out.append({"time": cells[0], "code": cells[1][:4], "name": htmlmod.unescape(cells[2]),
                        "title": htmlmod.unescape(cells[3]),
                        "url": f"https://www.release.tdnet.info/inbs/{pdf[0]}" if pdf else
                               f"https://www.release.tdnet.info/inbs/I_list_001_{datestr}.html",
                        "date": datestr})
            got += 1
        if got == 0:
            break
        # 다음 페이지 링크가 없으면 끝
        if f"I_list_{page + 1:03d}_{datestr}.html" not in html:
            break
        page += 1
        time.sleep(0.3)
    return out


def collect_jp(watch, days):
    items, errs = [], []
    codes = {w["corp_code"].split(":", 1)[1]: w for w in watch}
    today = datetime.date.today()
    for back in range(days):
        d = today - datetime.timedelta(days=back)
        if d.weekday() >= 5:
            continue
        ds = ymd(d)
        try:
            for it in tdnet_day(ds):
                if it["code"] in codes:
                    w = codes[it["code"]]
                    items.append({
                        "rcept_no": f"JP:{ds}:{it['code']}:{hashlib.md5(it['title'].encode()).hexdigest()[:8]}",
                        "corp_code": w["corp_code"], "corp_cls": "JP",
                        "corp_name": it["name"], "stock_code": it["code"],
                        "report_nm": it["title"], "rcept_dt": ds, "flr_nm": it["name"],
                        "pblntf_ty": "JP", "rm": "", "url": it["url"], "time": it["time"],
                    })
        except Exception as e:
            errs.append(f"TDnet {ds}: {str(e)[:60]}")
        time.sleep(0.4)
    return items, errs


# ───────────────────────── main ─────────────────────────
def main():
    tok = token()
    watch, _ = gh_get("watchlist.json", tok)
    if not isinstance(watch, list) or not watch:
        raise SystemExit("watchlist.json 이 비어 있음 — 페이지에서 관심기업 추가 후 ☁️ 동기화 필요")

    kr = [w for w in watch if not str(w.get("corp_code", "")).startswith(("US:", "JP:"))]
    us = [w for w in watch if str(w.get("corp_code", "")).startswith("US:")]
    jp = [w for w in watch if str(w.get("corp_code", "")).startswith("JP:")]
    end = datetime.date.today()
    bgn = end - datetime.timedelta(days=DAYS - 1)
    print(f"관심기업 한국 {len(kr)} · 미국 {len(us)} · 일본 {len(jp)}  |  기간 {ymd(bgn)}~{ymd(end)}")

    errs = []
    items_kr, e = collect_kr(kr, dart_key(), ymd(bgn), ymd(end)) if kr else ([], [])
    errs += e
    print(f"  한국 {len(items_kr)}건" + (f"  (오류 {len(e)})" if e else ""))
    items_us, e = collect_us(us, ymd(bgn), ymd(end)) if us else ([], [])
    errs += e
    print(f"  미국 {len(items_us)}건" + (f"  (오류 {len(e)})" if e else ""))
    items_jp, e = collect_jp(jp, JP_DAYS) if jp else ([], [])
    errs += e
    print(f"  일본 {len(items_jp)}건 (최근 {JP_DAYS}일)" + (f"  (오류 {len(e)})" if e else ""))
    for m in errs:
        print("   ⚠", m)

    items = items_kr + items_us + items_jp
    seen, dedup = set(), []
    for it in items:
        if it["rcept_no"] in seen:
            continue
        seen.add(it["rcept_no"]); dedup.append(it)
    dedup.sort(key=lambda x: (x.get("rcept_dt", ""), x.get("rcept_no", "")), reverse=True)

    payload = {
        "generated_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "range": {"bgn": ymd(bgn), "end": ymd(end), "jp_days": JP_DAYS},
        "counts": {"kr": len(items_kr), "us": len(items_us), "jp": len(items_jp)},
        "errors": errs,
        "items": dedup,
    }
    if DRY:
        print(f"\n(dry-run) 총 {len(dedup)}건 — 커밋 안 함")
        return 0

    old, sha = gh_get(OUT_PATH, tok)
    # 내용이 같으면 커밋 스킵 (generated_at 만 바뀌는 무의미한 커밋 방지)
    fp = lambda p: hashlib.md5(json.dumps(p.get("items", []), ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    if old and fp(old) == fp(payload) and old.get("errors") == errs:
        print(f"\n변경 없음 (총 {len(dedup)}건) — 커밋 스킵")
        return 0
    r = gh_put(OUT_PATH, payload, sha,
               f"dart: 공시 {len(dedup)}건 (KR {len(items_kr)} / US {len(items_us)} / JP {len(items_jp)})", tok)
    print(f"\n✓ 커밋 {r['commit']['sha'][:7]} — 총 {len(dedup)}건")
    return 0


if __name__ == "__main__":
    sys.exit(main())
