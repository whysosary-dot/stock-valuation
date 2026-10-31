#!/usr/bin/env python3
"""
Chart 탭 데이터 빌더 — 시장 그룹판 + 일봉·주봉 스캔 (드러켄밀러·짐 라이트너식 '아이디어 포착기')

  python3 chart/build_chart.py kr      # 한국: 업종 그룹판(시총가중 지수) + KR 스캔   (평일 16:40 KST)
  python3 chart/build_chart.py us      # 미국: ETF 그룹판 + 매크로판 + US 스캔(S&P 1500) (평일 06:40 KST)

산출물 (chart/data/)
  groups_kr.json / groups_us.json / groups_macro.json : 카드별 주봉 종가(2년)·30주선·뱃지·수익률
  scan_kr.json / scan_us.json      : 오늘 스캔 결과 + 히트 종목 300일 OHLCV (+ 주봉)
  scan/<date>_<mkt>.json           : 이력 (90일 보관)
  meta.json                        : 갱신 시각·유니버스 크기

데이터 출처: 네이버(업종 분류·일봉, 무료) · 야후 파이낸스(미국·매크로, 무료) · 위키피디아(S&P 500/400/600 구성종목)
Claude 토큰 0 — GitHub Actions 에서 실행.
"""
import sys, json, os, re, math, time, datetime, io
from concurrent.futures import ThreadPoolExecutor
import requests

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
os.makedirs(os.path.join(DATA, "scan"), exist_ok=True)
H = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36"}
KST = datetime.timezone(datetime.timedelta(hours=9))
TODAY = datetime.datetime.now(KST).strftime("%Y-%m-%d")

# ───────────────────────── 공통 수학 ─────────────────────────
def sma(a, n):
    out = [None] * len(a); s = 0.0
    for i, v in enumerate(a):
        s += v
        if i >= n: s -= a[i - n]
        if i >= n - 1: out[i] = s / n
    return out

def pct(a, b):
    return None if (a is None or b is None or b == 0) else (a / b - 1) * 100

def weekly_bars(dates, o, h, l, c, v):
    """일봉 → 주봉 (ISO 주 기준, 마지막 주는 미완성일 수 있음)"""
    out = []
    cur = None
    for i, d in enumerate(dates):
        y, w, _ = datetime.date.fromisoformat(d).isocalendar()
        key = (y, w)
        if cur is None or cur["k"] != key:
            if cur: out.append(cur)
            cur = {"k": key, "d": d, "o": o[i], "h": h[i], "l": l[i], "c": c[i], "v": v[i]}
        else:
            cur["h"] = max(cur["h"], h[i]); cur["l"] = min(cur["l"], l[i]); cur["c"] = c[i]; cur["v"] += v[i]; cur["d"] = d
    if cur: out.append(cur)
    return out

def monthly_close(dates, c):
    out = []; cur = None
    for d, x in zip(dates, c):
        key = d[:7]
        if cur is None or cur[0] != key:
            if cur: out.append(cur)
            cur = [key, x]
        else: cur[1] = x
    if cur: out.append(cur)
    return out

def rank_pct(values):
    """0~99 백분위 (높을수록 강함). None 은 None"""
    idx = [i for i, v in enumerate(values) if v is not None]
    srt = sorted(idx, key=lambda i: values[i])
    out = [None] * len(values); n = len(srt)
    for r, i in enumerate(srt): out[i] = int(r * 99 / max(1, n - 1))
    return out

def r(x, n=2):
    return None if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))) else round(x, n)

