#!/usr/bin/env python3
"""
retention.py — 날짜별로 쌓이는 파일을 1년(365일)만 보관하고 지난 것은 삭제한다 (2026-11-03, 사용자 지정).
GitHub Actions retention.yml 이 매주 실행 (토큰: 환경변수 GH_TOKEN = secrets.SV_PAT). Mac 에서는 옆의 .github_token.

  python3 retention.py            # 실제 삭제 (저장소마다 커밋 1개)
  python3 retention.py --dry-run  # 지울 목록만 출력

대상 (파일명 안의 YYYY-MM-DD 가 기준일보다 오래되면 삭제, 목록 JSON 이 있으면 그 항목도 같이 뺌)
  Market      us-market-briefing/briefings/us-market-briefing-DATE.html
              kr-market-briefing/briefings/kr-market-closing-DATE.html   (+ briefings/manifest.json)
  Awake       awake-disclosure/reports/AWAKE_DATE.html, AWAKE_DATE.html, daily_analyses_DATE.json
  Interest    watchlist-briefing/briefings/watchlist-brief-DATE.html     (+ briefings/manifest.json)
  Report      stock-valuation/report/data/DATE.json                      (+ report/data/index.json)
  Telegram    stock-valuation/telegram/data/DATE.json                    (+ telegram/data/index.json)
  (KR Screener daily/·swing/ 는 daily_screen.py 가, Chart 스캔은 data 브랜치에서 90일로 이미 관리)
주의: main 에서 지워도 git 이력에는 남는다 → 사이트·작업 트리 크기는 줄지만 저장소 이력은 계속 늘어난다.
"""
import base64, datetime, json, os, re, sys, time, urllib.error, urllib.request
from pathlib import Path

KEEP_DAYS = 365
DRY = "--dry-run" in sys.argv
OWNER = "whysosary-dot"
DATE = r"(\d{4}-\d{2}-\d{2})"
TARGETS = [
    ("us-market-briefing", [rf"^briefings/us-market-briefing-{DATE}\.html$"], []),
    ("kr-market-briefing", [rf"^briefings/kr-market-closing-{DATE}\.html$"], [("briefings/manifest.json", "briefings")]),
    ("awake-disclosure",   [rf"^reports/AWAKE_{DATE}\.html$", rf"^AWAKE_{DATE}\.html$", rf"^daily_analyses_{DATE}\.json$"], []),
    ("watchlist-briefing", [rf"^briefings/watchlist-brief-{DATE}\.html$"], [("briefings/manifest.json", "briefings")]),
    ("stock-valuation",    [rf"^report/data/{DATE}\.json$", rf"^telegram/data/{DATE}\.json$"], [("report/data/index.json", "dates"), ("telegram/data/index.json", "dates")]),
]

def token():
    t = os.environ.get("GH_TOKEN", "").strip()
    if t: return t
    f = Path(__file__).resolve().parent / ".github_token"
    return f.read_text().strip()
TOK = token()

def api(method, repo, path, body=None):
    url = f"https://api.github.com/repos/{OWNER}/{repo}/{path}"
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": "token " + TOK, "Accept": "application/vnd.github+json",
                                          "User-Agent": "retention", "Content-Type": "application/json"})
    for a in range(4):
        try:
            with urllib.request.urlopen(req, timeout=120) as r: return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError: raise
        except urllib.error.URLError:
            if a == 3: raise
            time.sleep(5 * (a + 1))

def run_repo(repo, pats, indexes, cutoff):
    for attempt in range(3):
        head = api("GET", repo, "git/ref/heads/main")["object"]["sha"]
        base = api("GET", repo, f"git/commits/{head}")["tree"]["sha"]
        tree = api("GET", repo, f"git/trees/{base}?recursive=1")["tree"]
        dels = []
        for t in tree:
            if t["type"] != "blob": continue
            for p in pats:
                m = re.match(p, t["path"])
                if m and m.group(1) < cutoff: dels.append(t["path"]); break
        changes = [{"path": p, "mode": "100644", "type": "blob", "sha": None} for p in dels]
        pruned = 0
        for ipath, key in indexes:
            blob = next((t for t in tree if t["path"] == ipath), None)
            if not blob: continue
            raw = base64.b64decode(api("GET", repo, f"git/blobs/{blob['sha']}")["content"]).decode("utf-8")
            j = json.loads(raw); arr = j.get(key) or []
            keep = [x for x in arr if not (isinstance(x, dict) and str(x.get("date", "9999")) < cutoff)]
            if len(keep) != len(arr):
                pruned += len(arr) - len(keep); j[key] = keep
                indent = 4 if '\n    "' in raw[:200] else (2 if "\n  " in raw[:200] else None)
                changes.append({"path": ipath, "mode": "100644", "type": "blob",
                                "content": json.dumps(j, ensure_ascii=False, indent=indent, separators=None if indent else (",", ":"))})
        print(f"[{repo}] 삭제 {len(dels)}개 · 목록 항목 {pruned}개 제거" + (f" (예: {dels[0]})" if dels else ""))
        if not changes or DRY: return
        nt = api("POST", repo, "git/trees", {"base_tree": base, "tree": changes})
        c = api("POST", repo, "git/commits", {"message": f"🧹 {KEEP_DAYS}일 지난 파일 {len(dels)}개 정리 (retention)", "tree": nt["sha"], "parents": [head]})
        try:
            api("PATCH", repo, "git/refs/heads/main", {"sha": c["sha"]}); print(f"  ✓ {c['sha'][:7]}"); return
        except urllib.error.HTTPError as e:
            print(f"  ref 갱신 충돌({e.code}) — 재시도 {attempt + 1}"); time.sleep(5)

def main():
    cutoff = (datetime.date.today() - datetime.timedelta(days=KEEP_DAYS)).isoformat()
    print(f"기준일 {cutoff} 이전 삭제{' (dry-run)' if DRY else ''}")
    for repo, pats, idx in TARGETS:
        try: run_repo(repo, pats, idx, cutoff)
        except Exception as e: print(f"[{repo}] 실패: {e}")

if __name__ == "__main__":
    main()
