#!/usr/bin/env python3
"""
투자 조언(코칭) 생성 보조 — Diary 탭 「🧭 조언」. 스케줄 태스크(Claude)가 쓰는 두 명령.

  python3 advice_gen.py data weekly  [YYYY-Www]   # 그 주(월~일) 일기·매매·포트 변화·시나리오 변경 + 규칙 점검 + 직전 조언 → Claude 가 읽고 조언을 쓴다
  python3 advice_gen.py data monthly [YYYY-MM]
  python3 advice_gen.py data yearly  [YYYY]
  python3 advice_gen.py write weekly|monthly|yearly /path/advice.json   # 검증 후 invest-private sv/advice.json 에 추가(같은 기간은 교체, 이력 유지)

기간 생략 시: 어제가 속한 주/월/년.

JSON 형식 (Claude 가 작성):
{
 "kind": "weekly" | "monthly" | "yearly",
 "period": "2026-W44" | "2026-10" | "2026",
 "range": "2026-10-27 ~ 2026-11-02",
 "headline": "한 줄 총평 (60자 이내)",
 "process": "이 기간 투자 과정 요약 — 무엇을 보고 무엇을 했나 (3~6문장)",
 "good": ["잘한 것 · 장점 (근거 날짜 포함)"],
 "mistakes": ["실수 (근거 날짜·인용 포함)"],
 "critique": "비판적 검토 — 본인이 쓴 논리의 약점, 자기모순, 회피한 질문 (3~6문장)",
 "improve": ["개선 사항 — 구체적·측정 가능하게"],
 "advice": "조언 (5~8문장, 직설적이되 존중)",
 "next": ["다음 기간 과제 2~4개"],
 "rules": {"kept": ["지킨 규칙"], "broken": ["어긴 규칙 (날짜)"]},
 "score": 7   # 10점 만점 자기 규칙 준수·과정 점수 (결과 아님)
}
규칙: 결과(수익률)가 아니라 과정을 평가. 매수·매도 권유 금지. 인용은 일기 원문 그대로 짧게. 날짜는 반드시.
"""
import sys, json, base64, urllib.request, datetime, re, html
from pathlib import Path
BASE = Path(__file__).resolve().parent
TOK = (BASE / ".github_token").read_text().strip()
PRIV = "whysosary-dot/invest-private"
KST = datetime.timezone(datetime.timedelta(hours=9))
H = {"Authorization": "token " + TOK, "Accept": "application/vnd.github+json", "User-Agent": "sv-advice", "Content-Type": "application/json"}

def gh_get(path):
    try:
        r = urllib.request.urlopen(urllib.request.Request(f"https://api.github.com/repos/{PRIV}/contents/{path}?ref=main&t={datetime.datetime.now().timestamp()}", headers=H), timeout=60)
        j = json.loads(r.read()); return json.loads(base64.b64decode(j["content"]).decode("utf-8")), j["sha"]
    except urllib.error.HTTPError as e:
        if e.code == 404: return None, None
        raise

def gh_put(path, obj, sha, msg):
    body = {"message": msg, "content": base64.b64encode((json.dumps(obj, ensure_ascii=False, indent=1) + "\n").encode()).decode(), "branch": "main"}
    if sha: body["sha"] = sha
    r = urllib.request.urlopen(urllib.request.Request(f"https://api.github.com/repos/{PRIV}/contents/{path}", method="PUT", data=json.dumps(body).encode(), headers=H), timeout=60)
    return json.loads(r.read())["commit"]["sha"][:7]

def strip(h):
    h = re.sub(r"<(b|strong)[^>]*>(.*?)</\1>", r"**\2**", str(h or ""), flags=re.S)
    h = re.sub(r"<br\s*/?>|</p>|</div>", "\n", h); h = re.sub(r"<[^>]+>", "", h)
    return re.sub(r"\n{3,}", "\n\n", html.unescape(h)).strip()