# ───────────────────────── 그룹 카드 ─────────────────────────
def group_card(name, sub, dates, closes, bench_closes=None, n_members=None, note=None):
    """2년 일봉 종가 → 카드 데이터. 뱃지: 일=50일선, 주=30주선, 월=10개월선 위/아래"""
    if len(closes) < 60: return None
    last = closes[-1]
    ma50 = sma(closes, 50)[-1]
    wk = weekly_bars(dates, closes, closes, closes, closes, [0] * len(closes))
    wc = [b["c"] for b in wk]; wd = [b["d"] for b in wk]
    ma30w = sma(wc, 30)
    mo = monthly_close(dates, closes); mc = [m[1] for m in mo]
    ma10m = sma(mc, 10)[-1] if len(mc) >= 10 else None
    def idx_ago(days):
        target = (datetime.date.fromisoformat(dates[-1]) - datetime.timedelta(days=days)).isoformat()
        j = max(i for i, d in enumerate(dates) if d <= target) if dates[0] <= target else 0
        return j
    i1m, i3m = idx_ago(30), idx_ago(91)
    ret_1m = pct(last, closes[i1m]); ret_3m = pct(last, closes[i3m])
    rel_3m = None
    if bench_closes and len(bench_closes) == len(closes):
        rel_3m = ret_3m - pct(bench_closes[-1], bench_closes[i3m])
    hi52 = max(closes[idx_ago(365):])
    chg = pct(last, closes[-2])
    tail = wc[-105:]; tail_ma = ma30w[-105:]; tail_d = wd[-105:]
    return {
        "name": name, "sub": sub, "n": n_members, "note": note,
        "last": r(last, 4), "chg": r(chg), "ret_1m": r(ret_1m), "ret_3m": r(ret_3m), "rel_3m": r(rel_3m, 1),
        "ret_2y": r(pct(last, closes[0])),
        "off_hi52": r(pct(last, hi52)),
        "d_above": None if ma50 is None else last > ma50,
        "w_above": None if ma30w[-1] is None else last > ma30w[-1],
        "m_above": None if ma10m is None else last > ma10m,
        "w_dates": [tail_d[0], tail_d[-1]], "w_close": [r(x, 4) for x in tail], "w_ma30": [r(x, 4) if x else None for x in tail_ma],
    }

