#!/usr/bin/env python3
"""
Hourly fast price update:
  1) GitHub에서 stocks.json 최신 불러오기
  2) yfinance로 모든 종목 시총/주가만 갱신 (price_history 생략 → 빠름)
     - 유저 입력 필드(target/safety/q1~q4) 및 price_history 는 절대 건드리지 않음
  3) GitHub Contents API로 커밋 & 푸시

한국/일본 장중(09~16시), 미국 장중(23~02시) KST 에 매 정각 실행합니다.
수동 실행: python3 hourly_price_update.py
"""

import os
import sys
import json
import base64
import datetime
import urllib.request
import urllib.error
from pathlib import Path

BASE_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(BASE_DIR))
# 번들된 패키지 경로 추가 (flask/yfinance 등 전역 설치 불필요)
for _p in ("pylib", "pylibs"):
    _pp = BASE_DIR / _p
    if _pp.is_dir():
        sys.path.insert(0, str(_pp))

from server import _get_usdkrw, _fetch_marketcap_krw  # noqa: E402

REPO = "whysosary-dot/invest-private"  # 비공개 데이터 리포
FILE_PATH = "sv/stocks.json"
BRANCH = "main"
TOKEN_FILE = BASE_DIR / ".github_token"

# 빠른 업데이트에서 덮어쓰는 필드만 (price_history / price_history_3y 제외)
FAST_WRITE_FIELDS = {"market_cap_oku", "currency", "price_native", "price_change_pct"}


def get_token() -> str:
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if tok:
        return tok.strip()
    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text().strip()
    raise SystemExit(
        f"GitHub 토큰 없음: 환경변수 GITHUB_TOKEN 또는 {TOKEN_FILE} 에 PAT를 넣어주세요."
    )


