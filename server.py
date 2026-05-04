#!/usr/bin/env python3
"""
Stock Valuation Local Server
- 로컬 Flask 서버
- index.html 서빙
- yfinance로 현재 시총 조회 (원화 환산)
- stocks.json 저장/로드
- git auto commit & push
"""

import os
import json
import base64
import datetime
import subprocess
import urllib.request
import urllib.error
from pathlib import Path
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

try:
    import yfinance as yf
except ImportError:
    print("[ERROR] yfinance가 필요합니다. pip install yfinance")
    raise

BASE_DIR = Path(__file__).parent.resolve()
DATA_FILE = BASE_DIR / "stocks.json"

app = Flask(__name__, static_folder=str(BASE_DIR), static_url_path="")
CORS(app)


# ─────────────────────────────────────────────
#   Helpers
# ─────────────────────────────────────────────

def _load_data():
    if not DATA_FILE.exists():
        return {"updated_at": "", "usdkrw": 0, "stocks": []}
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _get_usdkrw():
    """USDKRW 환율 조회 (yfinance)"""
    try:
        fx = yf.Ticker("KRW=X")
        info = fx.fast_info
        rate = info.get("lastPrice") or info.get("last_price")
        if rate and rate > 100:
            return float(rate)
        # fallback: history
        hist = fx.history(period="5d")
        if not hist.empty:
            return float(hist["Close"].iloc[-1])
    except Exception as e:
        print(f"[WARN] USDKRW 조회 실패: {e}")
    return 1400.0  # fallback


