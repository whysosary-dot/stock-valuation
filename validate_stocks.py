#!/usr/bin/env python3
"""stocks.json 무결성 검증 — 이름↔티커 불일치·중복 감지 (인바디/우주일렉트로 사고 재발 방지)

검사 항목:
  1. ticker 중복
  2. 국내(KRW) 종목: 네이버 API stockName 과 name 대조 (정규화 + ALIAS 허용)
사용: python3 validate_stocks.py   → 문제 있으면 ⚠️ 출력 + exit 1
새 종목 추가 시에는 _add_stock.py 가 같은 검증을 추가 시점에 수행한다.
"""
import json, re, sys, time, urllib.request
from pathlib import Path

BASE = Path(__file__).parent.resolve()
UA = {'User-Agent': 'Mozilla/5.0'}
# 공식명과 표기가 다른 정상 케이스는 여기 등록
ALIAS = {
    'YG엔터테인먼트': ['와이지엔터테인먼트'],
}

def norm(s):
    return re.sub(r'[\s()·,.&\-]+', '', str(s or '')).lower()

def naver_name(code):
    try:
        j = json.load(urllib.request.urlopen(urllib.request.Request(
            f'https://m.stock.naver.com/api/stock/{code}/basic', headers=UA), timeout=8))
        return j.get('stockName')
    except Exception:
        return None

def match(name, official):
    a, b = norm(name), norm(official)
    if not b:
        return True  # 조회 실패는 판단 보류 (오탐 방지)
    if a in b or b in a:
        return True
    return any(norm(al) in b or b in norm(al) for al in ALIAS.get(name, []))

def main():
    d = json.load(open(BASE / 'stocks.json', encoding='utf-8'))
    stocks = d.get('stocks', [])
    errs = []
    seen = {}
    for s in stocks:
        t = s.get('ticker')
        if t in seen:
            errs.append(f"중복 ticker {t}: '{seen[t]}' / '{s['name']}'")
        seen[t] = s['name']
    for s in stocks:
        if s.get('currency') != 'KRW':
            continue
        code = (s.get('ticker') or '').split('.')[0]
        off = naver_name(code)
        if off and not match(s['name'], off):
            errs.append(f"이름-코드 불일치: '{s['name']}' ({s['ticker']}) → 네이버 공식명 '{off}'")
        time.sleep(0.12)
    if errs:
        print(f'⚠️  stocks.json 무결성 오류 {len(errs)}건 — 수동 확인 필요!')
        for e in errs:
            print('  ⚠️', e)
        return 1
    print(f'✅ 무결성 검증 통과 — {len(stocks)}종목 (국내 이름대조 + ticker 중복)')
    return 0

if __name__ == '__main__':
    sys.exit(main())
