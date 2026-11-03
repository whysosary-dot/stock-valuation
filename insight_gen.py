#!/usr/bin/env python3
"""
인사이트 생성 보조 — 스케줄 태스크(Claude)가 쓰는 두 개 명령.

  python3 insight_gen.py data sv      # Stock Valuation 탭 데이터 요약(텍스트) — Claude 가 읽고 인사이트를 쓴다
  python3 insight_gen.py data etf     # ETF Monitor 탭 데이터 요약
  python3 insight_gen.py write sv /path/insight.json   # 검증 후 stock-valuation 리포 insights/sv.json (+history) 에 푸시
  python3 insight_gen.py write etf /path/insight.json
  python3 insight_gen.py data chart    # Chart 탭(업종·섹터·매크로·스캔) 요약 → write chart /path.json (sections 5개)

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
import sys, json, base64, urllib.request, urllib.error, datetime, statistics, re
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

# ───────────── Chart 탭 (chart/data/*.json, 공개 리포) ─────────────
def raw_pub(path):
    # chart/data/* 는 2026-11-03 부터 이력 없는 orphan 브랜치 `data` 에 (루트 = 옛 chart/data). 없으면 main 폴백
    cands = [f"data/{path[len('chart/data/'):]}", f"main/{path}"] if path.startswith("chart/data/") else [f"main/{path}"]
    last = None
    for c in cands:
        try:
            rq = urllib.request.Request(f"https://raw.githubusercontent.com/{PUB}/{c}?t={datetime.datetime.now().timestamp()}", headers={"User-Agent": "sv-insight"})
            return json.loads(urllib.request.urlopen(rq, timeout=60).read())
        except urllib.error.HTTPError as e:
            last = e
            if e.code != 404: raise
    raise last

def data_chart():
    """Chart 탭 5개 섹션 요약 — Claude 가 읽고 sections 별 인사이트를 쓴다"""
    meta = raw_pub("chart/data/meta.json")
    print(f"# Chart 탭 데이터 요약 · 한국 {meta.get('kr_date')} · 미국 {meta.get('us_date')} · 갱신 {meta.get('updated_at')}")
    try:
        auto = raw_pub("chart/data/insights_auto.json")
        print("\n## 자동 데이터 요약(build_chart.py 산출 — 이 사실들은 이미 패널에 있으니 반복하지 말고 '왜'를 쓸 것)")
        for k, v in auto.items():
            print(f"### {k} ({v.get('date')})"); [print("-", l) for l in v.get("lines", [])]
    except Exception as e: print("(insights_auto 없음:", e, ")")
    for key, bench in (("groups_kr", "코스피"), ("groups_us", "SPY"), ("groups_macro", None)):
        d = raw_pub(f"chart/data/{key}.json")
        print(f"\n## {key} · {d.get('date')} · 카드 {len(d['cards'])}")
        for c in d["cards"]:
            rel = "" if c.get("rel_3m") is None else f" · 3개월 상대 {c['rel_3m']:+.0f}%p"
            print(f"- {c['name']}{(' ('+c['note']+')') if c.get('note') else ''}: {c['chg']:+.1f}% · 1개월 {c['ret_1m']:+.0f}% · 3개월 {c['ret_3m']:+.0f}%{rel} · 2년 {c['ret_2y']:+.0f}% · 52주고점대비 {c['off_hi52']:+.0f}% · 일{'▲' if c.get('d_above') else '▼'}주{'▲' if c.get('w_above') else '▼'}월{'▲' if c.get('m_above') else '▼'}{' ★뱃지변경' if (c.get('d_flip') or c.get('w_flip') or c.get('m_flip')) else ''}")
    for key in ("scan_kr", "scan_us"):
        d = raw_pub(f"chart/data/{key}.json")
        print(f"\n## {key} · {d.get('date')} · 유니버스 {d.get('universe')} · 히트 {len(d['hits'])}")
        for h in d["hits"]:
            print(f"- {h['name']} ({h['tk']}) · {h.get('sector')}/{h.get('industry')} · {h['chg']:+.1f}% · 거래량 {h.get('vol_ratio')}배 · RS {h.get('rs')} · 52주고점대비 {h.get('off_hi52')}% · 태그 {h.get('tags')} 주봉 {h.get('wtags')}" + (f" · 시총 {h.get('mcap')}" if h.get('mcap') else "") + (f" · 뉴스: {h.get('news')}" if h.get('news') else ""))
    try: data_index()
    except Exception as e: print("\n## index (인덱스 탭) — 수집 실패:", e)
    try: data_etf_daily()
    except Exception as e: print("\n## etf (ETF 탭) — 수집 실패:", e)

def data_index():
    """인덱스 탭(invest-index INDICATORS, JS 배열) — node 로 파싱해 지표별 최근 12개 값·직전비·YoY 요약"""
    import subprocess, tempfile
    js = r"""