def period_range(kind, period):
    y = datetime.date.fromisoformat
    if kind == "weekly":
        yr, wk = period.split("-W"); mon = datetime.date.fromisocalendar(int(yr), int(wk), 1); return mon, mon + datetime.timedelta(days=6)
    if kind == "monthly":
        yr, mo = map(int, period.split("-")); a = datetime.date(yr, mo, 1); b = (datetime.date(yr + (mo == 12), (mo % 12) + 1, 1) - datetime.timedelta(days=1)); return a, b
    yr = int(period); return datetime.date(yr, 1, 1), datetime.date(yr, 12, 31)

def default_period(kind):
    yd = (datetime.datetime.now(KST) - datetime.timedelta(days=1)).date()
    if kind == "weekly": y, w, _ = yd.isocalendar(); return f"{y}-W{w:02d}"
    if kind == "monthly": return yd.strftime("%Y-%m")
    return str(yd.year)

def data(kind, period):
    d, _ = gh_get("sv/diary.json"); d = d or {}
    a, b = period_range(kind, period)
    inr = lambda s: a.isoformat() <= s <= b.isoformat()
    print(f"# 조언 자료 · {kind} {period} · {a} ~ {b} · 오늘 {datetime.datetime.now(KST).date()}")
    daily = {k: v for k, v in (d.get("daily") or {}).items() if inr(k)}
    print(f"\n## 일기 ({len(daily)}일) — 원문. '📊 매매일지' 줄은 그날 매매.")
    for k in sorted(daily): print(f"\n--- {k} ---\n{strip(daily[k])}")
    if kind != "weekly":
        # 월·연간은 기간 밖 직전 2주 일기도 맥락으로
        pre = {k: v for k, v in (d.get("daily") or {}).items() if (a - datetime.timedelta(days=14)).isoformat() <= k < a.isoformat()}
        if pre: print(f"\n## (맥락) 기간 직전 2주 일기 {len(pre)}일 — 요약용, 평가 대상 아님"); [print(f"--- {k} --- {strip(pre[k])[:300]}") for k in sorted(pre)]
    ph = [h for h in (d.get("pfHist") or []) if inr(h.get("d", ""))]
    print(f"\n## 포트 비중 이력 ({len(ph)}건, 커밋 때 자동 기록) — 날짜 · 종목 비중 · 메모")
    for h in sorted(ph, key=lambda x: x["d"]):
        tot = sum(i.get("amt", 0) for i in h["items"]) or 1
        print(f"- {h['d']} 총 {tot/1e8:.2f}억 | " + ", ".join(f"{i['name']} {i.get('amt',0)/tot*100:.0f}%" for i in h["items"]) + (f" | 메모: {strip(h.get('memo'))}" if h.get("memo") else ""))
    allh = sorted((d.get("pfHist") or []), key=lambda x: x["d"])
    prev = [h for h in allh if h["d"] < a.isoformat()]
    if prev: h = prev[-1]; tot = sum(i.get("amt", 0) for i in h["items"]) or 1; print(f"- (기간 직전 {h['d']}) " + ", ".join(f"{i['name']} {i.get('amt',0)/tot*100:.0f}%" for i in h["items"]))
    print("\n## 시나리오 (보관 제외) — 이름 · 보유 · 매수근거 · 매도조건 · 코멘트 · 안전가/매도가/시기 · 평단 · 기간 내 이력 변경")
    for s in (d.get("scenario") or []):
        if not s or s.get("archived") or s.get("name") == "현금": continue
        hist = [x for x in (s.get("hist") or []) if inr(x.get("d", ""))]
        print(f"- {s.get('name')} | 보유 {'O' if s.get('held') else 'X'} | 매수: {strip(s.get('buy'))[:200]} | 매도: {strip(s.get('sell'))[:200]} | 코멘트: {strip(s.get('cmt'))[:200]} | 안전 {s.get('safePx')} / 매도 {s.get('sellPx')} / 시기 {s.get('sellWhen')} | 평단 {s.get('avgPx')} | 수정 {s.get('updated')} | 기간 내 기록 {len(hist)}건" + ("".join(f"\n    · {x['d']}: 매수 {strip(x.get('buy'))[:80]} / 매도 {strip(x.get('sell'))[:80]}" for x in hist[:5])))
    tg = d.get("pfTarget") or []
    if tg: print("\n## 목표 포트 (목표% → 실제%)"); [print(f"- {t.get('name')} 목표 {t.get('pct')}% → 실제 {(t.get('entryAct') or 0)+(t.get('coreAct') or 0)+(t.get('alphaAct') or 0):.1f}% (진입 {t.get('entryAct')}/핵심 {t.get('coreAct')}/추가 {t.get('alphaAct')})") for t in tg]
    if d.get("pfMemo"): print("포트 메모:", strip(d["pfMemo"]))
    if d.get("pfTargetMemo"): print("목표 메모:", strip(d["pfTargetMemo"]))
    print("\n## 규칙 (Diary → 규칙 탭) — 점검 기준")
    print("1 매수 전 시나리오(매수근거·매도조건·안전가) 필수 · 2 한 종목 35%·최소 3종목 · 3 ±7% 움직인 날 매매 금지(다음 날 결정) · 4 리서치 북 90%/트레이딩 북 10%, 월 매매 10건 이하 · 5 매도는 가격이 아니라 시나리오로(+50%에서 1/3 트림) · 6 매매 없어도 매일 쓰기(5일 이상 공백 금지) · 7 손익비(상방÷하방) 3 이상일 때만 접근 · 8 시초가·NXT·장 시작 1시간 매수 금지")
    adv, _ = gh_get("sv/advice.json"); adv = adv or {}
    prevs = [x for x in (adv.get(kind) or []) if x.get("period") < period]
    if prevs:
        p = sorted(prevs, key=lambda x: x["period"])[-1]
        print(f"\n## 직전 {kind} 조언 ({p['period']}) — 이번 기간에 지켜졌는지 반드시 대조\n총평: {p.get('headline')}\n개선 사항: " + " / ".join(p.get("improve") or []) + "\n과제: " + " / ".join(p.get("next") or []))
    if kind != "weekly":
        sub = "weekly" if kind == "monthly" else "monthly"
        subs = [x for x in (adv.get(sub) or []) if a.isoformat() <= x.get("range", "")[:10] <= b.isoformat()]
        if subs: print(f"\n## 기간 내 {sub} 조언 총평 {len(subs)}건"); [print(f"- {x['period']}: {x.get('headline')} (점수 {x.get('score')})") for x in sorted(subs, key=lambda x: x["period"])]

