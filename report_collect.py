#!/usr/bin/env python3
"""
📑 Report 탭 수집기 — FnGuide WiseReport '리포트서머리' (증권사 리포트 목록) 정리.

소스: https://comp.wisereport.co.kr/bsfn/wiseReport/summary/ReportSummary.aspx
  fmt=1 기업 리포트 (기업명/코드, 기관/작성자, 투자의견, 목표주가, 전일수정주가, 제목, 요약, 변동 플래그)
  fmt=2 산업 리포트 (산업명, 기관/작성자, 투자의견, 이전의견, 제목, 요약)
  ee=YYYY-MM-DD 로 일자 지정. 페이지의 날짜 드롭다운이 최근 5개 발간일을 준다 (월요일자 리포트가 금요일 저녁에 미리 올라오기도 함).

동작: 드롭다운의 최근 5개 일자를 전부 다시 받아(멱등) report/data/<date>.json 으로 저장하고
      report/data/index.json 에 일자별 건수를 갱신한다. 내용이 같으면 커밋하지 않는다.

출력 스키마 (report/data/<date>.json):
{
  "date": "2026-09-17", "collected_at": "...",
  "counts": {"company": 48, "industry": 12, "tp_up": 4, "tp_down": 4, "op_up": 1, "op_down": 0, "new": 0},
  "company": [{"name","code","broker","analysts","opinion","opinion_flag","tp","tp_flag","close","title","summary":[...]}],
  "industry": [{"sector","broker","analysts","opinion","prev_opinion","title","summary":[...]}]
}
사용: python3 report_collect.py [--dry-run] [--dates=2026-09-17,2026-09-16]
"""
import sys, re, json, base64, html, time, datetime, hashlib
import urllib.request, urllib.error
from pathlib import Path

BASE   = Path(__file__).parent.resolve()
REPO   = "whysosary-dot/stock-valuation"
BRANCH = "main"
DRY    = "--dry-run" in sys.argv
URL    = "https://comp.wisereport.co.kr/bsfn/wiseReport/summary/ReportSummary.aspx"
H      = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36",
          "Accept-Language": "ko-KR,ko;q=0.9"}

FLAG = {"typ0": "none", "typ1": "up", "typ2": "same", "typ3": "down", "typ4": "new"}


def token():
    f = BASE / ".github_token"
    if not f.exists():
        raise SystemExit("깃허브 토큰 없음")
    return f.read_text().strip()


def fetch(fmt, date=None):
    q = f"?cmp_cd=005930&fmt={fmt}"
    if date:
        q += f"&ee={date}"
    for i in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(URL + q, headers=H), timeout=30) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:
            if i == 2:
                raise
            time.sleep(1.5)


def clean(s):
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s).replace("&nbsp", " ")
    return re.sub(r"\s+", " ", s).strip()


def bullets(cell):
    """요약 셀 → ▶ 로 나뉜 문장 리스트"""
    t = re.sub(r'<span class="comment_text">▶</span>', "\n▶", cell)
    t = clean(t.replace("\n", " \n "))
    parts = [p.strip(" ▶") for p in t.split("▶")]
    return [p for p in parts if p]


def flag_of(cell):
    m = re.search(r"float-left (typ\d)' title='([^']*)'", cell)
    if not m:
        return "none", ""
    t = m.group(2)
    # 아이콘 클래스보다 title 문구가 확실하다 (범례: 변동없음·신규·이전대비상향·이전대비하향)
    if "신규" in t:
        f = "new"
    elif "상향" in t:
        f = "up"
    elif "하향" in t:
        f = "down"
    elif "변동없음" in t:
        f = "same"
    elif "없음" in t:
        f = "none"
    else:
        f = FLAG.get(m.group(1), "none")
    return f, t


def num(s):
    s = clean(s).replace(",", "")
    try:
        return int(float(s)) if s and s not in ("-", "") else None
    except ValueError:
        return None


def parse_company(h):
    i = h.find('class="Summary_list"')
    seg = h[i:] if i >= 0 else h
    rows = re.findall(r'<tr height="40px" class="(?:itm_t1|alt_t1)">([\s\S]*?)</tr>', seg)
    out = []
    for r in rows:
        cells = re.findall(r"<t[hd][^>]*>([\s\S]*?)</t[hd]>", r)
        if len(cells) < 7:
            continue
        nm = re.search(r'title="([^"]*)\((\d{6})\)"', cells[0])
        br = re.search(r'title="([^"]*)\[([^\]]*)\]"', cells[1])
        op_flag, op_flag_txt = flag_of(cells[2])
        tp_flag, tp_flag_txt = flag_of(cells[3])
        opinion = clean(re.sub(r"<div class='float-left[\s\S]*?</div>", "", cells[2]))
        tp = num(re.sub(r"<div class='float-left[\s\S]*?</div>", "", cells[3]))
        out.append({
            "name": nm.group(1).strip() if nm else clean(cells[0]),
            "code": nm.group(2) if nm else "",
            "broker": br.group(1).strip() if br else clean(cells[1]),
            "analysts": br.group(2).strip() if br else "",
            "opinion": opinion,
            "opinion_flag": op_flag if opinion else "none",
            "opinion_flag_text": op_flag_txt,
            "tp": tp,
            "tp_flag": tp_flag if tp else "none",
            "tp_flag_text": tp_flag_txt,
            "close": num(cells[4]),
            "title": clean(cells[5]),
            "summary": bullets(cells[6]),
        })
    return out


