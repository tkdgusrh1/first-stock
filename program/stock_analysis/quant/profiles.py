"""투자 성향 — 칸을 하나하나 몰라도 고를 수 있게, 연구에서 나온 원칙으로 묶어 둔 설정.

숫자 자체는 연구가 정해준 값이 아니라 **연구가 가리키는 방향**으로 잡은 출발점이다:
- 거래를 적게 할수록 덜 잃었다(미국 66,465 가구 · 국내 개인 약 20만 명) → 안전형일수록 점검을 드물게.
- 변동성에 맞춰 비중을 줄이면 최대 낙폭이 크게 줄었다(모멘텀 연구, -96.7% → -45.2%) → 안전·균형형은 켬.
- 켈리 공식대로 다 걸면 언젠가 반토막 날 확률이 50% → 한 번에 거는 위험을 계좌의 0.5~2% 로.
- 소액·고회전·레버리지 투자자가 가장 많이 잃었다(국내 개인 약 10만 명) → 공격형도 레버리지·빚은 쓰지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass

from .sizing import RiskRules


@dataclass(frozen=True)
class Profile:
    key: str
    name: str
    summary: str
    rules: RiskRules
    check_days: tuple
    min_hold: int
    emergency: float          # 하루에 이만큼 빠지면 점검일이 아니어도 다음 시가에 판다
    strategy_us: str
    strategy_kr: str
    why: tuple

    def strategy(self, market: str) -> str:
        return self.strategy_kr if market == "kr" else self.strategy_us


PROFILES = (
    Profile(
        "safe", "안전형 — 잃지 않기",
        "8종목까지 나눠 담고, 한 번 틀릴 때 계좌의 0.5% 만 잃게. 금요일 주 1회만 점검, 흔들리면 비중을 줄임",
        RiskRules(risk_per_trade=0.005, max_weight=0.15, max_positions=8, vol_target=True, target_vol=0.20,
                  daily_loss_stop=0.02, dd_half=0.08, dd_stop=0.12),
        (4,), 10, 0.07, "rotation", "rotation",
        ("거래가 적을수록 덜 잃었다(미국 66,465 가구, 국내 약 20만 명)",
         "변동성에 맞춰 비중을 줄이면 최대 낙폭이 절반 가까이 줄었다",
         "ETF 도 담을 수 있는 모멘텀 회전 — 개별 회사의 실적 충격을 덜 받음"),
    ),
    Profile(
        "balanced", "균형형",
        "6종목, 한 번 틀릴 때 1%. 화·금 주 2회 점검, 흔들리면 비중을 줄임",
        RiskRules(risk_per_trade=0.01, max_weight=0.20, max_positions=6, vol_target=True, target_vol=0.30,
                  daily_loss_stop=0.03, dd_half=0.12, dd_stop=0.20),
        (1, 4), 5, 0.10, "growth", "rotation",
        ("켈리 공식의 절반 이하로만 건다 — 추정이 틀려도 버틸 수 있게",
         "성장 + 모멘텀(미국): 실적이 예상을 넘은 뒤 주가가 이어지는 현상과 모멘텀을 합침",
         "가치·모멘텀은 서로 반대로 움직여, 나중에 두 번째 전략을 더하면 덜 흔들림"),
    ),
    Profile(
        "aggressive", "공격형 — 소액 집중",
        "4종목에 집중, 한 번 틀릴 때 2%. 화·금 주 2회 점검. 레버리지·빚은 쓰지 않음",
        RiskRules(risk_per_trade=0.02, max_weight=0.35, max_positions=4, vol_target=False,
                  daily_loss_stop=0.03, dd_half=0.15, dd_stop=0.25),
        (1, 4), 5, 0.12, "growth", "breakout",
        ("공격성은 매매 횟수가 아니라 종목 수를 줄여서 낸다 — 소액·고회전 투자자가 가장 많이 잃었다",
         "한 번의 손실은 2% 로 묶는다 — -50% 를 되찾으려면 +100% 가 필요",
         "고점 대비 -25% 면 멈추고 모의로 돌아가 규칙을 다시 본다"),
    ),
)

BY_KEY = {p.key: p for p in PROFILES}
DEFAULT_PROFILE = "balanced"


def get(key: str) -> Profile | None:
    return BY_KEY.get(key)


# 직접 정하기 칸의 안내 — 칸 이름, 뜻, 어떻게 고르나
FIELD_GUIDE = (
    ("risk_per_trade", "한 번에 잃어도 되는 비율", "손절에 걸렸을 때 계좌에서 빠지는 몫",
     "0.5~2%. 2% 를 넘기면 몇 번 연속 틀릴 때 회복이 어려워짐"),
    ("max_weight", "한 종목 최대 비중", "한 종목에 담을 수 있는 최대 몫",
     "종목 수가 적을수록 크게(4종목이면 25~35%). 갭 하락은 손절로 못 막음"),
    ("max_positions", "최대 보유 종목", "동시에 들고 있을 종목 수",
     "소액은 3~5, 계좌가 커지면 8~15. 너무 많으면 1주도 못 사는 일이 생김"),
    ("daily_loss_stop", "하루 손실 멈춤", "계좌가 하루에 이만큼 빠지면 다음 날 새로 안 삼",
     "2~3%. 0 이면 끔"),
    ("dd_half", "낙폭 → 비중 절반", "고점 대비 이만큼 빠지면 새로 살 때 위험을 절반으로",
     "8~15%. 연속 손실 구간에서 손실 속도를 늦춤"),
    ("dd_stop", "낙폭 → 새 매수 멈춤", "고점 대비 이만큼이면 새로 사지 않고 규칙을 다시 봄",
     "12~25%. 이 선이 오면 모의로 돌아가는 것을 권함"),
    ("vol_target", "변동성이 크면 비중 줄이기", "최근 20일 흔들림이 크면 한 종목 비중 상한을 낮춤",
     "안전·균형형은 켬. 급등락 종목에 크게 들어가는 것을 막음"),
    ("emergency", "긴급 매도(하루 하락)", "점검일이 아니어도 하루에 이만큼 빠진 종목은 다음 시가에 팜",
     "7~12%. 손절선과 따로 작동. 0 이면 끔. 너무 작으면 흔들림에 자주 팔게 됨"),
)
