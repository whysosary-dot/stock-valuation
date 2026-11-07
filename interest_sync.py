#!/usr/bin/env python3
"""
interest_sync.py — Diary 탭 「종목 시나리오」(보관 제외, 현금 제외) 종목을 Interest 탭 설정(invest-private interest/watchlist.json)에
자동으로 넣어 관심종목 브리핑에 포함되게 한다 (2026-11-07, 사용자 지정).

  python3 interest_sync.py            # 동기화 후 변경이 있으면 invest-private 에 커밋
  python3 interest_sync.py --dry-run  # 변경 내용만 출력

규칙
- 시나리오에 있는데 watchlist 어느 섹터에도 없는 종목 → 추가. 표기는 {"name","aliases","dart","auto":"diary"}.
  섹터는 Stock Valuation(sv/stocks.json)의 sector 를 SECTOR_MAP 으로 대응, 없으면 'scenario'(📝 시나리오 종목) 섹터에.
  aliases = 이름 + 하이픈·공백 제거형 + 영문 티커(해외). dart = 한국 상장(티커 .KS/.KQ)일 때만 true.
- 이전에 이 스크립트가 넣은 종목(auto:"diary")인데 시나리오에서 빠졌거나 보관됐으면 → 제거. 손으로 넣은 종목은 절대 건드리지 않는다.
- 이름 매칭은 공백·하이픈·대소문자 무시 ('와이지-원' = '와이지원'). alias 에 걸려도 '있음'으로 본다.
토큰은 옆의 .github_token (출력 금지). 실행: 관심종목 브리핑 예약 작업 Step 1 에서 collect 직전에 호출.
"""
import base64, json, os, re, sys, time, urllib.request, urllib.error
from pathlib import Path

DRY = '--dry-run' in sys.argv
TOK = (os.environ.get('GH_TOKEN') or (Path(__file__).resolve().parent / '.github_token').read_text()).strip()
REPO = 'whysosary-dot/invest-private'
WL_PATH, DIARY_PATH, SV_PATH = 'interest/watchlist.json', 'sv/diary.json', 'sv/stocks.json'
SECTOR_MAP = [  # SV sector 부분일치 → watchlist 섹터 id (위에서부터 먼저 맞는 것)
    ('조선', 'ship'), ('반도체', 'semi'), ('빅테크', 'dc'), ('데이터센터', 'dc'), ('게임', 'game'),
    ('미용', 'beauty'), ('화장', 'beauty'), ('공작기계', 'mtool'), ('원자재', 'commodity'),
]
SCEN_SECTOR = {'id': 'scenario', 'name': '시나리오 종목', 'icon': '📝', 'color': '#6b5b95', 'hdr_color': '#4a3f73', 'companies': []}

def api(method, path, body=None):
    url = f'https://api.github.com/repos/{REPO}/{path}'
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={'Authorization': 'token ' + TOK, 'Accept': 'application/vnd.github+json', 'Content-Type': 'application/json'})
    for a in range(4):
        try:
            with urllib.request.urlopen(req, timeout=60) as r: return json.loads(r.read() or b'{}')
        except urllib.error.HTTPError: raise
        except urllib.error.URLError:
            if a == 3: raise
            time.sleep(5)

def get_json(path):
    j = api('GET', f'contents/{path}?ref=main')
    return json.loads(base64.b64decode(j['content']).decode('utf-8')), j['sha']

norm = lambda s: re.sub(r'[\s\-–_·.()]+', '', str(s or '')).lower()

def main():
    wl, sha = get_json(WL_PATH)
    diary, _ = get_json(DIARY_PATH)
    sv = {norm(x['name']): x for x in get_json(SV_PATH)[0].get('stocks', [])}
    active = {}
    for x in diary.get('scenario', []):
        n = (x.get('name') or '').strip()
        if not n or x.get('archived') or norm(n).startswith('현금'): continue
        active[norm(n)] = n
    # 현재 watchlist 에 있는 이름·별칭
    present = {}
    for sec in wl['sectors']:
        for c in sec['companies']:
            for a in [c['name']] + list(c.get('aliases') or []): present[norm(a)] = (sec['id'], c)
    added, removed = [], []
    # 1) 추가
    for key, name in active.items():
        if key in present: continue
        s = sv.get(key) or next((v for k, v in sv.items() if k == key.replace('-', '')), None)
        ticker = (s or {}).get('ticker') or ''
        kr = ticker.endswith(('.KS', '.KQ')) or bool(re.fullmatch(r'\d{6}', ticker))
        sec_id = 'scenario'
        for kw, sid in SECTOR_MAP:
            if kw in ((s or {}).get('sector') or ''): sec_id = sid; break
        aliases = [name]
        base = re.sub(r'\s+[A-C]$', '', name)            # '알파벳 A' → '알파벳'
        if base != name: aliases.append(base)
        if re.search(r'[\-–]', name): aliases.append(re.sub(r'[\-–]', '', name))
        if ticker and not kr: aliases.append(ticker.split('.')[0])
        aliases = list(dict.fromkeys(a for a in aliases if a))
        comp = {'name': name, 'aliases': aliases, 'dart': kr, 'auto': 'diary'}
        sec = next((x for x in wl['sectors'] if x['id'] == sec_id), None)
        if sec is None:
            sec = dict(SCEN_SECTOR); wl['sectors'].append(sec)
        sec['companies'].append(comp); added.append(f"{name}→{sec['name']}")
    # 2) 자동 추가분 중 시나리오에서 빠진 것 제거
    for sec in wl['sectors']:
        keep = []
        for c in sec['companies']:
            if c.get('auto') == 'diary' and not any(norm(a) in active for a in [c['name']] + list(c.get('aliases') or [])):
                removed.append(c['name']); continue
            keep.append(c)
        sec['companies'] = keep
    print(f"시나리오 {len(active)}종목 · 추가 {len(added)} {added} · 제거 {len(removed)} {removed}")
    if not (added or removed): print('변경 없음'); return
    if DRY: return
    content = json.dumps(wl, ensure_ascii=False, indent=2) + '\n'
    r = api('PUT', f'contents/{WL_PATH}', {'message': f'🔗 시나리오 종목 동기화: +{len(added)} -{len(removed)}',
                                           'content': base64.b64encode(content.encode()).decode(), 'sha': sha, 'branch': 'main'})
    print('✓ interest/watchlist.json', r['commit']['sha'][:7])

if __name__ == '__main__':
    main()
