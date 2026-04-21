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


def _fetch_marketcap_krw(ticker: str, usdkrw: float) -> dict:
    """ticker의 현재 시총을 원화(억원)로 반환"""
    try:
        t = yf.Ticker(ticker)
        info = t.fast_info
        price = info.get("lastPrice") or info.get("last_price")
        shares = info.get("shares") or info.get("shares_outstanding")
        currency = (info.get("currency") or "").upper()

        market_cap_native = None
        if price and shares:
            market_cap_native = float(price) * float(shares)

        # fallback - info dict
        if not market_cap_native:
            try:
                full_info = t.get_info()
                market_cap_native = full_info.get("marketCap")
                currency = currency or (full_info.get("currency") or "").upper()
                price = price or full_info.get("regularMarketPrice") or full_info.get("currentPrice")
            except Exception:
                pass

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

        return {
            "ok": True,
            "market_cap_oku": round(market_cap_krw / 1e8, 1),  # 억원
            "currency": currency,
            "price_native": float(price) if price else None,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


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
        r = _fetch_marketcap_krw(ticker, usdkrw)
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
