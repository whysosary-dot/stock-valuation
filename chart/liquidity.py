#!/usr/bin/env python3
"""증시 유동성 일별 지표 → chart/data/liquidity.json  (Chart 탭 인덱스 맨 앞 카드 4개)

출처: 금융투자협회 종합통계 (freesis.kofia.or.kr) — 공식 통계
  STATSCU0100000060BO 증시자금추이   : TMPV1 일자, TMPV2 투자자예탁금(백만원)
  STATSCU0100000070BO 신용공여잔고추이: TMPV1 일자, TMPV2 신용융자 합계, TMPV3 유가증권(코스피), TMPV4 코스닥 (백만원)
파생: 신용비율(%) = 신용융자 합계 ÷ 투자자예탁금 × 100  (Claude 계산, 공시 아님)
저장 단위: 조원(소수 2자리), %(소수 2자리). 최근 3년.
"""
import json, os, sys, time, urllib.request, datetime as dt

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "liquidity.json")
URL = "https://freesis.kofia.or.kr/meta/getMetaDataList.do"

def fetch(obj, s, e):
    body = {"dmSearch": {"tmpV40": "1000000", "tmpV41": "1", "tmpV1": "D", "tmpV45": s, "tmpV46": e, "OBJ_NM": obj}}
    req = urllib.request.Request(URL, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", "Accept": "application/json", "User-Agent": "Mozilla/5.0"})
    last = None
    for i in range(4):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read()
            txt = raw.decode("utf-8", "replace").strip()
            try:
                return json.loads(txt).get("ds1", [])
            except Exception as ex:
                # 응답이 잘렸거나(네트워크) HTML 오류 페이지인 경우 — 진단용으로 앞뒤를 남기고 재시도
                print(f"  ! {obj} JSON 파싱 실패 (len={len(raw)}): {txt[:100]!r} … {txt[-80:]!r}")
                last = ex
        except Exception as ex:
            last = ex
            print(f"  ! {obj} 요청 실패: {ex}")
        time.sleep(5 * (i + 1))
    raise last

def main():
    today = dt.date.today()
    s = (today - dt.timedelta(days=365 * 3 + 10)).strftime("%Y%m%d"); e = today.strftime("%Y%m%d")
    dep = {r["TMPV1"]: r for r in fetch("STATSCU0100000060BO", s, e) if r.get("TMPV2") not in (None, "", 0)}
    cr  = {r["TMPV1"]: r for r in fetch("STATSCU0100000070BO", s, e) if r.get("TMPV2") not in (None, "", 0)}
    days = sorted(set(dep) & set(cr))
    if len(days) < 30:
        raise SystemExit(f"KOFIA 데이터 부족: 예탁금 {len(dep)}일 · 신용 {len(cr)}일 · 교집합 {len(days)}일")
    jo = lambda v: round(float(v) / 1e6, 3)   # 백만원 → 조원
    out = {
        "updated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "금융투자협회 종합통계(증시자금추이·신용공여잔고추이) · 단위 조원 · 신용비율 = 신용융자 합계 ÷ 투자자예탁금 (계산)",
        "date": [f"{d[:4]}-{d[4:6]}-{d[6:]}" for d in days],
        "deposit": [jo(dep[d]["TMPV2"]) for d in days],
        "credit_total": [jo(cr[d]["TMPV2"]) for d in days],
        "kospi_credit": [jo(cr[d]["TMPV3"]) for d in days],
        "kosdaq_credit": [jo(cr[d]["TMPV4"]) for d in days],
    }
    out["credit_ratio"] = [round(c / p * 100, 2) if p else None for c, p in zip(out["credit_total"], out["deposit"])]
    # 이전 파일과 마지막 날짜가 같고 값도 같으면 변경 없음 (data 브랜치 커밋은 어차피 통째라 무해)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print(f"✓ liquidity.json {out['date'][0]}~{out['date'][-1]} ({len(days)}일) 예탁금 {out['deposit'][-1]}조 · 신용 {out['credit_total'][-1]}조 · 비율 {out['credit_ratio'][-1]}%")

if __name__ == "__main__":
    sys.exit(main())
