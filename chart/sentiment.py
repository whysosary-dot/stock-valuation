#!/usr/bin/env python3
"""투자 심리 일별 지표 → chart/data/sentiment.json  (Chart 탭 인덱스 맨 앞 '😨 투자 심리' 카드 2개)

1) VIX : CBOE 변동성지수 종가 (yfinance ^VIX) — 공식 지수 그대로
2) 공포·탐욕 지수(자체 계산) : CNN Fear & Greed 의 공개된 방식을 yfinance 로 구할 수 있는 4개 구성요소로 근사한 0~100 점수.
   CNN 의 7개 요소 중 ① 시장 모멘텀(S&P500 vs 125일선) ② 변동성(VIX vs 50일선, 역방향) ③ 안전자산 선호(주식 20일 수익 − 국채 20일 수익)
   ④ 정크본드 수요(HYG 20일 수익 − LQD 20일 수익) 를 쓰고, 52주 신고가·신저가 폭·거래량 breadth·풋콜비율 3개는 데이터가 없어 제외한다.
   각 요소는 직전 1년(252거래일) 안에서의 백분위(0~100)로 바꾼 뒤 단순 평균한다 → CNN 공식 수치와 다르며, 추세·국면 비교용이다.
3) VKOSPI : 코스피200 변동성지수 일별 종가 — 공공데이터포털 「금융위원회_지수시세정보」(GetMarketIndexInfoService) 공식 시세.
   환경변수 DATAGOKR_KEY 필요. 1영업일 지연. 실패하면 기존 파일의 vkospi 시계열을 그대로 유지한다.
   구간 라벨: <25 극단적 공포 · <45 공포 · <55 중립 · <75 탐욕 · ≥75 극단적 탐욕 (CNN 과 같은 경계)
저장: 최근 3년. 값은 소수 1자리.
"""
import json, os, sys, datetime as dt, urllib.request, urllib.parse
import yfinance as yf
import pandas as pd

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "sentiment.json")
SYMS = ["^VIX", "^GSPC", "IEF", "HYG", "LQD"]

def closes():
    df = yf.download(SYMS, period="5y", interval="1d", group_by="ticker", threads=True, progress=False, auto_adjust=False)
    out = {}
    for s in SYMS:
        try:
            c = df[s]["Close"] if isinstance(df.columns, pd.MultiIndex) else df["Close"]
        except KeyError:
            raise SystemExit(f"{s} 다운로드 실패")
        out[s] = c.dropna()
    px = pd.concat(out, axis=1).sort_index()
    px.index = pd.to_datetime(px.index).tz_localize(None)
    # VIX 는 미국 휴장일 제외 — 다른 종목과 교집합 날짜만 사용, 결측은 직전값
    px = px.dropna(subset=["^VIX", "^GSPC"]).ffill()
    if len(px) < 400: raise SystemExit(f"데이터 부족 {len(px)}일")
    return px

def pct_rank_1y(s, higher_is_greed=True, win=252):
    """각 시점 값이 직전 win 일(자기 포함) 안에서 차지하는 백분위 0~100"""
    r = s.rolling(win, min_periods=120).apply(lambda w: (w[:-1] < w[-1]).mean() * 100 if len(w) > 1 else float("nan"), raw=True)
    return r if higher_is_greed else 100 - r

DGK = "https://apis.data.go.kr/1160100/service/GetMarketIndexInfoService/"
def vkospi(start):
    """{YYYY-MM-DD: 종가}  — 파생상품지수 → 주가지수 순으로 '변동성' 지수명을 찾는다"""
    key = os.environ.get("DATAGOKR_KEY", "").strip()
    if not key: print("  VKOSPI: DATAGOKR_KEY 없음 → 건너뜀"); return {}
    for op in ("getDerivationProductMarketIndex", "getStockMarketIndex"):
        out = {}
        for page in range(1, 30):
            q = urllib.parse.urlencode({"serviceKey": key, "resultType": "json", "numOfRows": "1000", "pageNo": str(page),
                                        "idxNm": "코스피 200 변동성지수", "beginBasDt": start})
            try:
                j = json.loads(urllib.request.urlopen(DGK + op + "?" + q, timeout=40).read().decode("utf-8"))
            except Exception as e:
                print(f"  VKOSPI {op}: {type(e).__name__} {str(e)[:80]}"); out = None; break
            body = (j.get("response") or {}).get("body") or {}
            items = ((body.get("items") or {}).get("item")) or []
            if isinstance(items, dict): items = [items]
            for it in items:
                if "변동성" not in str(it.get("idxNm", "")): continue
                d = str(it.get("basDt", "")); v = it.get("clpr")
                try: out[f"{d[:4]}-{d[4:6]}-{d[6:8]}"] = round(float(v), 2)
                except (TypeError, ValueError): pass
            if page * 1000 >= int(body.get("totalCount") or 0): break
        if out: print(f"  VKOSPI {op}: {len(out)}일"); return out
    return {}