def _fetch_marketcap_krw(ticker: str, usdkrw: float, shares_adjustment: float = 0, shares_override: float = 0) -> dict:
    """ticker의 현재 시총을 원화(억원)로 반환.
    한국 증권사/네이버금융 표준(보통주 시총)에 맞추기 위해
    info.sharesOutstanding(보통주만)을 우선 사용하고,
    fast_info.shares(우선주 포함 가능성)는 폴백으로만 사용.

    shares_adjustment: yfinance 보고 발행주식수에 더할 보정치 (예: 최근 유상증자가
    아직 yfinance에 반영되지 않은 경우 신주 수만큼 양수로 지정).
    """
    try:
        t = yf.Ticker(ticker)
        fi = t.fast_info
        price = fi.get("lastPrice") or fi.get("last_price")
        currency = (fi.get("currency") or "").upper()

        # 1) get_info()의 sharesOutstanding(보통주) 우선
        shares = None
        full_info = {}
        try:
            full_info = t.get_info() or {}
            shares = full_info.get("sharesOutstanding")
            currency = currency or (full_info.get("currency") or "").upper()
            price = price or full_info.get("regularMarketPrice") or full_info.get("currentPrice")
        except Exception:
            full_info = {}

        # 2) 폴백: fast_info.shares (우선주 포함될 수 있음)
        if not shares:
            shares = fi.get("shares") or fi.get("shares_outstanding")

        # 2.5) 사용자 지정 주식수 완전 대체 (shares_override)
        try:
            override = float(shares_override or 0)
        except Exception:
            override = 0.0
        if override:
            shares = override

        # 2.6) 사용자 지정 보정치 (유증 등 yfinance 미반영분) — override가 없을 때만 적용
        else:
            try:
                adj = float(shares_adjustment or 0)
            except Exception:
                adj = 0.0
            if shares and adj:
                shares = float(shares) + adj

        market_cap_native = None
        if price and shares:
            market_cap_native = float(price) * float(shares)

        # 3) 마지막 폴백: yfinance 보고 marketCap (단, override/adj가 있으면 폴백 회피)
        adj = float(shares_adjustment or 0) if not override else 0.0
        if not market_cap_native and full_info and not adj and not override:
            market_cap_native = full_info.get("marketCap")

        if not market_cap_native:
            return {"ok": False, "error": "marketCap not found"}

        # 원화 변환
        if currency == "KRW" or ticker.endswith(".KS") or ticker.endswith(".KQ"):
            market_cap_krw = float(market_cap_native)
        elif currency == "USD":
            market_cap_krw = float(market_cap_native) * usdkrw
        elif currency == "JPY":
            # JPYKRW 간단 조회
            try:
                jpy = yf.Ticker("JPYKRW=X").fast_info
                jpy_rate = jpy.get("lastPrice") or jpy.get("last_price") or 9.0
            except Exception:
                jpy_rate = 9.0
            market_cap_krw = float(market_cap_native) * jpy_rate
        elif currency == "HKD":
            try:
                hkd = yf.Ticker("HKDKRW=X").fast_info
                hkd_rate = hkd.get("lastPrice") or hkd.get("last_price") or 180.0
            except Exception:
                hkd_rate = 180.0
            market_cap_krw = float(market_cap_native) * hkd_rate
        elif currency == "CNY" or currency == "RMB":
            try:
                cny = yf.Ticker("CNYKRW=X").fast_info
                cny_rate = cny.get("lastPrice") or cny.get("last_price") or 195.0
            except Exception:
                cny_rate = 195.0
            market_cap_krw = float(market_cap_native) * cny_rate
        else:
            # 기본: 달러로 간주
            market_cap_krw = float(market_cap_native) * usdkrw

        # 네이버 금융 종목 코드 계산 (해외주식용)
        naver_code = None
        if not (ticker.endswith('.KS') or ticker.endswith('.KQ')):
            exchange = (full_info.get('exchange') or '').upper()
            if exchange in ('NMS', 'NGM', 'NCM', 'NASDAQ'):
                naver_code = ticker + '.O'
            elif exchange in ('NYQ', 'NYSE'):
                naver_code = ticker + '.N'
            elif exchange in ('AMX', 'AMEX'):
                naver_code = ticker + '.A'
            else:
                naver_code = ticker  # fallback

        # 등락률 계산
        price_change_pct = None
        try:
            prev_close = fi.get("previous_close") or fi.get("regularMarketPreviousClose")
            if not prev_close and full_info:
                prev_close = full_info.get("regularMarketPreviousClose") or full_info.get("previousClose")
            if price and prev_close and float(prev_close) > 0:
                price_change_pct = round((float(price) - float(prev_close)) / float(prev_close) * 100, 2)
        except Exception:
            pass

        return {
            "ok": True,
            "market_cap_oku": round(market_cap_krw / 1e8, 1),  # 억원
            "currency": currency,
            "price_native": float(price) if price else None,
            "price_change_pct": price_change_pct,
            "naver_code": naver_code,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _fetch_price_history(ticker: str, period: str = '1y') -> list:
    """주간 종가 반환 (sparkline용). period: '1y' or '3y'"""
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period=period, interval='1wk')
        if hist.empty:
            return []
        return [round(float(x), 4) for x in hist['Close'].dropna().tolist()]
    except Exception:
        return []


def _run_git(*args, cwd=None):
    r = subprocess.run(
        ["git", *args],
        cwd=cwd or BASE_DIR,
        capture_output=True, text=True,
    )
    return r.returncode, r.stdout.strip(), r.stderr.strip()


# ─────────────────────────────────────────────
#   GitHub Contents API (fallback for local git)
# ─────────────────────────────────────────────
REPO = "whysosary-dot/stock-valuation"
FILE_PATH = "stocks.json"
TOKEN_FILE = BASE_DIR / ".github_token"
DEFAULT_BRANCH = "main"


def _get_gh_token():
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if tok:
        return tok.strip()
    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text().strip()
    return None