def gh_get_file(token: str):
    api = f"https://api.github.com/repos/{REPO}/contents/{FILE_PATH}?ref={BRANCH}"
    req = urllib.request.Request(
        api,
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "stock-valuation-hourly",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        meta = json.loads(resp.read().decode("utf-8"))
    content = base64.b64decode(meta["content"]).decode("utf-8")
    return json.loads(content), meta["sha"]


def gh_put_file(token: str, content_bytes: bytes, sha: str, message: str):
    api = f"https://api.github.com/repos/{REPO}/contents/{FILE_PATH}"
    body = {
        "message": message,
        "content": base64.b64encode(content_bytes).decode("ascii"),
        "branch": BRANCH,
        "sha": sha,
    }
    put = urllib.request.Request(
        api, method="PUT",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "stock-valuation-hourly",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(put, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _par_worker(args):
    i, s, usdkrw = args
    ticker = (s.get("ticker") or "").strip()
    if not ticker:
        return i, None
    adj = s.get("shares_adjustment") or 0
    override = s.get("shares_override") or 0
    try:
        r = _fetch_marketcap_krw(ticker, usdkrw, shares_adjustment=adj, shares_override=override)
    except Exception as e:  # noqa: BLE001
        r = {"ok": False, "error": str(e)}
    return i, r


def main():
    started = datetime.datetime.now()
    print(f"[{started:%Y-%m-%d %H:%M:%S}] 시간별 주가 업데이트 시작")

    token = get_token()

    # 1) GitHub에서 최신 불러오기
    data, sha = gh_get_file(token)
    stocks = data.get("stocks", [])
    if not stocks:
        print("  종목이 없습니다. 종료.")
        return 0
    print(f"  ↓ GitHub에서 {len(stocks)}개 종목 로드 (sha={sha[:7]})")

    usdkrw = _get_usdkrw()
    data["usdkrw"] = round(usdkrw, 2)
    print(f"  USDKRW: {usdkrw:.2f}")

    ok_count = 0
    fail = []

    # 병렬 조회 (네트워크 I/O 바운드) — 전체 실행 시간을 크게 단축
    from concurrent.futures import ThreadPoolExecutor

    def _fetch_one(s):
        ticker = (s.get("ticker") or "").strip()
        if not ticker:
            return s, None
        adj = s.get("shares_adjustment") or 0
        override = s.get("shares_override") or 0
        try:
            r = _fetch_marketcap_krw(ticker, usdkrw, shares_adjustment=adj, shares_override=override)
        except Exception as e:  # noqa: BLE001
            r = {"ok": False, "error": str(e)}
        return s, r

    # yfinance(curl_cffi)는 전역 세션을 공유하므로 다중 스레드 동시 호출 시
    # "Connection already opened" 오류로 전부 실패한다. 안정성을 위해 순차 조회하고,
    # 실패한 종목만 1회 순차 재시도한다.
    import multiprocessing as _mp
    from concurrent.futures import ProcessPoolExecutor as _PPE
    _ctx = _mp.get_context("spawn")
    _args = [(i, s, usdkrw) for i, s in enumerate(stocks)]
    try:
        with _PPE(max_workers=8, mp_context=_ctx) as _ex:
            _raw = list(_ex.map(_par_worker, _args))
    except Exception:  # fallback to sequential if pool fails
        _raw = [_par_worker(a) for a in _args]
    # retry failures sequentially on the ORIGINAL objects
    for _k, (i, r) in enumerate(_raw):
        if not (r and r.get("ok")):
            _raw[_k] = _par_worker((i, stocks[i], usdkrw))
    results = [(stocks[i], r) for i, r in _raw]

    for s, r in results:
        ticker = (s.get("ticker") or "").strip()
        if not ticker:
            fail.append(s.get("name", "?"))
            continue
        if r and r.get("ok"):
            # ★ 시총/주가 필드만 덮어씀. price_history 및 유저 입력은 유지
            s["market_cap_oku"] = r["market_cap_oku"]
            s["currency"] = r["currency"]
            s["price_native"] = r.get("price_native")
            s["price_change_pct"] = r.get("price_change_pct")
            ok_count += 1
            _mc = r.get('market_cap_oku')
            _pn = r.get('price_native')
            _pc = r.get('price_change_pct')
            _mc_s = f"{_mc:>14,.0f}" if isinstance(_mc, (int, float)) else f"{'N/A':>14s}"
            _pn_s = f"{_pn:>12,.2f}" if isinstance(_pn, (int, float)) else f"{'N/A':>12s}"
            _pc_s = f"{_pc:+.2f}%" if isinstance(_pc, (int, float)) else "N/A"
            print(f"  ✓ {s.get('name','?'):10s} {ticker:12s} {_mc_s} 억원  {_pn_s} ({_pc_s})")
        else:
            err = (r or {}).get("error")
            fail.append(f"{s.get('name','?')}({ticker}): {err}")
            print(f"  ✗ {s.get('name','?'):10s} {ticker:12s} — {err}")

    now_str = datetime.datetime.now().isoformat(timespec="seconds")
    data["updated_at"] = now_str
    print(f"\n  성공 {ok_count} / 실패 {len(fail)}")

    # 2) 로컬 백업
    try:
        local = BASE_DIR / "stocks.json"
        local.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except Exception as e:
        print(f"  (로컬 백업 실패 — 무시: {e})")

    # 3) GitHub에 PUT
    content = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    hour_kst = started.hour
    market = "한국/일본장" if 9 <= hour_kst <= 16 else "미국장"
    msg = f"⚡ {market} 주가 업데이트: {started:%Y-%m-%d %H:%M}"
    try:
        result = gh_put_file(token, content, sha, msg)
    except urllib.error.HTTPError as e:
        if e.code in (409, 422):
            print("  ⟳ 충돌 감지 — 재로드 후 재시도")
            data2, sha2 = gh_get_file(token)
            by_ticker = {(s.get("ticker") or ""): s for s in data2.get("stocks", [])}
            for s in stocks:
                latest = by_ticker.get(s.get("ticker") or "")
                if latest:
                    for k, v in latest.items():
                        if k not in FAST_WRITE_FIELDS:
                            s[k] = v
            data2["stocks"] = stocks
            data2["usdkrw"] = data["usdkrw"]
            data2["updated_at"] = now_str
            content = (json.dumps(data2, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            result = gh_put_file(token, content, sha2, msg + " (retry)")
        else:
            raise

    commit = result.get("commit", {})
    print(f"  ✓ GitHub 커밋: {commit.get('sha','?')[:7]}")
    if commit.get("html_url"):
        print(f"    {commit['html_url']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
