# Stock Valuation Dashboard

실시간 상대 매력 평가용 로컬 대시보드입니다. 야후 파이낸스(yfinance)로 현재 시총을
원화(억 원)로 받아오고, 목표 시총·안전마진·분기별 매출/영업이익을 입력하면 상방/하방%와
POR을 자동 계산합니다. `stocks.json`에 저장되며 `git` 자동 커밋·푸시 기능이 포함됩니다.

## 사용법

### 1. 최초 1회: 의존성 설치
```bash
pip3 install -r requirements.txt
```

### 2. 서버 실행
```bash
./run.sh
# 또는
python3 server.py
```

브라우저에서 자동으로 http://127.0.0.1:5055 를 여세요.

### 3. 기본 흐름
1. `+ 종목 추가` → 기업명과 **티커** 입력 (예: `005930.KS`, `AAPL`, `NVDA`)
2. `🔄 시총 업데이트` 클릭 → yfinance에서 현재가·주식수 조회 → 원화 환산 후 **억 원** 표시
3. 목표 시총 / 안전마진 입력 → 상방·하방 % 자동 계산
4. 1Q~4Q 매출 / 영업이익 입력 → 영업이익 합계와 POR 자동 계산
5. `💾 저장` 또는 입력하면 0.8초 뒤 자동 저장 (`stocks.json`)
6. `📤 GitHub 푸시` → git add / commit / push 일괄 실행

### 4. 정렬 / 필터
- **컬럼 헤더 클릭** → 오름차순, 다시 클릭 → 내림차순
- 상단 검색창에 **기업명/티커** 입력 → 실시간 필터

## 티커 규칙 (yfinance)
| 시장 | 포맷 | 예 |
|---|---|---|
| KOSPI | `<코드>.KS` | `005930.KS` (삼성전자) |
| KOSDAQ | `<코드>.KQ` | `035420.KQ` |
| 미국 | 티커만 | `AAPL`, `NVDA`, `TSLA` |
| 일본 | `<코드>.T` | `7203.T` |
| 홍콩 | `<코드>.HK` | `0700.HK` |

## 단위
- 모든 금액은 **억 원**(KRW × 1e8) 기준
- 비달러 통화는 yfinance 환율로 자동 환산 (USDKRW·JPYKRW·HKDKRW·CNYKRW)

## 계산식
- 상방 % = (목표 시총 / 현재 시총 − 1) × 100
- 하방 % = (안전마진 / 현재 시총 − 1) × 100
- POR = 현재 시총 / (1Q~4Q **영업이익** 합)

## 알려진 버그 / 주의사항

### ⚠️ 해외 종목 네이버 링크 — `naver_code` 반드시 명시
네이버 금융은 거래소별 접미사가 yfinance 티커와 다르다.
**해외 종목 추가 시 반드시 stocks.json에 `naver_code`를 함께 추가해야 한다.**
없으면 메인 페이지로 이동한다.

| 거래소 | yfinance 티커 | naver_code | 예 |
|--------|-------------|------------|-----|
| 나스닥 | `NVDA` | `NVDA.O` | `.O` 접미사 |
| NYSE | `BRK-B` | `BRK/B.N` | `.N` 접미사 (확인 필요) |
| 일본 TSE | `285A.T` | `285A.T` | `.T` 그대로 |
| 국내 KS/KQ | `005930.KS` | 불필요 | 코드에서 자동 처리 |

```javascript
// index.html 링크 생성 로직
const naverCode = s.naver_code || tickerCode;  // naver_code 없으면 suffix 제거 fallback
const naverUrl = isKorean
  ? `https://m.stock.naver.com/domestic/stock/${tickerCode}/total`
  : `https://m.stock.naver.com/worldstock/stock/${naverCode}/total`;
```

**새 해외 종목 추가 시 체크리스트:**
1. stocks.json에 `"naver_code": "TICKER.O"` (나스닥) 또는 `"TICKER.T"` (일본) 추가
2. `https://m.stock.naver.com/worldstock/stock/{naver_code}/total` 브라우저에서 직접 확인

## 파일 구조
```
stock-valuation/
├── index.html            # UI (테이블, 필터, 정렬, 계산)
├── server.py             # Flask: 데이터 저장 · 시총 업데이트 · git 푸시
├── stocks.json           # 데이터 저장소
├── requirements.txt
├── run.sh
└── README.md
```