def main():
    px = closes()
    spx, vix, ief, hyg, lqd = px["^GSPC"], px["^VIX"], px["IEF"], px["HYG"], px["LQD"]
    mom  = spx / spx.rolling(125).mean() - 1                 # ① 시장 모멘텀
    vixr = vix / vix.rolling(50).mean() - 1                  # ② 변동성 (높을수록 공포)
    safe = spx.pct_change(20) - ief.pct_change(20)           # ③ 안전자산 선호 (주식−국채 20일)
    junk = hyg.pct_change(20) - lqd.pct_change(20)           # ④ 정크본드 수요 (HYG−LQD 20일)
    parts = {
        "momentum":   pct_rank_1y(mom, True),
        "volatility": pct_rank_1y(vixr, False),
        "safe_haven": pct_rank_1y(safe, True),
        "junk_bond":  pct_rank_1y(junk, True),
    }
    P = pd.concat(parts, axis=1)
    fg = P.mean(axis=1, skipna=False)
    cut = px.index[-1] - pd.DateOffset(years=3)
    idx = px.index[(px.index >= cut) & fg.notna()]
    r1 = lambda v: None if pd.isna(v) else round(float(v), 1)
    last = idx[-1]
    def label(v):
        return "극단적 공포" if v < 25 else "공포" if v < 45 else "중립" if v < 55 else "탐욕" if v < 75 else "극단적 탐욕"
    out = {
        "updated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "VIX: CBOE 종가(yfinance ^VIX) · 공포·탐욕: 자체 계산 — CNN 방식 7요소 중 4요소(모멘텀·변동성·안전자산·정크본드) 1년 백분위 평균, 공식 수치 아님",
        "date": [d.strftime("%Y-%m-%d") for d in idx],
        "vix": [r1(vix[d]) for d in idx],
        "fg": [r1(fg[d]) for d in idx],
        "fg_label": label(float(fg[last])),
        "fg_parts": {k: r1(P[k][last]) for k in parts},
        "fg_parts_label": {"momentum": "시장 모멘텀 (S&P500 vs 125일선)", "volatility": "변동성 (VIX vs 50일선, 역방향)",
                           "safe_haven": "안전자산 선호 (주식−국채 20일)", "junk_bond": "정크본드 수요 (HYG−LQD 20일)"},
        "fg_bands": {"extreme_fear": 25, "fear": 45, "neutral": 55, "greed": 75},
    }
    # VKOSPI (한국 거래일 기준, 별도 날짜축)
    vk = vkospi((px.index[-1] - pd.DateOffset(years=3)).strftime("%Y%m%d"))
    if vk:
        ks = sorted(vk); out["vk_date"] = ks; out["vkospi"] = [vk[k] for k in ks]
        out["source"] += " · VKOSPI: 금융위원회_지수시세정보(공공데이터포털)"
    else:
        try:
            old = json.load(open(OUT, encoding="utf-8"))
            if old.get("vkospi"): out["vk_date"], out["vkospi"] = old["vk_date"], old["vkospi"]; out["source"] += " · VKOSPI: 직전 수집분 유지"; print("  VKOSPI 이전 값 유지")
        except Exception: pass
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print(f"✓ sentiment.json {out['date'][0]}~{out['date'][-1]} ({len(idx)}일) VIX {out['vix'][-1]} · 공포탐욕 {out['fg'][-1]} ({out['fg_label']}) · VKOSPI {(out.get('vkospi') or ['없음'])[-1]}")

if __name__ == "__main__":
    sys.exit(main())