def parse_industry(h):
    i = h.find('class="Summary_list"')
    seg = h[i:] if i >= 0 else h
    rows = re.findall(r'<tr height="40px" class="(?:itm_t1|alt_t1)">([\s\S]*?)</tr>', seg)
    out = []
    for r in rows:
        cells = re.findall(r"<t[hd][^>]*>([\s\S]*?)</t[hd]>", r)
        if len(cells) < 6:
            continue
        br = re.search(r"<span>([^<]*)</span>\s*<br\s*/?>\s*\[([^\]]*)\]", cells[1])
        out.append({
            "sector": clean(cells[0]),
            "broker": br.group(1).strip() if br else clean(cells[1]),
            "analysts": br.group(2).strip() if br else "",
            "opinion": clean(cells[2]),
            "prev_opinion": clean(cells[3]),
            "title": clean(cells[4]),
            "summary": bullets(cells[5]),
        })
    return out


def dates_from(h):
    return [f"{d[:4]}-{d[4:6]}-{d[6:]}" for d in re.findall(r'<option[^>]*value="(\d{8})"', h)]


# ── GitHub ─────────────────────────────────────────────
def gh(path, tok, method="GET", body=None):
    url = f"https://api.github.com/repos/{REPO}/contents/{path}"
    if method == "GET":
        url += f"?ref={BRANCH}&t={time.time()}"
    req = urllib.request.Request(url, method=method,
                                 data=json.dumps(body).encode() if body else None,
                                 headers={"Authorization": f"token {tok}",
                                          "Accept": "application/vnd.github+json",
                                          "User-Agent": "report-collect",
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 404 and method == "GET":
            return None
        raise SystemExit(f"GitHub {method} {path}: {e.code} {e.read()[:200]}")


def put_if_changed(path, obj, msg, tok):
    cur = gh(path, tok)
    raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n"
    if cur:
        old = base64.b64decode(cur["content"]).decode("utf-8", "replace")
        try:
            # 수집시각만 다른 경우는 건너뜀
            o, n = json.loads(old), json.loads(raw)
            o.pop("collected_at", None); n.pop("collected_at", None)
            if o == n:
                print(f"  · {path} 변경 없음")
                return False
        except Exception:
            pass
    body = {"message": msg, "branch": BRANCH, "content": base64.b64encode(raw.encode()).decode()}
    if cur:
        body["sha"] = cur["sha"]
    r = gh(path, tok, "PUT", body)
    print(f"  ✓ {path}  {r['commit']['sha'][:7]}")
    return True


def main():
    arg = next((a for a in sys.argv if a.startswith("--dates=")), None)
    first = fetch(1)
    dates = dates_from(first)
    if arg:
        dates = arg.split("=", 1)[1].split(",")
    if not dates:
        raise SystemExit("발간일자 드롭다운을 찾지 못함 — 페이지 구조 변경?")
    print(f"발간일자 {len(dates)}개: {', '.join(dates)}")

    tok = None if DRY else token()
    results = {}
    for d in dates:
        hc = fetch(1, d); time.sleep(0.6)
        hi = fetch(2, d); time.sleep(0.6)
        comp, ind = parse_company(hc), parse_industry(hi)
        counts = {
            "company": len(comp), "industry": len(ind),
            "tp_up": sum(1 for x in comp if x["tp_flag"] == "up"),
            "tp_down": sum(1 for x in comp if x["tp_flag"] == "down"),
            "op_up": sum(1 for x in comp if x["opinion_flag"] == "up"),
            "op_down": sum(1 for x in comp if x["opinion_flag"] == "down"),
            "new": sum(1 for x in comp if "new" in (x["tp_flag"], x["opinion_flag"]) or "신규" in (x["tp_flag_text"] + x["opinion_flag_text"])),
        }
        obj = {"date": d, "collected_at": datetime.datetime.now().isoformat(timespec="seconds"),
               "source": f"{URL}?fmt=1&ee={d}", "counts": counts, "company": comp, "industry": ind}
        results[d] = obj
        print(f"  {d}: 기업 {counts['company']} · 산업 {counts['industry']} · TP↑{counts['tp_up']} TP↓{counts['tp_down']} 의견↑{counts['op_up']} 의견↓{counts['op_down']}")

    if DRY:
        Path("/tmp/report_latest.json").write_text(json.dumps(results[dates[0]], ensure_ascii=False, indent=1))
        print("(dry-run — /tmp/report_latest.json)")
        return 0

    for d, obj in results.items():
        if obj["counts"]["company"] + obj["counts"]["industry"] == 0:
            print(f"  · {d} 0건 — 저장 안 함")
            continue
        put_if_changed(f"report/data/{d}.json", obj, f"report: {d} 리포트 {obj['counts']['company']}건", tok)

    cur = gh("report/data/index.json", tok)
    idx = {}
    if cur:
        try:
            idx = json.loads(base64.b64decode(cur["content"]).decode())
        except Exception:
            idx = {}
    dl = {e["date"]: e for e in idx.get("dates", [])} if isinstance(idx, dict) else {}
    for d, obj in results.items():
        if obj["counts"]["company"] + obj["counts"]["industry"] > 0:
            dl[d] = {"date": d, **obj["counts"]}
    out = {"updated_at": datetime.datetime.now().isoformat(timespec="seconds"),
           "dates": sorted(dl.values(), key=lambda e: e["date"], reverse=True)[:120]}
    put_if_changed("report/data/index.json", out, "report: index 갱신", tok)
    print("\n완료 → https://whysosary-dot.github.io/stock-valuation/#report")
    return 0


if __name__ == "__main__":
    sys.exit(main())