# ───────────────────────── 스캔 ─────────────────────────
def scan_market(univ, series, bench, mkt):
    """univ: {tk: {name, sector, mcap(억 or $M), ...}}, series: {tk: (dates,o,h,l,c,v)} (동일 길이 아님)
       bench: (dates, closes) 지수. 반환 {hits:[...], rs:{tk:rs}}"""
    # RS 점수 = 0.4*3m + 0.2*6m + 0.2*9m + 0.2*12m 수익률의 시장 내 백분위
    tks = [tk for tk in univ if tk in series and len(series[tk][0]) >= 130]
    def ret_n(c, n): return pct(c[-1], c[-1 - n]) if len(c) > n else None
    score = []
    for tk in tks:
        c = series[tk][4]
        r3, r6, r9, r12 = ret_n(c, 63), ret_n(c, 126), ret_n(c, 189), ret_n(c, 252)
        parts = [(0.4, r3), (0.2, r6), (0.2, r9), (0.2, r12)]
        avail = [(w, x) for w, x in parts if x is not None]
        score.append(sum(w * x for w, x in avail) / sum(w for w, _ in avail) if avail else None)
    rs = dict(zip(tks, rank_pct(score)))
    bench_c = dict(zip(bench[0], bench[1]))
    hits = []
    for tk in tks:
        d, o, h, l, c, v = series[tk]
        if len(c) < 60 or c[-1] <= 0 or v[-1] <= 0: continue
        ma50 = sma(c, 50); ma200 = sma(c, 200); vma50 = sma(v, 50)
        prev = c[-2]; chg = pct(c[-1], prev); gap = pct(o[-1], prev)
        vr = (v[-1] / vma50[-1]) if vma50[-1] else None
        rng = (h[-1] - l[-1]); pos = ((c[-1] - l[-1]) / rng) if rng > 0 else 1.0
        hi52 = max(h[-252:]) if len(h) >= 252 else max(h)
        prev_hi52 = max(h[-253:-1]) if len(h) >= 253 else max(h[:-1])
        tags = []
        # ① 거래량 터진 갭 상승
        if gap is not None and gap >= 3 and chg >= 5 and vr and vr >= 3 and pos >= 0.6:
            tags.append("gap_up")
        # ② 200일선 돌파
        if ma200[-1] and ma200[-2] and prev < ma200[-2] and c[-1] > ma200[-1] and chg >= 2 and vr and vr >= 1.5:
            tags.append("ma200_break")
        # ③ 52주 신고가 (거래량 동반)
        if c[-1] >= prev_hi52 and chg > 0 and vr and vr >= 1.2:
            tags.append("new_high")
        # ④ RS 선두주 거래량 급증 (RS≥90, 50일선 위, +3% 이상, 거래량 2배)
        if rs.get(tk) is not None and rs[tk] >= 90 and ma50[-1] and c[-1] > ma50[-1] and chg >= 3 and vr and vr >= 2:
            tags.append("rs_leader")
        # 주봉: 30주선 상향 돌파 (이번 주), 주봉 거래량 급증 상승
        wk = weekly_bars(d, o, h, l, c, v)
        wtags = []
        if len(wk) >= 32:
            wc = [b["c"] for b in wk]; wv = [b["v"] for b in wk]
            m30 = sma(wc, 30); v10 = sma(wv, 10)
            if m30[-1] and m30[-2] and wk[-2]["c"] < m30[-2] and wc[-1] > m30[-1] and v10[-2] and wv[-1] >= 1.5 * v10[-2]:
                wtags.append("w_ma30_break")
            wchg = pct(wc[-1], wc[-2])
            if wchg is not None and wchg >= 8 and v10[-2] and wv[-1] >= 2.5 * v10[-2] and (rs.get(tk) or 0) >= 60 and (wk[-1]["h"] - wk[-1]["l"]) > 0 and (wc[-1] - wk[-1]["l"]) / (wk[-1]["h"] - wk[-1]["l"]) >= 0.6:
                wtags.append("w_vol_surge")
        if not tags and not wtags: continue
        u = univ[tk]
        n = 300
        # RS 라인용 지수 (같은 날짜 정렬)
        bseries = [bench_c.get(x) for x in d[-n:]]
        hits.append({
            "tk": tk, "name": u.get("name"), "sector": u.get("sector"), "industry": u.get("industry"), "mkt": mkt, "mkt_sub": u.get("mkt"),
            "mcap": u.get("mcap"), "tags": tags, "wtags": wtags, "rs": rs.get(tk),
            "price": r(c[-1], 4), "chg": r(chg), "gap": r(gap), "vol_ratio": r(vr, 1), "close_pos": r(pos, 2),
            "off_hi52": r(pct(c[-1], hi52)), "prev_close": r(prev, 4),
            "above_ma200": bool(ma200[-1] and c[-1] > ma200[-1]), "above_ma50": bool(ma50[-1] and c[-1] > ma50[-1]),
            "d": d[-n:], "o": [r(x, 4) for x in o[-n:]], "h": [r(x, 4) for x in h[-n:]], "l": [r(x, 4) for x in l[-n:]],
            "c": [r(x, 4) for x in c[-n:]], "v": [int(x) for x in v[-n:]], "bench": [r(x, 4) if x else None for x in bseries],
        })
    order = {"gap_up": 0, "new_high": 1, "ma200_break": 2, "rs_leader": 3}
    hits.sort(key=lambda x: (min([order[t] for t in x["tags"]] + [9]), -(x["rs"] or 0), -(x["chg"] or 0)))
    return hits, rs

# ───────────────────────── 한국 ─────────────────────────
KR_GROUPS = {
    "반도체": ["반도체와반도체장비"],
    "IT하드웨어·전자부품": ["전자장비와기기", "디스플레이장비및부품", "디스플레이패널", "핸드셋", "통신장비", "컴퓨터와주변기기", "전자제품", "사무용전자제품"],
    "2차전지·전기장비": ["전기제품", "전기장비"],
    "자동차": ["자동차", "자동차부품"],
    "조선·해운": ["조선", "해운사"],
    "방산·우주": ["우주항공과국방"],
    "기계·산업재": ["기계", "건축제품", "상업서비스와공급품", "복합기업", "무역회사와판매업체", "에너지장비및서비스"],
    "운송": ["항공사", "항공화물운송과물류", "도로와철도운송", "운송인프라"],
    "건설·건자재": ["건설", "건축자재"],
    "화학·소재": ["화학", "포장재", "종이와목재"],
    "철강·비철금속": ["철강", "비철금속"],
    "에너지·유틸리티": ["석유와가스", "가스유틸리티", "전기유틸리티", "복합유틸리티"],
    "바이오·헬스케어": ["제약", "생물공학", "건강관리장비와용품", "생명과학도구및서비스", "건강관리업체및서비스", "건강관리기술"],
    "인터넷·소프트웨어": ["소프트웨어", "IT서비스", "양방향미디어와서비스"],
    "게임": ["게임엔터테인먼트"],
    "미디어·엔터": ["방송과엔터테인먼트", "광고", "출판"],
    "화장품·의류·생활용품": ["화장품", "섬유,의류,신발,호화품", "가정용품", "가정용기기와용품", "가구", "레저용장비와제품", "문구류"],
    "음식료·유통": ["식품", "음료", "담배", "백화점과일반상점", "판매업체", "식품과기본식료품소매", "인터넷과카탈로그소매", "전문소매"],
    "레저·교육·서비스": ["호텔,레스토랑,레저", "교육서비스", "다각화된소비자서비스"],
    "금융": ["증권", "은행", "손해보험", "생명보험", "기타금융", "카드", "창업투자", "부동산"],
    "통신": ["무선통신서비스", "다각화된통신서비스"],
}
ETF_PAT = re.compile(r"(KODEX|TIGER|KBSTAR|RISE|ACE|SOL|PLUS|HANARO|KOSEF|ARIRANG|TIMEFOLIO|TREX|WOORI|KIWOOM|마이티|히어로즈|스팩|ETN|레버리지|인버스|ETF|BNK|DAISHIN|KCGI|VITA|UNICORN|에셋플러스|파워|KINDEX|KoAct|TRUSTON|RISE|1Q)", re.I)