const html = require('fs').readFileSync(process.argv[2],'utf8');
function extractArr(txt, marker){ const i=txt.indexOf(marker); if(i<0) return null; const start=txt.indexOf('[',i); let d=0,inStr=null,escp=false;
  for(let j=start;j<txt.length;j++){ const c=txt[j]; if(inStr){ if(escp)escp=false; else if(c==='\\')escp=true; else if(c===inStr)inStr=null; continue; }
    if(c==='"'||c==="'"||c==='`'){inStr=c;continue;} if(c==='/'&&txt[j+1]==='/'){ j=txt.indexOf('\n',j); if(j<0)break; continue; }
    if(c==='/'&&txt[j+1]==='*'){ j=txt.indexOf('*/',j)+1; if(j<1)break; continue; } if(c==='[')d++; else if(c===']'){ d--; if(!d){ try{ return new Function('return '+txt.slice(start,j+1))(); }catch(e){ return null; } } } } return null; }
const inds = extractArr(html, 'const INDICATORS') || [];
const pc=(a,b)=>(b&&a!=null)?((a/b-1)*100):null, sg=v=>v==null?'—':(v>0?'+':'')+v.toFixed(1)+'%';
for(const ind of inds){ const d=(ind.data||[]).filter(x=>x&&x.value!=null); if(!d.length) continue;
  const last=d[d.length-1], prev=d[d.length-2]; let yoy=null; const lab=String(last.label); const m=lab.match(/^(\d{4})(.*)$/);
  if(m){ const hit=d.find(x=>String(x.label)===String(+m[1]-1)+m[2]); if(hit) yoy=pc(last.value,hit.value); }
  console.log(`- ${ind.name} [${ind.unit||''}] · ${ind.source||''}${ind.category?' · '+ind.category:''} · 최신 ${lab} ${last.value} (직전比 ${prev?sg(pc(last.value,prev.value)):'—'}, YoY ${sg(yoy)})`);
  console.log('  최근: ' + d.slice(-12).map(x=>`${x.label} ${x.value}${x.note?'('+String(x.note).slice(0,30)+')':''}`).join(' | '));
  if(ind.warn) console.log('  ⚠ '+ind.warn); if(ind.desc) console.log('  설명: '+String(ind.desc).slice(0,200)); }
