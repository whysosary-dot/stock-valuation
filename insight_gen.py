#!/usr/bin/env python3
"""
인사이트 생성 보조 — 스케줄 태스크(Claude)가 쓰는 두 개 명령.

  python3 insight_gen.py data sv      # Stock Valuation 탭 데이터 요약(텍스트) — Claude 가 읽고 인사이트를 쓴다
  python3 insight_gen.py data etf     # ETF Monitor 탭 데이터 요약
  python3 insight_gen.py write sv /path/insight.json   # 검증 후 stock-valuation 리포 insights/sv.json (+history) 에 푸시
  python3 insight_gen.py write etf /path/insight.json

JSON 형식 (Claude 가 작성):
{
 "tab": "sv" | "etf",
 "date": "YYYY-MM-DD",            # KST 생성일
 "as_of": "데이터 기준 2026-10-24 15:30 KST 종가",
 "headline": "한 줄 결론 (60자 이내)",
 "insights": [ {"title": "…", "body": "2~4문장. 숫자는 데이터/기사 출처가 있는 것만", "tags": ["섹터","종목"], "kind": "data|news|mixed"} ],   # 3~5개, 중요한 순
 "watch": ["내일·이번 주 확인할 것 1", "…"],      # 2~4개
 "sources": [ {"title": "기사 제목", "url": "https://…", "date": "YYYY-MM-DD"} ]   # 웹 검색에서 실제로 인용한 것만
}
규칙: 매수·매도 권유 금지. 등락률 나열 금지(패널 아래에 이미 있음) — '왜'와 '다음에 볼 것'을 쓴다.
"""
import sys, json, base64, urllib.request, datetime, statistics, re
from pathlib import Path
BASE = Path(__file__).resolve().parent
TOK = (BASE / ".github_token").read_text().strip()
PUB = "whysosary-dot/stock-valuation"; PRIV = "whysosary-dot/invest-private"; ETF = "whysosary-dot/etf-monitor"
KST = datetime.timezone(datetime.timedelta(hours=9))

def raw(repo, path):
    rq = urllib.request.Request(f"https://raw.githubusercontent.com/{repo}/main/{path}?t={datetime.datetime.now().timestamp()}",
                                headers={"Authorization": "token " + TOK, "User-Agent": "sv-insight"})
    return json.loads(urllib.request.urlopen(rq, timeout=60).read())

def pct(v): return "—" if v is None else f"{v:+.1f}%"

def data_sv():
    d = raw(PRIV, "sv/stocks.json")
    st = [s for s in d["stocks"] if isinstance(s.get("price_change_pct"), (int, float))]
    print(f"# Stock Valuation 데이터 요약 · 갱신 {d.get('updated_at')} · {len(st)}종목 · USDKRW {d.get('usdkrw')}")
    by = {}
    for s in st: by.setdefault(s.get("sector") or "미분류", []).append(s)
    print("\n## 섹터별 (평균 등락 · 종목수 · 상승/하락 · 최고/최저 종목)")
    for sec, arr in sorted(by.items(), key=lambda kv: -statistics.mean(x["price_change_pct"] for x in kv[1])):
        a = sorted(arr, key=lambda x: -x["price_change_pct"])
        up = sum(1 for x in arr if x["price_change_pct"] > 0)
        print(f"- {sec}: {pct(statistics.mean(x['price_change_pct'] for x in arr))} · {len(arr)}개 · ↑{up}/↓{len(arr)-up} · 최고 {a[0]['name']} {pct(a[0]['price_change_pct'])} / 최저 {a[-1]['name']} {pct(a[-1]['price_change_pct'])}")
    print("\n## 급등락 ±4% 이상 (종목 · 섹터 · 등락 · 시총 억원 · 52주 위치)")
    for s in sorted(st, key=lambda x: -abs(x["price_change_pct"])):
        if abs(s["price_change_pct"]) < 4: continue
        h = s.get("price_history") or []
        pos = None
        if h and s.get("price_native"):
            hi, lo = max(h), min(h); pos = (s["price_native"] - lo) / (hi - lo) * 100 if hi > lo else None
        v = s.get("val") or {}
        print(f"- {s['name']} ({s['ticker']}) · {s.get('sector')} · {pct(s['price_change_pct'])} · 시총 {s.get('market_cap_oku'):,.0f} · 52주위치 {'' if pos is None else f'{pos:.0f}%'} · PER {v.get('per') or v.get('cns_per') or '—'} PBR {v.get('pbr') or '—'}")
    print("\n## 1주·1개월 수익률 상위/하위 (price_history 주간 종가 기준 계산)")
    def ret(s, n):
        h = s.get("price_history") or []
        return None if len(h) <= n or not h[-1 - n] else (h[-1] / h[-1 - n] - 1) * 100
    for n, lab in ((1, "1주"), (4, "1개월")):
        arr = [(ret(s, n), s) for s in st]; arr = [(r, s) for r, s in arr if r is not None]
        arr.sort(key=lambda x: -x[0])
        print(f"- {lab} 상위: " + ", ".join(f"{s['name']} {pct(r)}" for r, s in arr[:6]))
        print(f"- {lab} 하위: " + ", ".join(f"{s['name']} {pct(r)}" for r, s in arr[-6:]))
    print("\n## 52주 신고가권(≥99%) / 신저가권(≤101%)")
    hi, lo = [], []
    for s in st:
        h = s.get("price_history") or []; c = s.get("price_native")
        if not h or not c: continue
        if c >= max(h) * 0.99: hi.append(s["name"])
        elif c <= min(h) * 1.01: lo.append(s["name"])
    print("- 신고가권: " + (", ".join(hi) or "없음")); print("- 신저가권: " + (", ".join(lo) or "없음"))
    fav = [s["name"] for s in d["stocks"] if s.get("favorite")]
    print("\n## 매수 리스트(즐겨찾기): " + (", ".join(fav) or "없음"))

