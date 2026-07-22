#!/usr/bin/env python3
"""
포트 비중 월말 스냅샷 → 월간 수익률 그래프 데이터
- 매월 '마지막 주 평일' 저녁 9시에 실행 (스케줄은 28~31일 21:00에 돌고, 여기서 마지막 평일인지 자체 판정)
- diary.json 의 시나리오(보유중) 종목 × stocks.json 현재가(마지막 거래일 종가)로 합계 계산
- invest-private sv/pf_monthly.json 에 {ym, d, total} 누적 저장 (같은 달은 교체)
사용: python3 pf_monthly_snapshot.py [--dry-run] [--force]
"""
import sys, json, base64, datetime, calendar, urllib.request, urllib.error
from pathlib import Path

BASE = Path(__file__).parent.resolve()
REPO = "whysosary-dot/invest-private"
BRANCH = "main"
DRY = "--dry-run" in sys.argv
FORCE = "--force" in sys.argv  # 마지막 평일 아니어도 강제 기록 (테스트용)


def token():
    f = BASE / ".github_token"
    if not f.exists():
        raise SystemExit("토큰 없음")
    return f.read_text().strip()


def is_last_weekday(d: datetime.date) -> bool:
    """오늘이 이 달의 마지막 '평일'인가 (남은 날이 전부 주말이면 True)"""
    if d.weekday() >= 5:
        return False
    last = calendar.monthrange(d.year, d.month)[1]
    for day in range(d.day + 1, last + 1):
        if datetime.date(d.year, d.month, day).weekday() < 5:
            return False
    return True


def gh_raw(tok, path):
    req = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}/contents/{path}?ref={BRANCH}",
        headers={"Authorization": f"token {tok}", "Accept": "application/vnd.github.raw", "User-Agent": "sv"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def gh_sha(tok, path):
    req = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}/contents/{path}?ref={BRANCH}",
        headers={"Authorization": f"token {tok}", "Accept": "application/vnd.github+json", "User-Agent": "sv"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())["sha"]
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def gh_put(tok, path, obj, sha, msg):
    body = {"message": msg, "branch": BRANCH,
            "content": base64.b64encode((json.dumps(obj, ensure_ascii=False, indent=2) + "\n").encode()).decode()}
    if sha:
        body["sha"] = sha
    req = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}/contents/{path}", method="PUT",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"token {tok}", "Accept": "application/vnd.github+json",
                 "User-Agent": "sv", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def norm(s):
    return str(s or "").lower().replace(" ", "")


def main():
    today = datetime.date.today()
    if not FORCE and not is_last_weekday(today):
        print(f"  {today} 는 마지막 평일 아님 — 스킵")
        return 0

    tok = token()
    diary = gh_raw(tok, "sv/diary.json") or {}
    stocks_data = gh_raw(tok, "sv/stocks.json") or {}
    stocks = stocks_data.get("stocks", [])
    usdkrw = float(stocks_data.get("usdkrw") or 0)

    def find_stock(name):
        n = norm(name)
        if not n:
            return None
        hit = next((s for s in stocks if norm(s.get("name")) == n), None)
        if not hit:
            cands = [s for s in stocks if n in norm(s.get("name")) or norm(s.get("name")) in n]
            if len(cands) == 1:
                hit = cands[0]
        return hit

    total = 0
    detail = []
    for it in (diary.get("scenario") or []):
        if not it or it.get("archived") or not it.get("held"):
            continue
        amt = 0
        stk = find_stock(it.get("name"))
        px = float(stk.get("price_native") or 0) if stk else 0
        if it.get("qty") and px > 0:
            if stk.get("currency") and stk["currency"] != "KRW" and usdkrw > 0:
                px *= usdkrw
            amt = round(float(it["qty"]) * px)
        elif it.get("amt"):
            amt = round(float(it["amt"]))
        if amt > 0:
            total += amt
            detail.append(f"{it.get('name')}={amt:,}")

    if total <= 0:
        print("  보유 종목 합계 0 — 기록 스킵")
        return 0

    ym = f"{today.year}-{today.month:02d}"
    print(f"  {ym} ({today}) 합계 {total:,}원  [{', '.join(detail)}]")

    if DRY:
        print("  (dry-run — 커밋 안 함)")
        return 0

    hist = gh_raw(tok, "sv/pf_monthly.json") or []
    hist = [h for h in hist if h.get("ym") != ym]
    hist.append({"ym": ym, "d": today.isoformat(), "total": total})
    hist.sort(key=lambda h: h["ym"])
    sha = gh_sha(tok, "sv/pf_monthly.json")
    res = gh_put(tok, "sv/pf_monthly.json", hist, sha, f"pf: {ym} 월말 스냅샷 {total:,}원")
    print(f"  ✓ 커밋 {res['commit']['sha'][:7]} (총 {len(hist)}개월)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