"""
    rq = urllib.request.Request("https://raw.githubusercontent.com/whysosary-dot/invest-index/main/index.html?t=%s" % datetime.datetime.now().timestamp(), headers={"User-Agent": "sv-insight"})
    html = urllib.request.urlopen(rq, timeout=60).read().decode("utf-8")
    with tempfile.TemporaryDirectory() as td:
        hp = Path(td, "ii.html"); hp.write_text(html, encoding="utf-8"); jp = Path(td, "x.js"); jp.write_text(js, encoding="utf-8")
        out = subprocess.run(["node", str(jp), str(hp)], capture_output=True, text=True, timeout=60)
    print("\n## index (invest-index 지표 — 인덱스 탭) · 각 지표의 최신값·직전비·YoY·최근 12개")
    print(out.stdout.strip() or ("(파싱 실패: " + out.stderr[:200] + ")"))

def data_etf_daily():
    """ETF 탭(chart/data/etf_daily.json) — ETF 별 일·주·월·3개월 수익률 표 (섹션 'etf' 근거)"""
    d = raw_pub("chart/data/etf_daily.json")
    rows = []
    for e in d.get("etfs", []):
        c = e.get("price_history_3m") or []
        if len(c) < 23: continue
        last = c[-1]
        rows.append((e.get("category"), e.get("sector"), e.get("name"), e.get("ticker"), e.get("price_change_pct"), (last / c[-6] - 1) * 100, (last / c[-23] - 1) * 100, (last / c[0] - 1) * 100))
    pa = sorted(x.get("priced_at") or "" for x in d.get("etfs", []))
    print(f"\n## etf (ETF 탭 · 일일 갱신) · 가격 {pa[len(pa)//2] if pa else '?'} · {len(rows)}개 · 열: 분류 | 섹터 | 이름 (티커) | 일 | 주 | 월 | 3개월")
    for r in sorted(rows, key=lambda x: (x[0] or "", x[1] or "", -(x[5] or 0))):
        print(f"- {r[0]} | {r[1]} | {r[2]} ({r[3]}) | {r[4]:+.1f}% | {r[5]:+.1f}% | {r[6]:+.1f}% | {r[7]:+.1f}%")

CHART_SECTIONS = ("kr", "us", "macro", "scan_kr", "scan_us", "index", "etf")
def write_chart(path):
    """JSON: {"tab":"chart","date":"YYYY-MM-DD","as_of":"…","sections":{"kr":{headline,insights[],watch[],sources[]}, "us":…, "macro":…, "scan_kr":…, "scan_us":…, "index":…(인덱스 탭 지표)}}
       섹션당 insights 2~4개. 등락률 나열 금지(데이터 요약이 이미 있음) — '왜'와 '다음에 볼 것'."""
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    assert d.get("tab") == "chart", "tab 불일치"
    assert re.match(r"^\d{4}-\d{2}-\d{2}$", d.get("date", "")), "date 형식"
    secs = d.get("sections") or {}
    assert all(k in secs for k in CHART_SECTIONS), f"sections 5개 필요: {CHART_SECTIONS}"
    for k in CHART_SECTIONS:
        sec = secs[k]
        assert sec.get("headline") and 2 <= len(sec.get("insights", [])) <= 5, f"{k}: headline/insights 2~5개"
        for it in sec["insights"]: assert it.get("title") and it.get("body"), f"{k}: insight title/body"
        for s_ in sec.get("sources", []): assert str(s_.get("url", "")).startswith("http"), f"{k}: source url"
    bad = [w for w in ("매수 추천", "매도 추천", "사세요", "파세요", "적극 매수") if w in json.dumps(d, ensure_ascii=False)]
    assert not bad, f"권유 표현 금지: {bad}"
    d["generated_at"] = datetime.datetime.now(KST).isoformat(timespec="seconds")
    txt = json.dumps(d, ensure_ascii=False, indent=1) + "\n"
    c1 = gh_put("insights/chart.json", txt, f"insight: chart {d['date']}")
    c2 = gh_put(f"insights/history/chart_{d['date']}.json", txt, f"insight: chart {d['date']} (history)")
    print(f"✓ insights/chart.json {c1} · history {c2}")

if __name__ == "__main__":
    cmd = sys.argv[1]; tab = sys.argv[2]
    if cmd == "data": (data_chart if tab == "chart" else data_sv if tab == "sv" else data_etf)()
    elif cmd == "write": (write_chart(sys.argv[3]) if tab == "chart" else write(tab, sys.argv[3]))