def naver_json(url):
    for a in range(3):
        try:
            rq = requests.get(url, headers=H, timeout=20)
            if rq.status_code == 200 and rq.text.strip(): return rq.json()
        except Exception: pass
        time.sleep(1.5)
    return None

def kr_universe():
    groups = []
    for p in range(1, 6):
        j = naver_json(f"https://m.stock.naver.com/api/stocks/industry?page={p}&pageSize=40")
        if not j: break
        groups += j["groups"]
        if len(j["groups"]) < 40: break
    univ = {}
    def one(g):
        out = []
        for p in range(1, 30):
            j = naver_json(f"https://m.stock.naver.com/api/stocks/industry/{g['no']}?page={p}&pageSize=100")
            if not j or not j.get("stocks"): break
            for s in j["stocks"]:
                if s.get("stockEndType") != "stock": continue
                nm = s["stockName"]
                if ETF_PAT.search(nm) and g["name"] == "기타": continue
                try: mcap = float(str(s.get("marketValue", "0")).replace(",", "") or 0)
                except Exception: mcap = 0
                out.append((s["itemCode"], {"name": nm, "industry": g["name"], "mkt": "KOSPI" if s.get("sosok") == "0" else "KOSDAQ", "mcap": mcap}))
            if len(j["stocks"]) < 100: break
        return out
    with ThreadPoolExecutor(8) as ex:
        for res in ex.map(one, groups):
            for tk, u in res: univ[tk] = u
    g2sec = {ind: grp for grp, inds in KR_GROUPS.items() for ind in inds}
    for tk, u in univ.items(): u["sector"] = g2sec.get(u["industry"])
    return univ

def naver_daily(code, count=520):
    for a in range(3):
        try:
            rq = requests.get(f"https://fchart.stock.naver.com/sise.nhn?symbol={code}&timeframe=day&count={count}&requestType=0", headers=H, timeout=20)
            items = re.findall(r'<item data="([^"]+)"', rq.text)
            if items:
                d, o, h, l, c, v = [], [], [], [], [], []
                for it in items:
                    p = it.split("|")
                    if len(p) < 6 or not p[4] or float(p[4]) <= 0: continue
                    d.append(f"{p[0][:4]}-{p[0][4:6]}-{p[0][6:]}"); o.append(float(p[1])); h.append(float(p[2])); l.append(float(p[3])); c.append(float(p[4])); v.append(float(p[5]))
                return (d, o, h, l, c, v)
        except Exception: pass
        time.sleep(1)
    return None

