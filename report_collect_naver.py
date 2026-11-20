#!/usr/bin/env python3
"""리포트 탭 보조 수집기 — 네이버 증권 리서치 API (WiseReport 리포트 요약 페이지가 '접속장애'일 때 report_collect.py 가 호출)

  종목분석: https://m.stock.naver.com/api/research/company?page=N&pageSize=100  → 상세 /api/research/company/{id}
           (itemCode·itemName·brokerName·writeDate·title · 상세: opinion·goalPrice·priceAtWriteDate·content)
  산업분석: https://m.stock.naver.com/api/research/industry?page=N&pageSize=100 → 상세 /api/research/industry/{id}

WiseReport 와 다른 점(페이지에 표시되는 source 로 구분):
  - 목표가·의견 변경 표시(tp_flag/opinion_flag)는 WiseReport 가 주던 값이 아니라, 우리 report/data 의 최근 60일 파일에서
    같은 종목·같은 증권사의 직전 목표가·의견을 찾아 비교한 값이다(없으면 'none' / '이전 기록 없음').
  - 요약 불릿은 리포트 본문(content)의 첫 문단들을 잘라 만든다. 애널리스트 이름은 API 에 없어 빈 값.
  - 수록 증권사 범위가 WiseReport 보다 좁다(하루 30~90건).
"""
import json, re, time, datetime, urllib.request
from concurrent.futures import ThreadPoolExecutor

API = "https://m.stock.naver.com/api/research"
RAW = "https://raw.githubusercontent.com/whysosary-dot/stock-valuation/main/report/data/"
H = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36",
     "Accept": "application/json", "Accept-Language": "ko-KR,ko;q=0.9"}

def _get(url, tries=3):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=H), timeout=30) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except Exception:
            if i == tries - 1: return None
            time.sleep(1.5 * (i + 1))

def _list(kind, min_date):
    out = []
    for p in range(1, 15):
        rows = _get(f"{API}/{kind}?page={p}&pageSize=100") or []
        if not rows: break
        out += rows
        if rows[-1].get("writeDate", "") < min_date: break
        time.sleep(0.3)
    return out

def _clean(s):
    s = re.sub(r"<br\s*/?>", "\n", str(s or ""))
    s = re.sub(r"<[^>]+>", " ", s)
    s = s.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
    return s

def _bullets(content, n=3, maxlen=170):
    txt = _clean(content)
    parts = [re.sub(r"\s+", " ", x).strip(' "“”-•▶■●·') for x in re.split(r"\n+", txt)]
    parts = [x for x in parts if len(x) >= 12]
    out = []
    for x in parts:
        if x in out: continue
        out.append(x if len(x) <= maxlen else x[:maxlen - 1].rstrip() + "…")
        if len(out) >= n: break
    return out

def _num(s):
    try:
        v = float(re.sub(r"[^\d.\-]", "", str(s or "")))
        return int(v) if v else None
    except ValueError:
        return None

_OP_RANK = [("strong", 4), ("적극", 4), ("buy", 3), ("매수", 3), ("outperform", 3), ("overweight", 3), ("비중확대", 3),
            ("hold", 2), ("중립", 2), ("neutral", 2), ("marketperform", 2), ("market perform", 2), ("trading", 2),
            ("reduce", 1), ("underweight", 1), ("비중축소", 1), ("sell", 1), ("매도", 1)]
def _op_rank(op):
    s = (op or "").lower()
    for k, r in _OP_RANK:
        if k in s: return r
    return None

def _prev_map(dates):
    """최근 60일 report/data 파일에서 종목·증권사별 직전 (tp, opinion, date). dates(이번 수집 대상)는 제외."""
    idx = _get(RAW + "index.json") or {}
    prev = {}
    files = [e["date"] for e in idx.get("dates", []) if e.get("date") and e["date"] not in dates][:60]
    def load(d):
        j = _get(RAW + f"{d}.json")
        return d, (j or {}).get("company", [])
    with ThreadPoolExecutor(6) as ex:
        for d, comp in ex.map(load, files):
            for c in comp:
                k = (c.get("code"), c.get("broker"))
                if not k[0] or not k[1]: continue
                cur = prev.get(k)
                if cur is None or d > cur["date"]:
                    prev[k] = {"tp": c.get("tp"), "opinion": c.get("opinion"), "date": d}
    return prev

