# 투자 대시보드 작업 인수인계 (새 세션용)

새 세션에서 이 파일을 먼저 읽으면 지금까지의 작업 방식을 그대로 이어갈 수 있다.
사용자: 리송. 대시보드: https://whysosary-dot.github.io/stock-valuation/ (탭마다 별도 GitHub Pages 저장소를 iframe 으로 묶은 구조).

## 작업 방식 (가장 중요)
- 모든 수정은 `Desktop/Claude/stock-valuation/` 의 로컬 파일을 고친 뒤 **GitHub Contents API** 로 푸시한다
  (GET sha → PUT base64). 토큰은 `stock-valuation/.github_token`. **로컬 git(commit/push)은 쓰지 않는다** — 맥 저장소가 원격과 수백 커밋 어긋나 있다.
- 푸시 전에 inline `<script>` 를 추출해 `node -c` 로 문법 검사. 푸시 후 60~75초 뒤 `curl …?cb=$(date +%s)` 로 배포 확인.
- 다른 저장소(stock-screener 등)도 같은 토큰으로 Contents API 푸시. 로컬 사본은 `Desktop/Claude/<repo>/` 에 있는 것도 있고 없는 것도 있다(없으면 raw.githubusercontent 에서 받아 고친 뒤 푸시).
- 검증은 Claude 의 내장 브라우저(`mcp__Claude_Browser__*`)로 실제 페이지를 열어 확인한다. 대시보드는 PAT 가 있는 기기에서만 개인 데이터(stocks/diary)가 보인다.
- 사용자 자격(GitHub 토큰, DART 키 `.dart_api_key`, 텔레그램 `.tg_api`)은 공개 저장소에 절대 넣지 않는다. 사용자 이메일을 외부 서비스 헤더에 쓰지 않는다.
- 보고는 짧게. 사용자는 한국어, 반말 섞인 짧은 지시를 준다. 디자인 변경은 "이상하면 원복" 전제로 과감히 해도 된다.

## 저장소 지도 (whysosary-dot/…)
| 탭 | 저장소 / 경로 | 데이터·갱신 |
|---|---|---|
| Stock Valuation·Diary·Folder (대시보드 본체) | `stock-valuation/index.html`, `sheet.html` | 데이터는 비공개 `invest-private` `sv/stocks.json`, `sv/diary.json`, `sv/sheets/*` |
| Report (증권사 리포트) | `stock-valuation/report/` + `report_collect.py` | WiseReport 수집, 평일·토 07:20 스케줄 `report-daily-collect` |
| Telegram (채널 언급 종목) | `stock-valuation/telegram/` + `tg_collect.py`, `tg_build.py` | Telethon 세션(`AWAKE 전자 공시/awake_session.session`), 매일 23:50 `telegram-daily-mentions` (Claude 가 한 줄 요약 작성) |
| US/KR Market·Awake·Interest | 뷰어 `stock-valuation/view/index.html?src=us|kr|awake|interest` | 각 저장소(`us-market-briefing`, `kr-market-briefing`, `awake-disclosure`, `watchlist-briefing`)의 날짜별 리포트를 ◀선택▶ 로 열고 `skin.css` 주입 |
| KR Screener | `stock-screener/daily.html`, `scripts/daily_screen.py`, `swing_scan.py` | 평일 16:30 `stock-screener-daily` (스크리닝 → 1개월 추세·신고가 스캔). 탭은 전체·1개월 추세·신고가(52주/10일/5일)·즐겨찾기 |
| Dart | `dart-monitoring` | `dart_collect.py` 평일 3회 수집(KR·US·JP), Vercel 프록시는 미배포 |
| ETF Monitor / Index / Calendar / Research | `etf-monitor`, `invest-index`(대시보드 내 네이티브), `investment-calendar`, `investment-notes` | 각자 스케줄 |

## 공통 디자인
- `stock-valuation/theme.css` (토큰·글꼴·배율 1.08·헤더·날짜선택·숫자카드) — 모든 탭 페이지가 링크. 기준 디자인은 Telegram·Report 탭.
- `stock-valuation/skin.css` — 대시보드(`svInjectSkin`)와 뷰어가 같은 출처 iframe 문서에 주입. `html[data-skin=etf|dart|calendar|screener|research|us|kr|awake|interest]` 로 가족별 규칙. 원복은 주입 코드 제거 또는 `localStorage.sv_skin_off='1'`.
- 날짜 선택은 어디서나 `◀ [select] ▶`. 이모지 없는 제목 16~17px/800 + 회색 설명 12px.
- 밸류에이션 8칸(주가·시총·PER/추정·PBR·EPS·배당·외인·52주 위치)은 Telegram·Report·KR Screener·Awake·Stock Valuation 에 공통. 출처는 네이버 증권 비공식 API(`m.stock.naver.com/api/stock/<code>/integration`, 해외 `api.stock.naver.com/stock/<reuters>/basic`). 52주 위치 = (현재가−52주저)/(52주고−52주저).
- Stock Valuation 열: 현재시총 · 현재주가 · 등락률 · 52주 위치 · PER/추정 · PBR · EPS · 배당 · 외인 · 상방 · 하방 · 차트 … (`val_update.py` 가 `stocks[].val` 채움, 06:00/17:00 스케줄 끝에 실행).