def build_kr():
    print("KR universe …"); univ = kr_universe(); print(f"  {len(univ)} stocks")
    # 다운로드 대상: 시총 300억 이상 (업종 지수용) — 스캔은 1,000억 이상 + 거래대금 조건
    dl = [tk for tk, u in univ.items() if u["mcap"] >= 300]
    print(f"  download {len(dl)} daily series …")
    series = {}
    with ThreadPoolExecutor(12) as ex:
        for tk, s in zip(dl, ex.map(naver_daily, dl)):
            if s and len(s[0]) >= 60: series[tk] = s
    kospi = naver_daily("KOSPI", 520); kosdaq = naver_daily("KOSDAQ", 520)
    print(f"  got {len(series)} series; KOSPI last {kospi[0][-1]}")
    # 마지막 거래일 기준 정렬: 지수 날짜축에 맞춤
    axis = kospi[0]; aset = set(axis)
    if series and TODAY not in axis and max(s[0][-1] for s in series.values()) != axis[-1]:
        print("  ⚠ 지수와 종목 마지막 날짜 불일치")
    # 업종 지수 (시총가중, 지수 날짜축 위에서 가격상대 합성)
    cards = []
    bench_c = kospi[4]
    for grp in KR_GROUPS:
        members = [tk for tk, u in univ.items() if u["sector"] == grp and tk in series]
        if len(members) < 3: continue
        tot = sum(univ[tk]["mcap"] for tk in members)
        idx = [0.0] * len(axis); cover = [0.0] * len(axis)
        for tk in members:
            d, o, h, l, c, v = series[tk]; m = dict(zip(d, c)); base = None
            w = univ[tk]["mcap"] / tot
            lastp = None
            for i, day in enumerate(axis):
                p = m.get(day, lastp)
                if p is None: continue
                lastp = p
                if base is None: base = p
                idx[i] += w * (p / base); cover[i] += w
        closes = [idx[i] / cover[i] * 100 if cover[i] > 0.5 else None for i in range(len(axis))]
        # 앞쪽 None 제거
        k = next((i for i, x in enumerate(closes) if x is not None), 0)
        cd, cc, bc = axis[k:], closes[k:], bench_c[k:]
        card = group_card(grp, f"{len(members)}종목 · 시총가중", cd, cc, bc, n_members=len(members))
        if card:
            # 구성종목 명세 (카드 제목 클릭 → 목록): 시총 내림차순, 비중·등락·1/3개월 수익률
            mem = []
            for tk in members:
                c = series[tk][4]; u = univ[tk]
                mem.append({"code": tk, "name": u["name"], "ind": u.get("industry"), "mkt": u.get("mkt"),
                            "mcap": round(u["mcap"]), "w": round(u["mcap"] / tot * 100, 2),
                            "chg": round(pct(c[-1], c[-2]), 2) if len(c) > 1 else None,
                            "r1m": round(pct(c[-1], c[-22]), 1) if len(c) > 22 else None,
                            "r3m": round(pct(c[-1], c[-64]), 1) if len(c) > 64 else None})
            mem.sort(key=lambda x: -x["mcap"])
            card["members"] = mem
            inds = {}
            for m in mem: inds[m["ind"]] = inds.get(m["ind"], 0) + 1
            card["industries"] = sorted(inds.items(), key=lambda x: -x[1])
            cards.append(card)
    cards.sort(key=lambda x: -(x["rel_3m"] or -999))
    for nm, s in (("코스피", kospi), ("코스닥", kosdaq)):
        card = group_card(nm, "지수", s[0], s[4], None)
        if card: cards.insert(0 if nm == "코스닥" else 0, card)
    cards = [c for c in cards if c["name"] == "코스피"] + [c for c in cards if c["name"] == "코스닥"] + [c for c in cards if c["name"] not in ("코스피", "코스닥")]
    for c in cards: c["_date"] = kospi[0][-1]
    mark_flips(os.path.join(DATA, "groups_kr.json"), cards)
    for c in cards: c.pop("_date", None)
    json.dump({"date": kospi[0][-1], "generated_at": datetime.datetime.now(KST).isoformat(timespec="seconds"), "bench": "코스피", "cards": cards},
              open(os.path.join(DATA, "groups_kr.json"), "w"), ensure_ascii=False)
    print(f"  groups_kr: {len(cards)} cards")
    # 스캔: 시총 1,000억 이상 & 당일 거래대금 10억 이상
    scan_univ = {tk: u for tk, u in univ.items() if u["mcap"] >= 1000 and tk in series and series[tk][4][-1] * series[tk][5][-1] >= 1e9}
    hits, rs = scan_market(scan_univ, series, (kospi[0], kospi[4]), "KR")
    out = {"date": kospi[0][-1], "generated_at": datetime.datetime.now(KST).isoformat(timespec="seconds"), "market": "KR",
           "universe": len(scan_univ), "bench": "코스피", "hits": hits}
    json.dump(out, open(os.path.join(DATA, "scan_kr.json"), "w"), ensure_ascii=False)
    json.dump(out, open(os.path.join(DATA, "scan", f"{kospi[0][-1]}_kr.json"), "w"), ensure_ascii=False)
    print(f"  scan_kr: universe {len(scan_univ)} → hits {len(hits)}")
    return {"kr_date": kospi[0][-1], "kr_universe": len(scan_univ), "kr_hits": len(hits)}

