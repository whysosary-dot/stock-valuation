#!/usr/bin/env python3
"""
📨 Telegram 탭 — 1단계: 오늘(KST) 내 텔레그램 모든 채널·그룹 메시지에서 언급된 종목 추출.

- Telethon 세션은 ~/Desktop/Claude/AWAKE 전자 공시/awake_session.session (사용자 본인 계정) 을
  /tmp/tg/ 로 복사해 사용한다 (세션 파일은 동시 접근을 싫어함).
- 종목 사전: 한국 = stock-screener 최신 스냅샷(전 종목 이름·코드), 미국 = 아래 ALIAS_US + $TICKER / (TICKER) 패턴.
- 출력(/tmp/tg/mentions_<date>.json): 종목별 언급 수·채널 목록·발췌(요약용, 최대 3개·240자).
  ★ 발췌는 요약 작성용 임시 파일이며 공개 저장소에는 올리지 않는다 (tg_build.py 가 요약·밸류에이션만 푸시).

사용: PYTHONPATH=/tmp/pylibs python3 tg_collect.py [YYYY-MM-DD] [--since-hours=24]
"""
import sys, os, re, json, shutil, collections, urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE = Path(__file__).parent.resolve()
KST = timezone(timedelta(hours=9))
def _tg_api():
    """텔레그램 API 자격: 환경변수 TG_API_ID/TG_API_HASH (GitHub Actions) 또는 저장소 밖 .tg_api (gitignore): 1행 api_id, 2행 api_hash"""
    if os.environ.get("TG_API_ID") and os.environ.get("TG_API_HASH"):
        return int(os.environ["TG_API_ID"]), os.environ["TG_API_HASH"]
    f = BASE / ".tg_api"
    if not f.exists():
        raise SystemExit(f"텔레그램 API 파일 없음: {f} (1행 api_id, 2행 api_hash)")
    a, b = [x.strip() for x in f.read_text().splitlines()[:2]]
    return int(a), b
API_ID, API_HASH = _tg_api()
SRC_SESSION = BASE.parent / "AWAKE 전자 공시" / "awake_session.session"
WORK = Path("/tmp/tg"); WORK.mkdir(parents=True, exist_ok=True)
SESSION = WORK / "sess"

date_arg = next((a for a in sys.argv[1:] if re.fullmatch(r"\d{4}-\d{2}-\d{2}", a)), None)
since_h = next((int(a.split("=")[1]) for a in sys.argv if a.startswith("--since-hours=")), None)
today = datetime.strptime(date_arg, "%Y-%m-%d").date() if date_arg else datetime.now(KST).date()
day_start = datetime(today.year, today.month, today.day, tzinfo=KST)
day_end = day_start + timedelta(hours=23, minutes=50)  # 00:00 ~ 23:50 KST
if since_h:
    day_start = datetime.now(KST) - timedelta(hours=since_h)

