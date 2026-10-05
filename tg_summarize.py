#!/usr/bin/env python3
"""
📨 Telegram 탭 — 2단계(클라우드용): 종목별 한 줄 요약 작성.

  입력  /tmp/tg/mentions_<date>.json   (tg_collect.py)
  출력  /tmp/tg/summaries_<date>.json  {"KR:005930": "한 줄 요약", ...}

- CLAUDE_CODE_OAUTH_TOKEN(구독, `claude setup-token`) 이 있으면 Claude Code CLI(`claude -p`)로,
  ANTHROPIC_API_KEY 가 있으면 Messages API 로 발췌(snips)만 근거로 60~120자 한국어 요약을 쓴다.
- 둘 다 없거나 호출이 실패하면 첫 발췌를 다듬어 넣는다 (fallback, 'ⓘ 발췌:' 접두).
- 종목이 120개를 넘으면 채널 수·언급 수 상위 120개만 요약한다.

사용: python3 tg_summarize.py [YYYY-MM-DD]
"""
import sys, os, re, json, time, subprocess, shutil, urllib.request, urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path

KST = timezone(timedelta(hours=9))
WORK = Path("/tmp/tg")
date_arg = next((a for a in sys.argv[1:] if re.fullmatch(r"\d{4}-\d{2}-\d{2}", a)), None)
DATE = date_arg or datetime.now(KST).strftime("%Y-%m-%d")
MODEL = os.environ.get("TG_SUMMARY_MODEL", "claude-sonnet-4-5")
MAX_STOCKS = 120
BATCH = 25

RULES = """너는 증권사 리서치·투자 커뮤니티 텔레그램 채널에서 오늘 언급된 종목을 정리하는 도우미다.
각 종목마다 '오늘 왜 언급됐는지'를 한국어 한 줄(60~120자)로 쓴다.
규칙:
- 발췌(snips)에 있는 사실·숫자만 쓴다. 목표주가·실적 수치·수주·순매수 금액처럼 검증 가능한 것 우선. 없는 내용을 만들지 않는다.
- 여러 채널이면 공통 주제를 먼저, 다음에 가장 구체적인 숫자를 붙인다.
- 발췌가 종목과 무관한 오탐(예: 브라질 SBS, '하이브리드'→하이브, 채널 서명의 증권사명, 채널이 코드를 잘못 적은 경우)이면 "오탐 — 사유" 라고 적는다.
- 투자 커뮤니티 잡담·관심도 집계만 있으면 "커뮤니티 언급(집계)" 수준으로 짧게.
- 투자 권유·판단("매수 추천")은 쓰지 않는다. 사실 정리만.
출력은 JSON 객체 하나만: {"KR:005930": "…", "US:NVDA": "…"} (키는 입력의 key 와 정확히 일치, 다른 텍스트 없이)."""


def call_claude(key, stocks):
    payload = [{"key": s["key"], "name": s["name"], "mkt": s["mkt"], "mentions": s["mentions"],
                "channels": s["channels"], "snips": s["snips"]} for s in stocks]
    body = {"model": MODEL, "max_tokens": 4000, "system": RULES,
            "messages": [{"role": "user", "content": "오늘 날짜: " + DATE + "\n\n종목 목록:\n" +
                          json.dumps(payload, ensure_ascii=False)}]}
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=json.dumps(body).encode(),
                                 headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                                          "content-type": "application/json"}, method="POST")
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                d = json.loads(r.read())
            text = "".join(b.get("text", "") for b in d.get("content", []))
            m = re.search(r"\{.*\}", text, re.S)
            return json.loads(m.group(0)) if m else {}
        except urllib.error.HTTPError as e:
            err = e.read().decode(errors="ignore")[:300]
            print(f"  ⚠ Claude API {e.code}: {err}")
            if e.code in (429, 529, 500, 502, 503) and attempt < 2:
                time.sleep(10 * (attempt + 1)); continue
            return {}
        except Exception as e:
            print(f"  ⚠ Claude API 오류: {e}")
            if attempt < 2:
                time.sleep(5); continue
            return {}
    return {}


def call_claude_cli(stocks):
    """Claude Code CLI (구독 OAuth 토큰) — 도구 없이 텍스트 응답만 받는다."""
    payload = [{"key": s["key"], "name": s["name"], "mkt": s["mkt"], "mentions": s["mentions"],
                "channels": s["channels"], "snips": s["snips"]} for s in stocks]
    prompt = RULES + "\n\n오늘 날짜: " + DATE + "\n\n종목 목록:\n" + json.dumps(payload, ensure_ascii=False)
    exe = shutil.which("claude")
    if not exe:
        print("  ⚠ claude CLI 없음"); return {}
    for attempt in range(2):
        try:
            r = subprocess.run([exe, "-p", "--output-format", "json", "--model", os.environ.get("TG_SUMMARY_CLI_MODEL", "sonnet"),
                                "--tools", ""], input=prompt, capture_output=True, text=True, timeout=300,
                               env={**os.environ, "CI": "1"})
            if r.returncode != 0:
                print(f"  ⚠ claude CLI rc={r.returncode}: {(r.stderr or r.stdout)[:300]}")
                if attempt == 0: time.sleep(10); continue
                return {}
            d = json.loads(r.stdout)
            text = d.get("result", "") if isinstance(d, dict) else ""
            m = re.search(r"\{.*\}", text, re.S)
            return json.loads(m.group(0)) if m else {}
        except Exception as e:
            print(f"  ⚠ claude CLI 오류: {e}")
            if attempt == 0: time.sleep(5); continue
            return {}
    return {}


def fallback(s):
    if not s["snips"]:
        return "커뮤니티 언급(집계)"
    t = re.sub(r"\s+", " ", s["snips"][0]["text"]).strip()
    i = t.find(s["name"])
    if i > 40:
        t = t[i - 40:]
    return "ⓘ 발췌: " + t[:110].rstrip() + "…"


def main():
    mp = WORK / f"mentions_{DATE}.json"
    if not mp.exists():
        raise SystemExit(f"없음: {mp} — tg_collect.py 먼저")
    stocks = json.loads(mp.read_text())["stocks"]
    stocks = sorted(stocks, key=lambda s: (-len(s["channels"]), -s["mentions"]))[:MAX_STOCKS]
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    oauth = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "").strip()
    out = {}
    if key or oauth:
        print("  요약 엔진:", "Claude Code CLI(구독)" if oauth and not key else "Messages API")
        for i in range(0, len(stocks), BATCH):
            chunk = stocks[i:i + BATCH]
            res = call_claude(key, chunk) if key else call_claude_cli(chunk)
            for s in chunk:
                v = (res.get(s["key"]) or "").strip()
                out[s["key"]] = v if v else fallback(s)
            print(f"  Claude 요약 {i + len(chunk)}/{len(stocks)} (응답 {sum(1 for s in chunk if res.get(s['key']))}건)")
    else:
        print("⚠ CLAUDE_CODE_OAUTH_TOKEN / ANTHROPIC_API_KEY 없음 — 발췌 기반 fallback 요약")
        for s in stocks:
            out[s["key"]] = fallback(s)
    sp = WORK / f"summaries_{DATE}.json"
    sp.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(f"{DATE} · 요약 {len(out)}건 → {sp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
