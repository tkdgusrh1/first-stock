# first-stock

관심 종목의 **SEC EDGAR 공시(8-K, Form 4)** 를 주기적으로 확인해서 새 공시가 뜨면 **텔레그램**으로 알려주는 봇입니다.
덤으로 **미국 증시 휴장일**, **주요 경제지표 일정**, 그리고 정리해둔 판단 기준(가이던스 > 어닝 서프라이즈 > 마진 방향)에 맞춘 **재무 지표 체크리스트**를 같이 보내줍니다.

외부 유료 API 없이 **SEC 공식 무료 API**(EDGAR submissions / XBRL companyfacts)와 **Stooq 종가**만 씁니다. API 키가 필요 없습니다.

---

## 1. 빠르게 시작하기

```bash
git clone <이 저장소>
cd first-stock
pip install -r requirements.txt

cp config.example.yml config.yml
# config.yml 에서 user_agent(이메일 포함)와 watchlist 를 수정
```

텔레그램 봇 만들기:

1. 텔레그램에서 [@BotFather](https://t.me/BotFather) 에게 `/newbot` → 토큰을 받습니다.
2. 만든 봇에게 아무 메시지나 한 번 보냅니다. (봇이 먼저 말을 걸 수 없어서 필요합니다)
3. `https://api.telegram.org/bot<토큰>/getUpdates` 를 브라우저로 열어 `chat.id` 를 확인합니다.

```bash
export TELEGRAM_BOT_TOKEN="123456:ABC..."
export TELEGRAM_CHAT_ID="123456789"
export SEC_USER_AGENT="Hong Gildong hong@example.com"   # SEC 필수 요구사항

python main.py test        # 연결 확인 (테스트 메시지 1건 발송)
python main.py run         # 감시 시작
```

> **SEC User-Agent는 선택이 아니라 필수입니다.** 연락 가능한 이메일이 없으면 SEC가 403으로 차단합니다.
> 이 봇은 SEC 권고에 맞춰 초당 8회 이하로만 요청합니다.

---

## 2. 명령어

| 명령 | 설명 |
|---|---|
| `python main.py run` | 주기적으로 공시를 확인하고, 브리핑·실적 리마인더를 보내고, 텔레그램 명령도 받습니다 (상시 실행용) |
| `python main.py check` | 새 공시를 1회만 확인 (cron / GitHub Actions 용) |
| `python main.py brief --force` | 데일리 브리핑을 지금 전송 |
| `python main.py metrics NVDA` | 특정 종목 지표 리포트 (비우면 watchlist 전체) |
| `python main.py earnings` | 실적 발표일 확인 (모르면 과거 발표 간격으로 추정) |
| `python main.py calendar --days 45` | 휴장일·경제지표 일정을 콘솔에 출력 |
| `python main.py update` | `git pull` + 의존성 설치로 봇을 최신 버전으로 갱신 |
| `python main.py test` | 설정·티커 해석·텔레그램 연결 점검 |

공통 옵션: `-c config.yml` (설정 경로), `--dry-run` (텔레그램 대신 콘솔 출력), `-v` (디버그 로그).
`--dry-run` 같은 공통 옵션은 **명령어 앞**에 씁니다: `python main.py --dry-run brief --force`

---

## 2-1. 실행 중에 계속 고치기

`run` 으로 띄워두면 **봇을 끄지 않고** 감시 목록과 판단 기준을 바꿀 수 있습니다.

### 텔레그램에서 바로

봇에게 메시지를 보내면 즉시 반영됩니다. (`/help` 로 전체 목록)

| 명령 | 하는 일 |
|---|---|
| `/list` | 지금 감시 중인 종목과 설정 |
| `/add TSLA 테슬라` | 종목 추가 — 다음 확인부터 바로 감시 |
| `/remove TSLA` | 종목 빼기 |
| `/earnings TSLA 2026-10-22` | 실적 발표일 지정 (날짜 없이 치면 추정치 조회) |
| `/consensus TSLA eps=1.01 rev=25000000000` | 컨센서스 입력 → 어닝 서프라이즈 자동 계산 |
| `/milestone RKLB Neutron 첫 발사` | 적자 기업 체크리스트 ⑤ 마일스톤 추가 |
| `/peers NVDA AMD,AVGO` | PER/PSR 비교 대상 지정 |
| `/forms NVDA 8-K,4,10-Q` | 이 종목만 감시할 폼 |
| `/metrics NVDA` · `/calendar 30` · `/check` · `/brief` · `/status` | 조회·즉시 실행 |

- 명령은 **등록된 대화방에서만** 받습니다 (`telegram_chat_id`, 또는 `allowed_chat_ids`). 다른 방에서 온 명령은 로그만 남기고 무시합니다.
- 변경 내용은 `config.yml` 이 아니라 **`watchlist.local.yml`** 에 저장됩니다. 손으로 적어둔 주석과 서식이 그대로 남고, 재시작해도 유지됩니다.
- 새로 추가한 종목도 **과거 공시를 쏟아내지 않습니다** (기준선만 잡고 그 다음부터 알림).
- 끄고 싶으면 `telegram_commands: false`.

### 파일을 고쳐도 됩니다

`config.yml` 을 저장하면 봇이 **변경을 감지해 재시작 없이 다시 읽습니다.** YAML이 깨져 있으면 이전 설정을 그대로 유지하고 로그로만 알려줍니다.

### 봇 자체 업데이트

```bash
python main.py update                  # git pull + pip install
sudo systemctl restart first-stock     # 상시 실행 중이라면
```

`/status` 로 현재 버전(커밋 해시)과 마지막 확인 시각을 볼 수 있습니다.

---

## 3. 무엇을 알려주나

### 8-K (수시공시)

```
🚨 [8-K] AAPL · Apple Inc.
‼️ 2.02 실적 발표 (매출·이익·가이던스)
• 9.01 재무제표 및 첨부자료
📅 사건일 2026-08-09
🕒 접수 2026-08-10 05:31 (Asia/Seoul)
🔗 원문 · 전체 문서

💡 확인 순서
1) 가이던스 — 다음 분기 회사 전망 + 과거 가이던스 이행 이력, 현금흐름표
2) 어닝 서프라이즈 — 컨센서스 대비 차이
3) 마진 방향 — 영업이익률이 오르는가 내리는가
```

- 8-K 항목 코드(2.02, 5.02 …)를 한글 설명으로 바꿔줍니다.
- 주가에 즉시 영향이 큰 항목(실적발표 2.02, Reg FD 7.01, 재무제표 재작성 4.02, 상장폐지 3.01 등)은 🚨 와 ‼️ 로 구분합니다.
- 접수 시각은 미 동부 시간을 한국 시간으로 변환해서 보여줍니다.

### Form 4 (내부자 거래)

XML 원문을 파싱해서 **누가, 무엇을, 몇 주, 얼마에** 거래했는지까지 보여줍니다.

```
🔴 [Form 4 내부자 거래] NVDA · NVIDIA CORP
👤 Hong Gildong (이사, Chief Executive Officer)
• 공개시장 매도 100,000주 @ $120.50 = $12.05M (2026-08-07) · 거래 후 보유 1,234,567주

➡️ 공개시장 매도 합계 $12.05M — 사전계획(10b5-1) 매도인지 원문에서 확인하세요
```

보상용 RSU 취득(A), 세금 납부용 반납(F), 옵션 행사(M)와 **실제 공개시장 매수(P)·매도(S)** 를 구분해서 표시합니다.

### 데일리 브리핑 (기본 매일 08:00 KST)

- 오늘 미국 증시가 휴장인지 / 조기폐장인지 / 정상 개장인지
- **관심 종목 실적 발표일** (D-day 포함, 맨 위에 따로)
- 다가오는 휴장·조기폐장 일정
- 주요 경제지표 일정
- 관심 종목 지표 한 줄 요약
- FOMC 일정 데이터가 만료돼 가면 갱신하라는 경고

### 실적 발표 리마인더 (D-7 / D-1 / 당일)

메모의 1·2순위(가이던스·어닝 서프라이즈)가 실제로 결정되는 날이라 따로 챙깁니다.

```
🔔 TSLA · Tesla, Inc. 실적 발표가 7일 뒤입니다
📆 2026-10-22(목) (D-7, 추정일)
과거 8-K 2.02 제출 간격으로 추정한 날짜입니다. /earnings 로 확정일을 넣어두면 정확해집니다.

📊 직전 분기 기준
• TSLA · $412.30 · ROE 18.2% · 영업이익률 9.4%↓ · PER 62.1x
컨센서스를 넣어두면 발표 직후 서프라이즈를 자동 계산합니다: /consensus TSLA eps=1.01
❌ 영업이익률 9.4% (전년 동기 11.0%, -1.6%p 악화)

💡 확인 순서
1) 가이던스 …
```

발표일은 `earnings_date` 로 직접 넣거나, 없으면 **과거 8-K 2.02(실적 발표) 제출 간격의 중앙값**으로 추정합니다. 알림 시점은 `earnings_reminder_days: [7, 1, 0]` 로 조절합니다.

---

## 4. 지표 체크리스트 — 정리해둔 기준 그대로

`python main.py metrics NVDA` 를 실행하면 SEC XBRL 재무데이터로 아래를 자동 계산합니다.

### 우선순위

| 순위 | 항목 | 봇이 하는 일 |
|---|---|---|
| 1순위 | **가이던스** | 자동 추출은 불가. 대신 8-K **2.02 / 7.01** 공시가 뜨면 🚨 로 띄우고 "가이던스와 과거 이행 이력, 현금흐름표를 확인하라"는 안내를 붙입니다 |
| 2순위 | **어닝 서프라이즈** | `config.yml` 에 컨센서스(`consensus_eps`, `consensus_revenue`)를 넣어두면 실제 발표치와 비교해 %까지 계산합니다 |
| 3순위 | **마진 방향** | 영업이익률을 전년 동기와 비교해 ↑/↓ 와 %p 변화를 표시합니다 |

가이던스는 회사가 "관리"할 수 있으니(낮게 부르기, 정의 바꾸기, 강조점 옮기기) 봇이 숫자로 단정하지 않고 원문 확인을 유도합니다.

### 흑자 기업 체크리스트

| # | 항목 | 판정 기준 |
|---|---|---|
| ① | 분기 매출 지속 | 최근 4개 분기 매출이 모두 존재/양수인지 + 최근 분기 YoY |
| ② | 자본 효율 | **ROE 15% 이상**, 판정은 부채까지 포함한 **ROIC** 로 (NOPAT ÷ (자기자본 + 총부채 − 현금)) |
| ③ | 영업이익률 방향 | 전년 동기 대비 상승/하락 |
| ④ | PER 위치 | 과거 5년 PER 중앙값 대비 + `peers` 로 지정한 동종업계 중앙값 대비 |
| ⑤ | 영업현금흐름 > 순이익 | 미달이면 "이익은 나는데 현금이 안 들어옴" 경고 |

### 적자 기업 체크리스트

| # | 항목 | 판정 기준 |
|---|---|---|
| ① | 매출 성장률 | **30% 이상**인지 (무조건 판단) |
| ② | 적자 축소 추세 | 적자가 줄고 있는지. **매출↑인데 적자도↑면 "최악의 조합"으로 표시** |
| ③ | 현금 런웨이 | 보유 현금 ÷ 연간 소진액. **2년 미만이면 '금지'** 로 표시 |
| ④ | PSR 위치 | `peers` 동종업계 중앙값 대비 |
| ⑤ | 핵심 마일스톤 | `config.yml` 의 `milestones` 를 매번 같이 띄워 대조하게 함 |

판정은 ✅ 통과 / ⚠️ 주의 / ❌ 미달 / ➖ 데이터 없음 으로 표시됩니다.

**데이터 출처와 한계**
- 재무: SEC XBRL companyfacts (10-Q/10-K 기준, 수정 공시가 있으면 최신본 사용). 10-K만 있는 4분기는 `연간 − 3개 분기`로 역산합니다.
- 주가: Stooq 종가 (실시간 아님)
- 컨센서스는 무료 공개 API가 없어 **직접 입력**해야 합니다. 넣지 않으면 해당 항목은 ➖ 로 표시됩니다.

---

## 5. 설정 (config.yml)

```yaml
user_agent: "Hong Gildong hong@example.com"   # SEC 필수. 환경변수 SEC_USER_AGENT 권장
forms: ["8-K", "4"]          # 감시할 폼. 10-Q, 10-K, SC 13D 등 추가 가능
poll_interval_sec: 900       # 감시 주기(초)
lookback_days: 3             # 재시작 시 며칠 전 공시까지 훑을지
timezone: "Asia/Seoul"
daily_brief_time: "08:00"    # 끄려면 null
econ_min_importance: 2       # 1=자잘한 것까지, 3=최상급만
econ_include_weekly: false   # 매주 목요일 실업수당 청구건수 포함 여부
earnings_reminder_days: [7, 1, 0]   # 실적 발표 며칠 전에 알릴지 (0=당일)

telegram_commands: true      # 텔레그램 명령(/add, /consensus …) 받기
allowed_chat_ids: []         # 비우면 telegram_chat_id 만 허용
overrides_path: "watchlist.local.yml"   # 명령으로 바뀐 내용을 저장할 파일

econ_extra_events:           # 확정된 일정은 여기에 (추정일 대신 이 값이 쓰임)
  - date: 2026-08-12
    name: "소비자물가 CPI (7월)"
    time_et: "08:30"
    importance: 3

watchlist:
  - ticker: AAPL             # 가장 간단한 형태

  - ticker: NVDA
    name: "엔비디아"
    forms: ["8-K", "4", "10-Q"]
    peers: ["AMD", "AVGO"]   # PER/PSR 상대 비교 대상
    consensus_eps: 1.01
    consensus_revenue: 45000000000
    earnings_date: 2026-11-18   # 알면 지정, 없으면 자동 추정

  - ticker: RKLB
    milestones:
      - "Neutron 첫 발사"
```

비밀값은 환경변수가 파일 값보다 우선합니다: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `SEC_USER_AGENT`.

### 챙기는 일정 목록

| 분류 | 항목 |
|---|---|
| 금리·연준 | FOMC 금리 결정(+점도표), **FOMC 의사록**(회의 3주 뒤), **베이지북**(회의 2주 전), **잭슨홀 심포지엄** |
| 물가 | CPI, PPI, **PCE 물가지수** |
| 고용 | 고용보고서(NFP), **ADP 민간고용**, **JOLTS 구인건수**, 주간 실업수당(옵션) |
| 경기·소비 | ISM 제조업/서비스업 PMI, 소매판매, **미시간대 소비자심리**, 분기 GDP |
| 수급 | 쿼드러플 위칭(3·6·9·12월), **월간 옵션 만기**(그 외 달) |
| 실적 | **관심 종목 실적 발표일**, **어닝시즌 개막**(대형은행) |
| 휴장 | NYSE 정규 휴장일 + 조기폐장(13:00 ET) |

`econ_min_importance` 로 노출 수준을 조절합니다 (3=FOMC·CPI·고용·PCE 급만, 1=전부).

### 경제지표 일정에 대한 솔직한 설명

무료·무인증으로 쓸 수 있는 **확정** 경제지표 캘린더 API가 없습니다. 그래서 두 가지를 섞었습니다.

- **확정**: FOMC 일정은 연준 공식 일정을 `data/fomc.yml` 에 넣어뒀습니다 (2025·2026년). 의사록·베이지북은 여기서 관례대로 파생시킵니다.
- **추정**: 나머지는 발표 기관의 관례로 계산합니다 — 고용보고서는 첫째 금요일, ISM 제조업은 첫 영업일, CPI는 중순, PCE는 월말 등. 메시지에 `(관례 기반 추정일)` 이라고 표시됩니다.

날짜가 중요한 지표는 `econ_extra_events` 에 확정일을 넣어주세요. 같은 달 같은 지표의 추정치를 자동으로 대체합니다 (`소비자물가 CPI (7월)` 처럼 괄호를 붙여도 같은 지표로 인식합니다).

**FOMC 일정이 떨어져 가면 봇이 먼저 알려줍니다.** 마지막 회의까지 60일 미만이 남으면 데일리 브리핑과 `/status` 에 "`data/fomc.yml` 에 다음 연도 일정을 추가하세요"라는 경고가 붙습니다. [federalreserve.gov](https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm) 에서 날짜를 복사해 넣고 `python main.py update` 로 받으면 됩니다.

휴장일은 추정이 아니라 **NYSE 규칙 그대로 계산**합니다 (성금요일은 부활절 역산, 토요일 휴일은 금요일 대체, 단 신정이 토요일이면 대체휴장 없음, 준틴스는 2022년부터 등). 조기폐장(13:00 ET)도 포함합니다.

---

## 6. 상시 실행하기

### systemd (라즈베리파이·VPS 등)

```ini
# /etc/systemd/system/first-stock.service
[Unit]
Description=SEC EDGAR 공시 감시 봇
After=network-online.target

[Service]
WorkingDirectory=/home/pi/first-stock
Environment="TELEGRAM_BOT_TOKEN=..."
Environment="TELEGRAM_CHAT_ID=..."
Environment="SEC_USER_AGENT=Hong Gildong hong@example.com"
ExecStart=/usr/bin/python3 main.py run
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now first-stock
```

### cron

```cron
*/15 * * * * cd /home/pi/first-stock && /usr/bin/python3 main.py check >> bot.log 2>&1
0 8 * * *    cd /home/pi/first-stock && /usr/bin/python3 main.py brief >> bot.log 2>&1
```

### GitHub Actions (서버 없이)

`.github/workflows/edgar-bot.yml` 이 이미 들어 있습니다. Secrets 3개(`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `SEC_USER_AGENT`)를 등록하고,
`config.example.yml` 을 `config.public.yml` 로 복사해 **비밀값 없이 watchlist만 남겨** 커밋하면 됩니다.
다만 Actions 스케줄은 러너 사정에 따라 수 분씩 밀릴 수 있어, 속도가 중요하면 상시 실행을 권합니다.

---

## 7. 동작 방식 · 주의사항

- **첫 실행은 조용합니다.** 과거 공시를 한꺼번에 쏟아내지 않도록 기준선만 저장합니다. 과거 것도 받고 싶으면 `python main.py check --force`.
- 알림에 성공한 공시만 `state.json` 에 기록합니다. 전송이 실패하면 다음 실행에서 다시 시도합니다.
- 텔레그램 4096자 제한에 맞춰 긴 메시지는 줄 단위로 나눠 보냅니다.
- 네트워크·파싱 오류가 나도 루프는 죽지 않고 다음 주기에 계속합니다.
- EDGAR는 접수 후 공개까지 보통 몇 분 걸립니다. 실시간 체결 알림이 아닙니다.
- 텔레그램 명령은 **등록된 대화방에서만** 받고, 명령 하나가 실패해도 봇은 계속 돕니다.
- 명령을 켜두면 롱폴링으로 대기하므로 응답이 25초 안에 옵니다. 꺼두면 다음 확인 주기까지 그냥 잠듭니다.
- 실적 발표일 추정은 과거 8-K 2.02 기록이 3회 이상 있어야 하고, 60~120일 간격만 분기 실적으로 인정합니다.

## 8. 테스트

```bash
pip install pytest
python -m pytest -q
```

휴장일 계산, XBRL 파싱(수정공시·4분기 역산 포함), 체크리스트 판정, Form 4 파싱, 메시지 포맷, 중복 알림 방지, 텔레그램 명령 처리, 설정 핫 리로드, 실적일 추정까지 **네트워크 없이** 검증합니다.

---

## 면책

이 봇은 공시·지표를 **모아서 보여줄 뿐** 매매 신호를 주지 않습니다. 계산값은 SEC 공시 원문과 다를 수 있으니 중요한 판단은 반드시 원문을 확인하세요. 투자 판단과 그 결과는 본인 책임입니다.