# ── 미국 종목 한글 별칭 (채널에서 자주 쓰는 표기) ──
ALIAS_US = {
    "엔비디아": "NVDA", "테슬라": "TSLA", "애플": "AAPL", "마이크로소프트": "MSFT", "아마존": "AMZN",
    "알파벳": "GOOGL", "구글": "GOOGL", "메타": "META", "브로드컴": "AVGO", "마이크론": "MU", "TSMC": "TSM",
    "팔란티어": "PLTR", "넷플릭스": "NFLX", "오라클": "ORCL", "인텔": "INTC", "퀄컴": "QCOM", "코인베이스": "COIN",
    "일라이릴리": "LLY", "릴리": "LLY", "노보노디스크": "NVO", "코스트코": "COST", "월마트": "WMT", "비자": "V",
    "마스터카드": "MA", "JP모건": "JPM", "엑손모빌": "XOM", "버티브": "VRT", "슈퍼마이크로": "SMCI", "델": "DELL",
    "아리스타": "ANET", "시스코": "CSCO", "세일즈포스": "CRM", "서비스나우": "NOW", "스노우플레이크": "SNOW",
    "데이터독": "DDOG", "크라우드스트라이크": "CRWD", "우버": "UBER", "에어비앤비": "ABNB", "스타벅스": "SBUX",
    "나이키": "NKE", "디즈니": "DIS", "보잉": "BA", "록히드마틴": "LMT", "캐터필러": "CAT", "GE버노바": "GEV",
    "콘스텔레이션": "CEG", "비스트라": "VST", "뉴스케일": "SMR", "오클로": "OKLO", "아이온큐": "IONQ",
    "리게티": "RGTI", "로켓랩": "RKLB", "소파이": "SOFI", "로빈후드": "HOOD", "힘스앤허스": "HIMS",
    "어플라이드머티리얼즈": "AMAT", "램리서치": "LRCX", "유나이티드헬스": "UNH", "화이자": "PFE", "머크": "MRK",
    "암젠": "AMGN", "어도비": "ADBE", "쇼피파이": "SHOP", "페이팔": "PYPL", "앱러빈": "APP", "레딧": "RDDT",
    "알리바바": "BABA", "핀둬둬": "PDD", "카바나": "CVNA", "테라다인": "TER", "마벨": "MRVL", "코히런트": "COHR",
    "루멘텀": "LITE", "크레도": "CRDO", "아스테라랩스": "ALAB", "셀레스티카": "CLS", "자빌": "JBL", "이튼": "ETN",
    "콴타": "PWR", "엠코": "AMKR", "온세미": "ON", "텍사스인스트루먼트": "TXN", "웨스턴디지털": "WDC",
    "샌디스크": "SNDK", "시게이트": "STX", "AMD": "AMD", "ASML": "ASML", "IBM": "IBM", "AMAT": "AMAT",
}
US_TICKER_SET = set(ALIAS_US.values())

# ── 한국 종목 별칭 (약칭) ──
ALIAS_KR = {
    "하이닉스": "SK하이닉스", "삼전": "삼성전자", "현차": "현대차", "엘지엔솔": "LG에너지솔루션", "엔솔": "LG에너지솔루션",
    "한조해": "HD한국조선해양", "한국조선": "HD한국조선해양", "현대중": "HD현대중공업", "현중": "HD현대중공업",
    "삼중": "삼성중공업", "한화오션": "한화오션", "에코프로비엠": "에코프로비엠", "알테오젠": "알테오젠",
    "삼바": "삼성바이오로직스", "삼성바이오": "삼성바이오로직스", "셀트": "셀트리온", "네이버": "NAVER",
    "카카오": "카카오", "현대로템": "현대로템", "한화에어로": "한화에어로스페이스", "에어로": "한화에어로스페이스",
    "LIG넥스원": "LIG넥스원", "두산에너": "두산에너빌리티", "두산에너빌": "두산에너빌리티",
}
# 직접 수집하지 않는 채널 (이름 부분 일치). AWAKE 실시간 공시는 Awake 탭이 따로 있고 종목이 너무 많아 제외.
SKIP_CHANNELS = ["AWAKE - 실시간 주식 공시"]

# 너무 흔한 말이라 단독 매칭을 금지하는 종목명 (뒤에 (코드) 가 붙은 경우만 인정)
BLOCK = {"동양", "한화", "삼성", "현대", "우리", "미래", "성장", "한국", "대성", "서울", "경남", "전북", "대한",
         "동부", "신한", "하나", "KB", "SK", "LG", "GS", "CJ", "한진", "태양", "기업", "리더", "신라", "부산",
         "제일", "중앙", "동아", "대구", "에이", "바이오", "모두", "한솔", "두산", "효성", "코오롱", "대상",
         "오리온", "삼양", "롯데", "포스코", "하림", "농심", "국보", "유니온", "다우", "이마트", "신세계", "AK",
         "DB", "DL", "HL", "KT", "LS", "NH", "OCI", "SG", "TP", "경동", "경인", "남양", "대동", "대원", "동국",
         "동성", "동원", "동화", "삼익", "삼화", "서연", "서울", "세아", "세원", "아이", "유성", "유진", "이수",
         "인산", "일성", "일진", "진로", "참좋은", "청담", "태경", "태영", "한양", "한일", "화성", "황금", "흥국"}