## 자주 쓰는 규칙·주의
- 네이버 일별시세 페이지 스크레이핑은 2026-09 부터 안 됨 → `api.finance.naver.com/siseJson.naver` 사용.
- DART OpenAPI 는 병렬 호출 금지(0.3초 간격) — IP 차단 이력.
- bash 한 번 실행 상한 ≈178초, 백그라운드 프로세스는 호출 간 살아남지 않는다. 긴 작업은 스크립트에 체크포인트.
- 스케줄 작업 프롬프트는 자체 완결형(세션 기억 없음). 스크립트 경로는 `/sessions/*/mnt/Claude/...`.
- Stone 탭은 삭제·스케줄 정지(`rock-daily-screening`), 데이터 `rock/` 은 보존. Npay 탭 삭제.
- Awake 채널(`AWAKE - 실시간 주식 공시`)은 텔레그램 수집에서 제외(타 채널 전달분은 포함).

## 미결
- Vercel DART 프록시(사용자가 배포해야 Dart 탭 국내 실시간).
- GitHub 토큰 교체 권장(과거 다른 세션에 평문 노출). `.github_token` 만 바꾸면 모든 스크립트가 따라간다.
- 텔레그램 API ID/해시가 초기 커밋 이력에 남음(세션 파일 없으면 무해). 원하면 my.telegram.org 에서 재발급 후 `.tg_api` 교체.

## 폴더탭(Folder)에 카드 추가 — 다른 세션용 절차 (2026-10-22)
- 스크립트: `stock-valuation/add_folder_card.py` (설명은 파일 상단 docstring)
- 전제: Mac 의 `Claude` 폴더가 세션에 연결되어 있을 것 (토큰 `.github_token` 이 거기 있음). 토큰 값은 절대 출력·복사 금지.
- 폴더 확인: `python3 add_folder_card.py --list`
- 추가: `python3 add_folder_card.py --folder <폴더명> --html <파일.html> [--name "카드 제목"]`
- 폴더가 없으면 `--create` 를 붙이면 주식/관심종목 아래에 생성.
- 결과는 비공개 리포 whysosary-dot/invest-private 의 sv/sheets/ 에 들어가며, 대시보드 새로고침 후 보임.
- 카드 읽기: `python3 add_folder_card.py --get <id>` (sheet.html?open=<id> 링크의 id) / 폴더 안 카드 목록: `--cards <폴더명>`
  (폴더탭 페이지는 브라우저 localStorage 의 토큰으로 비공개 리포를 읽으므로, 다른 세션의 브라우저로 링크를 열면 빈 화면이 정상. 이 스크립트로 읽을 것)

---
## 세션 소스에 없는 레포에 push 하기 (403 해결) — gh_push.py
클라우드 세션의 git 프록시에 레포가 등록돼 있지 않으면 `git push` 가 403 이다. 이 경우 Mac 에 연결된 상태에서
`~/Desktop/Claude/stock-valuation/gh_push.py` 를 device_bash 로 실행하면 Mac 의 `.github_token` 으로 어떤 whysosary-dot 레포에든 커밋할 수 있다.
토큰은 스크립트가 파일에서만 읽는다. 절대 cat 하거나 출력하지 말 것.

```
cd $HOME/mnt/Claude/stock-valuation
python3 -I gh_push.py --repo whysosary-dot/us-market-briefing -m "US market briefing 2026-10-31" \
    --file $HOME/mnt/Claude/<임시폴더>/2026-10-31.md=briefings/2026-10-31.md \
    --file $HOME/mnt/Claude/<임시폴더>/index.html=index.html
python3 -I gh_push.py --repo <owner/name> --get index.html > /tmp/x.html   # 원격 파일 읽기(수정 전 최신본 확보)
python3 -I gh_push.py --repo <owner/name> --ls briefings                  # 원격 목록
```
- 여러 `--file` 은 커밋 하나로 묶인다. `--dir LOCAL=REMOTE_PREFIX` 로 폴더 통째도 가능.
- 클라우드 클론에서 만든 파일은 먼저 Mac 연결 폴더(`~/Desktop/Claude/...`)로 옮긴 뒤(SendUserFile → device_commit_files) 실행.
- 다른 커밋이 먼저 들어오면 ref 갱신 실패 → 그냥 다시 실행.

---
## Mac VM 디스크 가득 참 → 작업 폴더는 `/tmp/<이름>_$(id -un)` 에 (2026-10-30)
device_bash 의 `$HOME`(`/sessions/<세션>/`)이 있는 디스크는 세션 홈 수천 개가 쌓여 100% 찰 수 있다(`mkdir: No space left on device`). 다른 세션 소유라 우리가 지울 수 없다.
반면 `/tmp` 는 다른 디스크라 여유가 있다. 그래서 **스크립트 작업 폴더는 `$HOME` 아래가 아니라 `/tmp/<작업명>_$(id -un)` 로 잡는다.**
- 사용자 이름을 붙이는 이유: `/tmp/tg` 같은 공유 이름은 먼저 만든 다른 세션 사용자 소유라 `unable to open database file` 로 실패한다.
- 텔레그램 예약 작업은 `TG_WORK=/tmp/tgwork_$(id -un)` 으로 이미 변경(2026-10-30). `tg_collect.py` 는 TG_WORK 쓰기 실패 시 `/tmp/tg_<pid>` 로 자동 우회하니, 그 메시지가 나오면 이후 단계도 그 폴더를 쓸 것.
- 다른 예약 작업(`scrwork`, `inv`, `s1029` 등)도 `$HOME` 아래에 쓰고 있다면 같은 식으로 옮길 것. `pip --target` 도 `/tmp/pylibs` 로.
- `/tmp` 역시 세션이 끝나면 지워질 수 있으니, 남겨야 하는 결과물은 `$HOME/mnt/Claude/...`(연결 폴더) 에 두거나 레포에 push.