# ───────────────────────── 미국 ─────────────────────────
US_ETFS = [  # (이름, 티커, 그룹)
    ("S&P 500", "SPY", "지수"), ("나스닥100", "QQQ", "지수"), ("러셀2000", "IWM", "지수"), ("S&P 500 동일가중", "RSP", "지수"),
    ("소프트웨어", "IGV", "섹터"), ("혁신성장(ARKK)", "ARKK", "섹터"), ("에너지", "XLE", "섹터"), ("석유·가스 개발", "XOP", "섹터"),
    ("헬스케어", "XLV", "섹터"), ("금광", "GDX", "섹터"), ("기술", "XLK", "섹터"), ("반도체", "SMH", "섹터"),
    ("바이오", "XBI", "섹터"), ("경기소비재", "XLY", "섹터"), ("필수소비재", "XLP", "섹터"), ("소재", "XLB", "섹터"),
    ("금융", "XLF", "섹터"), ("커뮤니케이션", "XLC", "섹터"), ("소매", "XRT", "섹터"), ("운송", "IYT", "섹터"),
    ("산업재", "XLI", "섹터"), ("유틸리티", "XLU", "섹터"), ("리츠", "XLRE", "섹터"), ("주택건설", "ITB", "섹터"),
    ("지역은행", "KRE", "섹터"), ("사이버보안", "BUG", "섹터"), ("우라늄", "URA", "섹터"), ("클린에너지", "ICLN", "섹터"),
    ("방산", "ITA", "섹터"), ("중국 인터넷", "KWEB", "해외"), ("일본", "EWJ", "해외"), ("한국", "EWY", "해외"),
    ("신흥국", "EEM", "해외"), ("유럽", "VGK", "해외"), ("인도", "INDA", "해외"), ("대만", "EWT", "해외"),
]
MACRO = [  # (이름, 심볼, 설명)
    ("WTI 원유", "CL=F", "달러/배럴"), ("브렌트유", "BZ=F", "달러/배럴"), ("천연가스", "NG=F", "달러/MMBtu"), ("금", "GC=F", "달러/온스"),
    ("은", "SI=F", "달러/온스"), ("구리", "HG=F", "달러/파운드"), ("알루미늄", "ALI=F", "달러/톤"), ("옥수수", "ZC=F", "센트/부셸"),
    ("달러 인덱스", "DX-Y.NYB", "DXY"), ("원/달러", "KRW=X", "원"), ("엔/달러", "JPY=X", "엔"), ("유로/달러", "EURUSD=X", "달러"),
    ("위안/달러", "CNY=X", "위안"), ("미 10년물 금리", "^TNX", "%"), ("미 2년물 금리", "2YY=F", "%"), ("미 30년물 금리", "^TYX", "%"),
    ("장기국채 ETF", "TLT", "달러"), ("하이일드", "HYG", "달러"), ("투자등급 회사채", "LQD", "달러"), ("VIX", "^VIX", "pt"),
    ("비트코인", "BTC-USD", "달러"), ("이더리움", "ETH-USD", "달러"), ("상품지수", "DBC", "달러"), ("신흥국 채권", "EMB", "달러"),
]

def us_universe():
    import pandas as pd
    univ = {}
    for u, src in [("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", "S&P500"),
                   ("https://en.wikipedia.org/wiki/List_of_S%26P_400_companies", "S&P400"),
                   ("https://en.wikipedia.org/wiki/List_of_S%26P_600_companies", "S&P600")]:
        try:
            rq = requests.get(u, headers=H, timeout=30)
            tables = pd.read_html(io.StringIO(rq.text))
            df = [t for t in tables if "Symbol" in t.columns][0]
            for _, row in df.iterrows():
                tk = str(row["Symbol"]).strip().replace(".", "-")
                if not tk or tk == "nan": continue
                univ[tk] = {"name": str(row.get("Security", "")), "sector": str(row.get("GICS Sector", "")), "industry": str(row.get("GICS Sub-Industry", "")), "mkt": src}
        except Exception as e:
            print(f"  ⚠ {src} 목록 실패: {e}")
    return univ