PARTICLES = "은는이가을를의도와과로에서만까지부터처럼보다랑이나며및"
def occurs(nm, text):
    """종목명/별칭이 '독립된 단어'로 등장하는지. 앞은 한글·영문이 아니어야 하고,
    짧은 이름(≤2자)은 뒤가 한글이면 조사일 때만 인정 (모델→델, 메타버스→메타 오탐 방지)."""
    for m in re.finditer(re.escape(nm), text):
        s, e = m.start(), m.end()
        if s > 0 and re.match(r"[가-힣A-Za-z0-9]", text[s - 1]):
            continue
        # 뒤에 한글이 이어지면 조사일 때만 인정 (하이브리드→하이브, 아스트라→아스트, 모델→델 오탐 방지)
        if e < len(text) and re.match(r"[가-힣]", text[e]) and text[e] not in PARTICLES:
            continue
        return True
    return False


def load_kr_universe():
    idx = json.loads(urllib.request.urlopen(
        "https://raw.githubusercontent.com/whysosary-dot/stock-screener/main/daily/index.json", timeout=30).read())
    d = json.loads(urllib.request.urlopen(
        f"https://raw.githubusercontent.com/whysosary-dot/stock-screener/main/daily/{idx['latest_date']}.json", timeout=30).read())
    uni = {}
    for s in d["stocks"]:
        nm = s["name"].strip()
        if nm.endswith(("우", "우B", "우C", "1우", "2우B", "3우B")) and not nm.endswith(("우리", "우유", "우성", "우진", "우신")):
            continue  # 우선주 제외
        uni[nm] = {"code": s["ticker"], "market": s["market"], "mcap": s.get("market_cap")}
    return uni