def _github_put(token: str, content_bytes: bytes, message: str):
    api = f"https://api.github.com/repos/{REPO}/contents/{FILE_PATH}"
    # 기존 sha
    req = urllib.request.Request(
        f"{api}?ref={DEFAULT_BRANCH}",
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "stock-valuation",
        },
    )
    sha = None
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            sha = json.loads(resp.read()).get("sha")
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise

    body = {
        "message": message,
        "content": base64.b64encode(content_bytes).decode("ascii"),
        "branch": DEFAULT_BRANCH,
    }
    if sha:
        body["sha"] = sha
    put = urllib.request.Request(
        api, method="PUT", data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "stock-valuation",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(put, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ─────────────────────────────────────────────
#   Routes
# ─────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(str(BASE_DIR), "index.html")


@app.route("/api/data", methods=["GET"])
def get_data():
    return jsonify(_load_data())


@app.route("/api/data", methods=["POST"])
def save_data():
    """HTML에서 수정한 전체 stocks 데이터 저장"""
    payload = request.get_json(force=True)
    data = _load_data()
    if "stocks" in payload:
        data["stocks"] = payload["stocks"]
    data["updated_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    _save_data(data)
    return jsonify({"ok": True, "updated_at": data["updated_at"]})


@app.route("/api/update-marketcap", methods=["POST"])
def update_marketcap():
    """모든 종목의 현재 시총을 yfinance로 업데이트"""
    data = _load_data()
    usdkrw = _get_usdkrw()
    data["usdkrw"] = round(usdkrw, 2)

    results = []
    for stock in data.get("stocks", []):
        ticker = stock.get("ticker", "").strip()
        if not ticker:
            results.append({"name": stock.get("name"), "ok": False, "error": "no ticker"})
            continue
        adj = stock.get("shares_adjustment") or 0
        override = stock.get("shares_override") or 0
        r = _fetch_marketcap_krw(ticker, usdkrw, shares_adjustment=adj, shares_override=override)
        if r["ok"]:
            stock["market_cap_oku"] = r["market_cap_oku"]
            stock["currency"] = r["currency"]
            stock["price_native"] = r.get("price_native")
            results.append({"name": stock.get("name"), "ticker": ticker, "ok": True,
                            "market_cap_oku": r["market_cap_oku"]})
        else:
            results.append({"name": stock.get("name"), "ticker": ticker, "ok": False,
                            "error": r.get("error")})

    data["updated_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    _save_data(data)
    return jsonify({"ok": True, "usdkrw": data["usdkrw"], "results": results, "data": data})


@app.route("/api/update-one", methods=["POST"])
def update_one():
    """하나의 ticker만 조회 (종목 추가 시 사용)"""
    payload = request.get_json(force=True)
    ticker = payload.get("ticker", "").strip()
    if not ticker:
        return jsonify({"ok": False, "error": "ticker required"}), 400
    usdkrw = _get_usdkrw()
    r = _fetch_marketcap_krw(ticker, usdkrw)
    r["usdkrw"] = round(usdkrw, 2)
    return jsonify(r)


@app.route("/api/commit-push", methods=["POST"])
def commit_push():
    """stocks.json을 GitHub Contents API로 직접 커밋 (로컬 git 상태 무관)"""
    payload = request.get_json(silent=True) or {}
    msg = payload.get("message") or f"📊 시총 업데이트: {datetime.date.today().isoformat()}"

    token = _get_gh_token()
    if not token:
        return jsonify({"ok": False, "error": "GitHub 토큰 없음 (.github_token 파일 확인)"}), 500

    # stocks.json 최신 내용 로드
    try:
        content = (BASE_DIR / "stocks.json").read_bytes()
    except FileNotFoundError:
        return jsonify({"ok": False, "error": "stocks.json 없음"}), 500

    try:
        result = _github_put(token, content, msg)
        commit = result.get("commit", {})
        return jsonify({
            "ok": True, "pushed": True, "message": msg,
            "commit_sha": commit.get("sha"),
            "commit_url": commit.get("html_url"),
        })
    except urllib.error.HTTPError as e:
        return jsonify({"ok": False, "error": f"HTTP {e.code}: {e.read().decode('utf-8', 'ignore')[:300]}"}), 500
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5055))
    print(f"\n🚀 Stock Valuation Server")
    print(f"   http://127.0.0.1:{port}")
    print(f"   데이터: {DATA_FILE}\n")
    app.run(host="127.0.0.1", port=port, debug=False)