def yf_download(tickers, period="2y"):
    import yfinance as yf
    out = {}
    for i in range(0, len(tickers), 150):
        chunk = tickers[i:i + 150]
        for a in range(2):
            try:
                df = yf.download(chunk, period=period, interval="1d", group_by="ticker", threads=True, progress=False, auto_adjust=False)
                break
            except Exception as e:
                print(f"  ⚠ yf chunk {i} 재시도: {e}"); time.sleep(5); df = None
        if df is None or df.empty: continue
        for tk in chunk:
            try:
                sub = df[tk] if len(chunk) > 1 else df
                sub = sub.dropna(subset=["Close"])
                if sub.empty: continue
                d = [x.strftime("%Y-%m-%d") for x in sub.index]
                out[tk] = (d, sub["Open"].tolist(), sub["High"].tolist(), sub["Low"].tolist(), sub["Close"].tolist(), sub["Volume"].fillna(0).tolist())
            except Exception: pass
    # 누락 종목 재시도(야후 간헐적 빈 응답 보정) — 작은 청크로 2회
    missing = [t for t in tickers if t not in out]
    for attempt in range(2):
        if len(missing) < 5: break
        print(f"  ↻ 누락 {len(missing)}개 재시도 {attempt+1}"); time.sleep(8)
        for i in range(0, len(missing), 40):
            chunk = missing[i:i + 40]
            try:
                df = yf.download(chunk, period=period, interval="1d", group_by="ticker", threads=True, progress=False, auto_adjust=False)
            except Exception: continue
            if df is None or df.empty: continue
            for tk in chunk:
                try:
                    sub = df[tk] if len(chunk) > 1 else df
                    sub = sub.dropna(subset=["Close"])
                    if sub.empty: continue
                    d = [x.strftime("%Y-%m-%d") for x in sub.index]
                    out[tk] = (d, sub["Open"].tolist(), sub["High"].tolist(), sub["Low"].tolist(), sub["Close"].tolist(), sub["Volume"].fillna(0).tolist())
                except Exception: pass
        missing = [t for t in tickers if t not in out]
    return out