def main():
    from telethon.sync import TelegramClient
    tg_session = os.environ.get("TG_SESSION")  # GitHub Actions: StringSession (secret)
    if tg_session:
        from telethon.sessions import StringSession
        sess = StringSession(tg_session.strip())
    else:
        if not SESSION.with_suffix(".session").exists():
            if not SRC_SESSION.exists():
                raise SystemExit(f"텔레그램 세션 없음: {SRC_SESSION}")
            shutil.copy(SRC_SESSION, SESSION.with_suffix(".session"))
        sess = str(SESSION)

    uni = load_kr_universe()
    names = sorted(uni.keys(), key=len, reverse=True)
    print(f"한국 종목 사전 {len(uni)}개 · 미국 별칭 {len(ALIAS_US)}개 · 기간 {day_start:%m-%d %H:%M} ~ {min(day_end, datetime.now(KST)):%m-%d %H:%M} KST")

    client = TelegramClient(sess, API_ID, API_HASH)
    client.connect()
    if not client.is_user_authorized():
        raise SystemExit("텔레그램 세션 미인증")

    mentions = collections.defaultdict(lambda: {"mentions": 0, "channels": collections.Counter(), "snips": []})
    channels = []
    total_msgs = 0
    for dlg in client.iter_dialogs():
        if not (dlg.is_channel or dlg.is_group):
            continue
        # 제외 채널: 직접 수집하지 않는다. 다른 채널이 전달(forward)한 글은 그 채널에서 읽히므로 자연히 포함된다.
        if any(s in (dlg.name or "") for s in SKIP_CHANNELS):
            print(f"  · 제외: {dlg.name}")
            continue
        cnt = 0
        for m in client.iter_messages(dlg.entity, limit=2000):
            if m.date is None:
                continue
            t = m.date.astimezone(KST)
            if t < day_start:
                break
            if t >= day_end:
                continue
            text = (m.text or "").strip()
            if len(text) < 8:
                continue
            cnt += 1
            found = {}
            work = text
            # 1) 한국 종목명 — 긴 이름부터, 찾으면 그 자리를 비워 중복 매칭 방지
            for nm in names:
                if nm not in work or not occurs(nm, work):
                    continue
                if (len(nm) <= 2 or nm in BLOCK):
                    if not re.search(re.escape(nm) + r"\s*\(\s*" + uni[nm]["code"] + r"\s*\)", work):
                        continue
                found["KR:" + uni[nm]["code"]] = nm
                work = work.replace(nm, " ")
            for al, nm in ALIAS_KR.items():
                if nm in uni and occurs(al, work):
                    found["KR:" + uni[nm]["code"]] = nm
                    work = work.replace(al, " ")
            # 2) 6자리 코드 직접 언급
            for code in re.findall(r"(?<!\d)(\d{6})(?!\d)", text):
                nm = next((n for n, v in uni.items() if v["code"] == code), None)
                if nm:
                    found["KR:" + code] = nm
            # 3) 미국 — 별칭 / $TICKER / (TICKER)
            for al, tk in ALIAS_US.items():
                if occurs(al, work):
                    found["US:" + tk] = al
                    work = work.replace(al, " ")
            for tk in re.findall(r"\$([A-Z]{1,5})\b", text) + re.findall(r"\(([A-Z]{2,5})\)", text):
                if tk in US_TICKER_SET:
                    found["US:" + tk] = tk
            for key, nm in found.items():
                e = mentions[key]
                e["mentions"] += 1
                e["channels"][dlg.name] += 1
                e.setdefault("name", nm)
                if len(e["snips"]) < 3:
                    snip = re.sub(r"\s+", " ", text)
                    i = max(0, snip.find(nm) - 60)
                    e["snips"].append({"ch": dlg.name, "t": t.strftime("%H:%M"), "text": snip[i:i + 240]})
        if cnt:
            channels.append({"name": dlg.name, "id": dlg.id, "msgs": cnt})
        total_msgs += cnt
    client.disconnect()

    out_stocks = []
    for key, e in mentions.items():
        mk, code = key.split(":", 1)
        if mk == "KR":
            nm = next((n for n, v in uni.items() if v["code"] == code), e.get("name"))
            meta = uni.get(nm, {})
            out_stocks.append({"key": key, "mkt": "KR", "code": code, "name": nm, "market": meta.get("market"),
                               "mentions": e["mentions"], "channels": [c for c, _ in e["channels"].most_common()],
                               "snips": e["snips"]})
        else:
            out_stocks.append({"key": key, "mkt": "US", "code": code, "name": e.get("name", code), "market": "US",
                               "mentions": e["mentions"], "channels": [c for c, _ in e["channels"].most_common()],
                               "snips": e["snips"]})
    out_stocks.sort(key=lambda s: (-len(s["channels"]), -s["mentions"], s["name"]))
    out = {"date": today.isoformat(), "collected_at": datetime.now(KST).isoformat(timespec="seconds"),
           "channels": sorted(channels, key=lambda c: -c["msgs"]), "total_msgs": total_msgs, "stocks": out_stocks}
    p = WORK / f"mentions_{today}.json"
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(f"메시지 {total_msgs}건 / 채널 {len(channels)}개 → 종목 {len(out_stocks)}개 (KR {sum(1 for s in out_stocks if s['mkt']=='KR')} · US {sum(1 for s in out_stocks if s['mkt']=='US')})")
    for s in out_stocks[:15]:
        print(f"  {s['name']:14s} {s['mkt']} 채널 {len(s['channels'])} · 언급 {s['mentions']}  [{', '.join(s['channels'][:3])}]")
    print(f"→ {p}")
    # 요약 템플릿 (Claude 가 채움)
    tpl = {s["key"]: "" for s in out_stocks}
    (WORK / f"summaries_{today}.template.json").write_text(json.dumps(tpl, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