def data_etf():
    d = raw(ETF, "etfs.json"); ins = d.get("insights") or {}
    print(f"# ETF Monitor 데이터 요약 · 갱신 {d.get('updated_at')} · {len(d['etfs'])}개 · 인사이트 계산 {ins.get('computed_at')}")
    print("\n## 규칙 기반 요약(verdict)"); [print("- " + v) for v in ins.get("verdict", [])]
    print("\n## 섹터 랭킹 (1주/1개월/3개월/1년)")
    for s in ins.get("sector_rank", []):
        print(f"- {s.get('sector')} ({s.get('n')}): {pct(s.get('week'))} / {pct(s.get('month'))} / {pct(s.get('quarter'))} / {pct(s.get('year'))}")
    for k, lab in (("top_week", "1주 상위"), ("bot_week", "1주 하위"), ("top_year", "1년 상위"), ("bot_year", "1년 하위"), ("structural", "구조적 강세"), ("weakness", "구조적 약세"), ("accel", "모멘텀 가속"), ("reversal", "조정 진입"), ("rebound", "저점 반등")):
        arr = ins.get(k) or []
        if arr: print(f"\n## {lab}: " + ", ".join(f"{x.get('name')}({x.get('ticker')}) {pct((x.get('r') or {}).get('week'))}/1y {pct((x.get('r') or {}).get('year'))}" for x in arr[:8]))
    print("\n## 크로스에셋")
    for c in ins.get("cross", []): print(f"- {c.get('label')} {c.get('ticker')}: 1주 {pct((c.get('r') or {}).get('week'))} · 1개월 {pct((c.get('r') or {}).get('month'))} · 3개월 {pct((c.get('r') or {}).get('quarter'))} · 52주위치 {c.get('pos52')}")
    r = ins.get("risk") or {}; print(f"- 위험선호 스프레드: 1주 {r.get('week')} · 1개월 {r.get('month')} · 3개월 {r.get('quarter')}")
    f = ins.get("flows") or {}
    print(f"\n## 국내 ETF 자금흐름 ({f.get('prev_date')}→{f.get('date')})")
    for g in f.get("groups", []): print(f"- {g.get('group')}: 순유입 {g.get('flow')}억 ({g.get('pct')}%) · 최대 {g.get('best')} / 최소 {g.get('worst')}")
    print("- 유입 TOP: " + ", ".join(f"{x.get('name')} {x.get('flow'):+,.0f}억" for x in f.get("top", [])[:6]))
    print("- 유출 TOP: " + ", ".join(f"{x.get('name')} {x.get('flow'):+,.0f}억" for x in f.get("bot", [])[:6]))
    print("\n## 규칙 기반 해설(narrative)"); [print(f"- {n.get('title')}: {n.get('body')[:300]}") for n in ins.get("narrative", [])]
    s = ins.get("sentiment"); 
    if s: print("\n## 심리지표: " + json.dumps(s, ensure_ascii=False)[:600])

def gh_put(path, text, msg):
    H = {"Authorization": "token " + TOK, "Accept": "application/vnd.github+json", "User-Agent": "sv-insight", "Content-Type": "application/json"}
    api = f"https://api.github.com/repos/{PUB}/contents/{path}"
    sha = None
    try:
        sha = json.loads(urllib.request.urlopen(urllib.request.Request(api + "?ref=main", headers=H), timeout=30).read())["sha"]
    except urllib.error.HTTPError as e:
        if e.code != 404: raise
    body = {"message": msg, "content": base64.b64encode(text.encode()).decode(), "branch": "main"}
    if sha: body["sha"] = sha
    r = urllib.request.urlopen(urllib.request.Request(api, method="PUT", data=json.dumps(body).encode(), headers=H), timeout=60)
    return json.loads(r.read())["commit"]["sha"][:7]

def write(tab, path):
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    assert d.get("tab") == tab, "tab 불일치"
    assert re.match(r"^\d{4}-\d{2}-\d{2}$", d.get("date", "")), "date 형식"
    assert d.get("headline") and 3 <= len(d.get("insights", [])) <= 6, "headline/insights 3~6개"
    for it in d["insights"]:
        assert it.get("title") and it.get("body"), "insight title/body"
    for s in d.get("sources", []):
        assert str(s.get("url", "")).startswith("http"), "source url"
    bad = [w for w in ("매수 추천", "매도 추천", "사세요", "파세요", "적극 매수") if w in json.dumps(d, ensure_ascii=False)]
    assert not bad, f"권유 표현 금지: {bad}"
    d["generated_at"] = datetime.datetime.now(KST).isoformat(timespec="seconds")
    txt = json.dumps(d, ensure_ascii=False, indent=1) + "\n"
    c1 = gh_put(f"insights/{tab}.json", txt, f"insight: {tab} {d['date']}")
    c2 = gh_put(f"insights/history/{tab}_{d['date']}.json", txt, f"insight: {tab} {d['date']} (history)")
    print(f"✓ insights/{tab}.json {c1} · history {c2} · {d['headline']}")

if __name__ == "__main__":
    cmd = sys.argv[1]; tab = sys.argv[2]
    if cmd == "data": (data_sv if tab == "sv" else data_etf)()
    elif cmd == "write": write(tab, sys.argv[3])