def write(kind, path):
    j = json.loads(Path(path).read_text(encoding="utf-8"))
    assert j.get("kind") == kind, "kind 불일치"
    pat = {"weekly": r"^\d{4}-W\d{2}$", "monthly": r"^\d{4}-\d{2}$", "yearly": r"^\d{4}$"}[kind]
    assert re.match(pat, j.get("period", "")), "period 형식"
    for k in ("headline", "process", "critique", "advice"): assert isinstance(j.get(k), str) and len(j[k]) > 10, f"{k} 필요"
    for k in ("good", "mistakes", "improve", "next"): assert isinstance(j.get(k), list) and j[k], f"{k} 목록 필요"
    assert isinstance(j.get("score"), (int, float)) and 0 <= j["score"] <= 10, "score 0~10"
    bad = [w for w in ("매수 추천", "매도 추천", "사세요", "파세요") if w in json.dumps(j, ensure_ascii=False)]
    assert not bad, f"권유 표현 금지: {bad}"
    j["generated_at"] = datetime.datetime.now(KST).isoformat(timespec="seconds")
    adv, sha = gh_get("sv/advice.json"); adv = adv or {"weekly": [], "monthly": [], "yearly": []}
    arr = [x for x in (adv.get(kind) or []) if x.get("period") != j["period"]]
    arr.append(j); adv[kind] = sorted(arr, key=lambda x: x["period"])[-60:]
    adv["updated_at"] = j["generated_at"]
    c = gh_put("sv/advice.json", adv, sha, f"advice: {kind} {j['period']}")
    print(f"✓ sv/advice.json {c} · {kind} {j['period']} · {j['headline']}")

if __name__ == "__main__":
    cmd, kind = sys.argv[1], sys.argv[2]
    if cmd == "data": data(kind, sys.argv[3] if len(sys.argv) > 3 else default_period(kind))
    elif cmd == "write": write(kind, sys.argv[3])