def build_us():
    print("US universe …"); univ = us_universe(); print(f"  {len(univ)} stocks")
    syms = sorted(set(list(univ.keys()) + [t for _, t, _ in US_ETFS] + [s for _, s, _ in MACRO]))
    print(f"  download {len(syms)} …"); series = yf_download(syms); print(f"  got {len(series)}")
    # 시총: 스캔 히트 종목만 info 조회(느림) 대신 S&P 편입 자체로 규모 담보 — mcap 은 비움
    spy = series.get("SPY")
    cards = []
    for nm, tk, grp in US_ETFS:
        if tk not in series: continue
        d, o, h, l, c, v = series[tk]
        bench = None
        if spy and grp != "지수":
            m = dict(zip(spy[0], spy[4])); bench = [m.get(x) for x in d]
            if any(x is None for x in bench): bench = None
        card = group_card(nm, f"{tk} · {grp}", d, c, bench, note=tk)
        if card: card["grp"] = grp; cards.append(card)
    idx_cards = [c for c in cards if c["grp"] == "지수"]
    rest = sorted([c for c in cards if c["grp"] != "지수"], key=lambda x: -(x["rel_3m"] if x["rel_3m"] is not None else -999))
    for c in cards: c["_date"] = spy[0][-1] if spy else TODAY
    mark_flips(os.path.join(DATA, "groups_us.json"), idx_cards + rest)
    for c in cards: c.pop("_date", None)
    json.dump({"date": spy[0][-1] if spy else TODAY, "generated_at": datetime.datetime.now(KST).isoformat(timespec="seconds"), "bench": "SPY", "cards": idx_cards + rest},
              open(os.path.join(DATA, "groups_us.json"), "w"), ensure_ascii=False)
    mcards = []
    for nm, sym, unit in MACRO:
        if sym not in series: continue
        d, o, h, l, c, v = series[sym]
        card = group_card(nm, f"{sym} · {unit}", d, c, None, note=sym)
        if card: mcards.append(card)
    for c in mcards: c["_date"] = spy[0][-1] if spy else TODAY
    mark_flips(os.path.join(DATA, "groups_macro.json"), mcards)
    for c in mcards: c.pop("_date", None)
    json.dump({"date": spy[0][-1] if spy else TODAY, "generated_at": datetime.datetime.now(KST).isoformat(timespec="seconds"), "cards": mcards},
              open(os.path.join(DATA, "groups_macro.json"), "w"), ensure_ascii=False)
    print(f"  groups_us {len(cards)} · macro {len(mcards)}")
    scan_univ = {tk: u for tk, u in univ.items() if tk in series and series[tk][4][-1] * series[tk][5][-1] >= 5e6}  # 거래대금 $5M+
    hits, rs = scan_market(scan_univ, series, (spy[0], spy[4]), "US")
    # 히트 종목 보강: 실적일·뉴스 (yfinance, 히트만)
    try:
        import yfinance as yf
        for hdl in hits[:60]:
            try:
                t = yf.Ticker(hdl["tk"])
                cal = t.calendar or {}
                ed = cal.get("Earnings Date")
                if ed:
                    ed = ed[0] if isinstance(ed, (list, tuple)) else ed
                    hdl["earnings"] = str(ed)[:10]
                news = t.news or []
                if news:
                    n0 = news[0].get("content", news[0]); hdl["news"] = {"title": n0.get("title"), "url": (n0.get("canonicalUrl") or {}).get("url") or n0.get("link"), "date": str(n0.get("pubDate") or n0.get("providerPublishTime"))[:10]}
                fi = t.fast_info
                mc = fi.get("marketCap") if hasattr(fi, "get") else None
                if mc: hdl["mcap"] = round(mc / 1e6)
            except Exception: pass
    except Exception as e:
        print("  ⚠ 보강 실패:", e)
    out = {"date": spy[0][-1] if spy else TODAY, "generated_at": datetime.datetime.now(KST).isoformat(timespec="seconds"), "market": "US",
           "universe": len(scan_univ), "bench": "SPY", "hits": hits}
    json.dump(out, open(os.path.join(DATA, "scan_us.json"), "w"), ensure_ascii=False)
    json.dump(out, open(os.path.join(DATA, "scan", f"{out['date']}_us.json"), "w"), ensure_ascii=False)
    print(f"  scan_us: universe {len(scan_univ)} → hits {len(hits)}")
    return {"us_date": out["date"], "us_universe": len(scan_univ), "us_hits": len(hits)}

def mark_flips(path, cards):
    """이전 산출물과 비교해 일·주·월 뱃지가 바뀐 카드에 *_flip 표시 (같은 날짜 재실행이면 이전의 flip 유지)"""
    try: prev = json.load(open(path))
    except Exception: return cards
    pm = {c["name"]: c for c in prev.get("cards", [])}
    same_day = prev.get("date") == (cards and cards[0].get("_date"))
    for c in cards:
        p = pm.get(c["name"])
        for k in ("d", "w", "m"):
            if same_day: c[k + "_flip"] = bool(p and p.get(k + "_flip"))
            else: c[k + "_flip"] = bool(p and p.get(k + "_above") is not None and p.get(k + "_above") != c.get(k + "_above"))
    return cards

def prune_history(days=90):
    cut = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    sd = os.path.join(DATA, "scan")
    for f in os.listdir(sd):
        if f[:10] < cut: os.remove(os.path.join(sd, f))
    files = sorted(os.listdir(sd))
    json.dump({"files": files}, open(os.path.join(sd, "index.json"), "w"))

if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "kr"
    meta_p = os.path.join(DATA, "meta.json")
    meta = json.load(open(meta_p)) if os.path.exists(meta_p) else {}
    meta.update(build_kr() if which == "kr" else build_us())
    meta["updated_at"] = datetime.datetime.now(KST).isoformat(timespec="seconds")
    json.dump(meta, open(meta_p, "w"), ensure_ascii=False, indent=1)
    prune_history()
    print("done", meta)
