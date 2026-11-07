# 교체용 스케줄 태스크 프롬프트 — "Daily watchlist news brief"
# (태스크 설정에서 기존 프롬프트를 아래 내용으로 통째로 교체)
# trigger id: trig_0167G91Qk9kEFA9bkNUzdzF2  /  cron: CRON_TZ=Asia/Seoul 0 6 * * *
# --- 아래 줄부터 끝까지 복사 ---

관심종목 모닝 브리핑을 생성해 GitHub Pages에 배포한다.

파이프라인 원본은 whysosary-dot/watchlist-briefing 저장소이고, 그 로컬 클론이 연결 폴더 안에 있다: `~/mnt/stock-valuation/watchlist-briefing` (device_bash 기준). 설정(watchlist.json)은 이 저장소에 없고 build_brief.py가 invest-private 저장소에서 자동으로 불러온다. 유지보수 지침은 클론 안의 MAINTENANCE.md 참고.

실행은 전부 `device_bash`(사용자 맥의 로컬 셸)로 한다.

경로 주의: 세션 VM의 `$HOME`과 `/tmp`는 용량이 꽉 차 쓰기가 실패한다. 중간 파일은 반드시 `$HOME/mnt/stock-valuation/` 아래에 쓴다.

## Step 0 - 시나리오 종목 동기화 (device_bash)
Diary 탭 「종목 시나리오」(보관 제외·현금 제외)의 종목이 Interest 설정(invest-private interest/watchlist.json)에 자동으로 들어가도록 먼저 동기화한다. 스크립트가 추가·제거를 알아서 하고(손으로 넣은 종목은 건드리지 않음), 변경이 있을 때만 커밋한다.
```bash
cd $HOME/mnt/stock-valuation && timeout 120 python3 -I interest_sync.py 2>&1 | sed -E 's/(gh[pousr]_|github_pat_)[A-Za-z0-9_]+/[REDACTED]/g' | tail -3
```
출력의 "추가 n [...] · 제거 n [...]" 을 보고에 한 토막 남긴다("변경 없음"이면 생략). 실패해도 중단하지 말고 Step 1 로 진행한다.

## Step 1 - 파이프라인 최신화 + 후보 수집 (device_bash, timeout_ms=180000)

```bash
cd $HOME/mnt/stock-valuation/watchlist-briefing
TOK=$(cat $HOME/mnt/stock-valuation/.github_token)
git -c user.name="리송" -c user.email="whysosary@naver.com" \
  pull --rebase -q "https://whysosary-dot:$TOK@github.com/whysosary-dot/watchlist-briefing.git" main 2>&1 | tail -3
export GH_TOKEN=$TOK
CAND_PATH=$HOME/mnt/stock-valuation/_brief_candidates.json \
  python3 scripts/build_brief.py collect 2>&1 | tail -100
```

pull이 실패해도 중단하지 말고 기존 로컬 코드로 계속 진행한다.

출력: 블록별 후보 목록(id | 점수 | 출처 | 날짜 | 제목) + `_brief_candidates.json` 저장. 스코어링은 관련성 소프트 게이트 방식이라 제목에 회사명이 없는 본문 관련 기사도 후보에 포함된다 - 그래서 다음 단계의 선별이 중요하다.

## Step 2 - 후보 선별 + 헤드라인 (Claude가 직접 판단)

Step 1 출력을 보고 투자 판단에 유의미한 기사만 선별한다:

- 포함: 수주·계약·실적·증설·규제·판호·M&A·신제품·가격동향 등 주가 관련 이벤트
- 제외: ESG 보고서·채용·기념행사·단순 홍보성, 기사리스트/주간전망 같은 묶음글, 날짜가 오늘과 동떨어진 재유통 기사, 그리고 제목에 회사명이 없으면서 내용도 그 회사와 무관해 보이는 오탐(점수 낮은 rel=0 기사에서 주로 발생)
- 같은 사건 중복은 더 신뢰도 높은 출처 1건만
- 블록당 최대 3건 노출되므로 블록당 2~3건이면 충분. 쓸 만한 후보가 없는 블록은 비워 둔다.
- 헤드라인: 그날 가장 중요한 이슈 1~2줄 (한국어, 종목·수치 구체적으로)

선별 결과 저장 (device_bash):

```bash
cat > $HOME/mnt/stock-valuation/_picks.json <<'EOF'
{"selected": [id들], "headline": "헤드라인"}
EOF
```

## Step 3 - 빌드 & 배포 (device_bash)

```bash
cd $HOME/mnt/stock-valuation/watchlist-briefing
export GH_TOKEN=$(cat $HOME/mnt/stock-valuation/.github_token)
CAND_PATH=$HOME/mnt/stock-valuation/_brief_candidates.json \
  python3 scripts/build_brief.py build $HOME/mnt/stock-valuation/_picks.json 2>&1 | tail -20
```

PUT 2건(html, manifest)이 200/201인지 확인한다. 401/403이면 토큰 문제다.

## Step 4 - 보고

정상 배포 시 한 줄로만 보고하고 알림은 보내지 않는다:
"시나리오 동기화 <추가/제거 또는 생략> · 후보 N건 → 선별 M건, 헤드라인: <요약>, URL: https://whysosary-dot.github.io/watchlist-briefing/briefings/watchlist-brief-<날짜>.html"
후보 목록 전체 재출력 금지.

배포에 실패했거나 device_bash를 쓸 수 없었다면(맥이 꺼져 있어 연결 폴더가 없는 경우 포함) 반드시 PushNotification으로 실패 사유를 알린다. 브리핑이 조용히 누락되는 것이 가장 나쁘다.

## 유지보수 규칙

- 기업/섹터 추가·필터 조정이 필요하면 코드가 아니라 invest-private 저장소의 interest/watchlist.json만 수정한다 (방법: 클론 안의 MAINTENANCE.md).
- scripts/build_brief.py는 읽기 전용으로 취급(수정 금지). 단 스크립트 자체가 깨져 빌드가 실패하면 최소 수정으로 고치고 저장소에 푸시한 뒤 보고한다.
