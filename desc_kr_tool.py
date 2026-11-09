#!/usr/bin/env python3
"""Chart 탭 KR 스캔 카드 기업 한 줄 설명(chart/desc_kr.json) 관리.

  python3 -I desc_kr_tool.py missing          # 오늘 scan_kr 히트 중 설명 없는 종목 (code  name  industry)
  python3 -I desc_kr_tool.py add FILE.json    # {"005930": "설명", ...} 을 desc_kr.json 에 병합 → gh_push.py 로 푸시

설명 규칙: 90자 이내 한국어 한 문장, "무엇을 만들어/하여 돈을 버는지"만. 전망·추천·주가 언급 금지.
"""
import json, os, re, subprocess, sys, urllib.request
HERE = os.path.dirname(os.path.abspath(__file__))
RAW = "https://raw.githubusercontent.com/whysosary-dot/stock-valuation/"
def _get(url):
    req = urllib.request.Request(url + ("&" if "?" in url else "?") + "t=%d" % int(__import__("time").time()),
                                 headers={"User-Agent": "desc_kr_tool"})
    return json.load(urllib.request.urlopen(req, timeout=30))
def load_desc():
    try: return _get(RAW + "main/chart/desc_kr.json")
    except Exception: return {}
def cmd_missing():
    desc = load_desc()
    hits = _get(RAW + "data/scan_kr.json").get("hits", [])
    miss = [h for h in hits if not desc.get(str(h["tk"]))]
    for h in miss: print(h["tk"], h.get("name"), h.get("industry") or "", h.get("mkt_sub") or "", sep="\t")
    print(f"# 히트 {len(hits)} · 설명 없음 {len(miss)}", file=sys.stderr)
def cmd_add(path):
    new = json.load(open(path, encoding="utf-8"))
    desc = load_desc()
    n = 0
    for k, v in new.items():
        k = str(k).strip(); v = re.sub(r"\s+", " ", str(v)).strip()
        if not re.fullmatch(r"\d{6}", k): print("skip(코드 아님):", k); continue
        if not v or len(v) > 90: print("skip(길이 0 또는 90자 초과):", k, len(v)); continue
        if desc.get(k) == v: continue
        desc[k] = v; n += 1
    if not n: print("변경 없음"); return
    out = os.path.join(HERE, "_dpatch_desc_kr.json")
    meta = {k: desc[k] for k in desc if k.startswith("_")}
    body = {k: desc[k] for k in sorted(k for k in desc if not k.startswith("_"))}
    json.dump({**meta, **body}, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    r = subprocess.run([sys.executable, "-I", os.path.join(HERE, "gh_push.py"), "--repo", "whysosary-dot/stock-valuation",
                        "-m", f"desc_kr: {n}개 추가", "--file", f"{out}=chart/desc_kr.json"], capture_output=True, text=True)
    txt = re.sub(r"(gh[pousr]_|github_pat_)[A-Za-z0-9_]+", "[R]", r.stdout + r.stderr)
    print(f"{n}개 추가 ·", txt.strip().splitlines()[-2] if "PUSHED" in txt else txt.strip()[-300:])
if __name__ == "__main__":
    a = sys.argv[1:]
    if a and a[0] == "missing": cmd_missing()
    elif len(a) == 2 and a[0] == "add": cmd_add(a[1])
    else: print(__doc__)