def collect(dates, attach_valuation=None, log=print):
    """dates: ['YYYY-MM-DD', ...] → {date: obj}  (report_collect.py 와 같은 스키마)"""
    dates = sorted(set(dates)); min_d = dates[0]
    comp_rows = [r for r in _list("company", min_d) if r.get("writeDate") in dates]
    ind_rows = [r for r in _list("industry", min_d) if r.get("writeDate") in dates]
    log(f"  네이버 리서치: 종목 {len(comp_rows)} · 산업 {len(ind_rows)} (대상 {', '.join(dates)})")
    def detail(kind, rid):
        j = _get(f"{API}/{kind}/{rid}")
        return rid, ((j or {}).get("researchContent") or {})
    with ThreadPoolExecutor(6) as ex:
        cd = dict(ex.map(lambda r: detail("company", r["researchId"]), comp_rows))
        idd = dict(ex.map(lambda r: detail("industry", r["researchId"]), ind_rows))
    prev = _prev_map(dates)
    results = {}
    for d in dates:
        comp = []
        for r in comp_rows:
            if r.get("writeDate") != d: continue
            x = cd.get(r["researchId"], {})
            code, broker = r.get("itemCode", ""), r.get("brokerName", "")
            tp = _num(x.get("goalPrice")); op = (x.get("opinion") or "").strip()
            pv = prev.get((code, broker))
            tp_flag, tp_txt = "none", "이전 기록 없음"
            if tp and pv and pv.get("tp"):
                tp_flag = "up" if tp > pv["tp"] else "down" if tp < pv["tp"] else "same"
                tp_txt = {"up": "목표주가 상향", "down": "목표주가 하향", "same": "목표주가 유지"}[tp_flag] + f" (직전 {pv['tp']:,} · {pv['date']})"
            elif not tp:
                tp_flag, tp_txt = "none", "목표주가 없음"
            op_flag, op_txt = "none", "이전 기록 없음"
            a, b = _op_rank(op), _op_rank((pv or {}).get("opinion"))
            if op and a and b:
                op_flag = "up" if a > b else "down" if a < b else "same"
                op_txt = {"up": "투자의견 상향", "down": "투자의견 하향", "same": "투자의견 유지"}[op_flag] + f" (직전 {pv['opinion']} · {pv['date']})"
            elif not op:
                op_flag, op_txt = "none", "투자의견 없음"
            comp.append({"name": r.get("itemName", ""), "code": code, "broker": broker, "analysts": "",
                         "opinion": op, "opinion_flag": op_flag, "opinion_flag_text": op_txt,
                         "tp": tp, "tp_flag": tp_flag, "tp_flag_text": tp_txt,
                         "close": _num(x.get("priceAtWriteDate")), "title": r.get("title", ""),
                         "summary": _bullets(x.get("content")), "pdf": x.get("attachUrl") or ""})
        ind = []
        for r in ind_rows:
            if r.get("writeDate") != d: continue
            x = idd.get(r["researchId"], {})
            ind.append({"sector": r.get("category") or "", "broker": r.get("brokerName", ""), "analysts": "",
                        "opinion": "", "prev_opinion": "", "title": r.get("title", ""),
                        "summary": _bullets(x.get("content")), "pdf": x.get("attachUrl") or ""})
        if attach_valuation and comp:
            try: attach_valuation(comp)
            except Exception as e: log(f"  (밸류에이션 첨부 실패: {e})")
        counts = {"company": len(comp), "industry": len(ind),
                  "tp_up": sum(1 for c in comp if c["tp_flag"] == "up"), "tp_down": sum(1 for c in comp if c["tp_flag"] == "down"),
                  "op_up": sum(1 for c in comp if c["opinion_flag"] == "up"), "op_down": sum(1 for c in comp if c["opinion_flag"] == "down"), "new": 0}
        results[d] = {"date": d, "collected_at": datetime.datetime.now().isoformat(timespec="seconds"),
                      "source": "naver-research-fallback (WiseReport 접속장애) · 변경표시는 자체 이력 비교",
                      "counts": counts, "company": comp, "industry": ind}
        log(f"  {d}: 기업 {counts['company']} · 산업 {counts['industry']} · TP↑{counts['tp_up']} TP↓{counts['tp_down']} 의견↑{counts['op_up']} 의견↓{counts['op_down']} (네이버)")
    return results

if __name__ == "__main__":
    import sys
    ds = sys.argv[1].split(",") if len(sys.argv) > 1 else [datetime.date.today().isoformat()]
    res = collect(ds)
    json.dump(next(iter(res.values()), {}), open("/tmp/report_naver_latest.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("→ /tmp/report_naver_latest.json")
