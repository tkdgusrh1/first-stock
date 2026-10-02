"""전략 — 숫자로만 정한 사고팔 규칙.

모든 판단은 **i 번째 날 장이 끝난 뒤, 그 날까지의 봉만 보고** 내린다.
주문은 엔진이 다음 날 시가에 넣는다. 같은 날 종가로 산 셈 치면 미래 정보다.

전략마다 '검증 결과' 와 '권장' 을 같이 적어 둔다. 화면에 그대로 나간다 —
캔들 전략처럼 검증에서 버티지 못한 것도 비교 공부를 위해 넣었고, 그렇다고 분명히 적는다.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import indicators as ind

STOP_ATR = 2.0          # 보호 손절: 진입 신호가 난 날 종가 - 2 × ATR(20)


@dataclass(frozen=True)
class Strategy:
    key: str
    name: str
    kind: str              # "signal"(종목마다 신호) | "rotation"(정해진 날 순위로 교체)
    buy_rule: str
    sell_rule: str
    hold: str
    evidence: str
    advice: str
    warmup: int            # 판단에 필요한 최소 봉 수

    # --- 신호형 -------------------------------------------------------------
    def entry(self, bars, closes, i) -> bool:
        return False

    def exit(self, bars, closes, i, held_days: int) -> str | None:
        return None

    def stop(self, bars, closes, i) -> float | None:
        """보호 손절가. 이 가격 아래로 내려가면 그 자리에서 판다."""
        a = ind.atr(bars, i, 20)
        if a is None:
            return None
        return closes[i] - STOP_ATR * a

    def strength(self, bars, closes, i) -> float:
        """같은 날 신호가 여럿이면 무엇부터 살지. 기본은 6개월 수익률(높은 것부터)."""
        value = ind.change(closes, i, 126)
        return value if value is not None else float("-inf")

    # --- 회전형 (주 1회 갈아타기는 엔진이 정한다) ----------------------------------
    def score(self, bars, closes, i) -> float | None:
        return None

    def keep(self, bars, closes, i) -> bool:
        return True


class Breakout(Strategy):
    def entry(self, bars, closes, i):
        top = ind.highest(closes, i, 55)
        return top is not None and closes[i] > top

    def exit(self, bars, closes, i, held_days):
        low = ind.lowest(closes, i, 20)
        if low is not None and closes[i] < low:
            return "20일 최저 종가 아래로"
        return None


class MaCross(Strategy):
    def entry(self, bars, closes, i):
        fast, slow = ind.sma(closes, i, 20), ind.sma(closes, i, 60)
        pf, ps = ind.sma(closes, i - 1, 20), ind.sma(closes, i - 1, 60)
        return None not in (fast, slow, pf, ps) and pf <= ps and fast > slow

    def exit(self, bars, closes, i, held_days):
        fast, slow = ind.sma(closes, i, 20), ind.sma(closes, i, 60)
        if None not in (fast, slow) and fast < slow:
            return "20일선이 60일선 아래로"
        return None


class Pullback(Strategy):
    def entry(self, bars, closes, i):
        long, r = ind.sma(closes, i, 200), ind.rsi(closes, i, 2)
        return None not in (long, r) and closes[i] > long and r < 10

    def exit(self, bars, closes, i, held_days):
        short = ind.sma(closes, i, 5)
        if short is not None and closes[i] > short:
            return "종가가 5일선 위로"
        if held_days >= 5:
            return "5일 보유 기한"
        return None


class Rotation(Strategy):
    """주 1회, 6개월 수익률 순위로 상위 몇 개를 들고 간다. 200일선 아래 종목은 뺀다."""

    def score(self, bars, closes, i):
        long = ind.sma(closes, i, 200)
        if long is None or closes[i] <= long:
            return None
        return ind.change(closes, i, 126)

    def keep(self, bars, closes, i):
        long = ind.sma(closes, i, 200)
        return long is not None and closes[i] > long


class GrowthRotation(Rotation):
    """성장 + 모멘텀: '그 날 알 수 있었던' 최근 4분기 매출 성장률이 문턱 이상인 종목 중에서만 모멘텀 순위.

    성장 그 자체를 사면 평균적으로 시장보다 못했다(자산 급성장 연구). 그래서 성장은 '자격' 으로만 쓰고,
    순위는 주가가 실제로 따라오는지(6개월 수익률)로 매긴다. 성장이 멈추면(전년 대비 마이너스) 판다.
    """

    needs_growth = True
    MIN_GROWTH = 0.10

    def score(self, bars, closes, i, growth=None):
        base = Rotation.score(self, bars, closes, i)
        if base is None or growth is None or growth < self.MIN_GROWTH:
            return None
        return base

    def keep(self, bars, closes, i, growth=None):
        return Rotation.keep(self, bars, closes, i) and (growth is None or growth >= 0)


STRATEGIES: dict[str, Strategy] = {s.key: s for s in (
    Rotation(
        key="rotation", name="모멘텀 회전 (주 1회)", kind="rotation",
        buy_rule="매주 첫 거래일, 200일선 위에 있는 종목 중 최근 6개월 수익률 상위 N개(N = 최대 보유 수)",
        sell_rule="순위가 2N 밖으로 밀리거나 200일선 아래로 내려가면, 또는 보호 손절",
        hold="2~12주", warmup=200,
        evidence="모멘텀은 미국에서 가장 오래 확인된 현상. 한국은 연구가 엇갈림. 2009년 같은 급반등장에서 크게 깨짐",
        advice="주력 후보. 실제 운용에서는 화면의 '성장 점수'(매출 성장·ROIC)로 후보를 먼저 거르는 것을 권함 — "
               "그 점수는 과거 시점 재무가 없어 백테스트에는 넣지 않았다"),
    GrowthRotation(
        key="growth", name="성장 + 모멘텀 회전 (미국)", kind="rotation",
        buy_rule="매주 첫 점검일, 그 날까지 공시된 최근 4분기 매출이 전년보다 10% 이상 늘었고 200일선 위인 종목 중 "
                 "6개월 수익률 상위 N개",
        sell_rule="순위가 2N 밖으로 밀리거나, 200일선 아래로 가거나, 새 분기 공시로 매출 성장률이 마이너스가 되면, 또는 보호 손절",
        hold="1~3개월", warmup=200,
        evidence="실적 서프라이즈·추정치 상향 뒤 주가가 이어지는 현상(미국, 48분기 중 41분기)과 모멘텀을 합친 것. "
                 "성장만 사는 건 연구에서 시장보다 못했음",
        advice="당신 기준(성장 가능성)을 숫자로 옮긴 주력 후보. SEC 제출일 기준이라 미래 정보 없음 — 미국만 가능"),
    Breakout(
        key="breakout", name="신고가 돌파 (추세추종)", kind="signal",
        buy_rule="종가가 직전 55거래일 최고 종가를 넘으면 다음 날 시가에",
        sell_rule="종가가 직전 20거래일 최저 종가 아래로 내려가면, 또는 보호 손절(신호일 종가 - 2×ATR)",
        hold="2~10주", warmup=60,
        evidence="52주 신고가 근접 효과(미국, 월 0.45%), 자산군 추세추종(58개 모두 플러스). 개별 주식 단기 추세는 판정 보류",
        advice="주력 후보 — 회전형의 진입 시점 고르기로도 쓸 수 있음"),
    MaCross(
        key="ma_cross", name="이동평균 교차 (20/60)", kind="signal",
        buy_rule="20일 이동평균이 60일 이동평균을 아래에서 위로 뚫은 날의 다음 날 시가",
        sell_rule="20일선이 60일선 아래로 내려가면, 또는 보호 손절",
        hold="1~6개월", warmup=61,
        evidence="95개 연구 중 56개 플러스였지만 1990년대 초 이후 약해짐, 자료 뒤지기 문제 많음",
        advice="비교 기준으로만"),
    Pullback(
        key="pullback", name="눌림목 (RSI 2)", kind="signal",
        buy_rule="종가가 200일선 위인데 2일 RSI 가 10 아래로 떨어지면",
        sell_rule="종가가 5일선 위로 올라오거나 5일이 지나면, 또는 보호 손절",
        hold="며칠", warmup=200,
        evidence="대규모 학술 검증 없음. 2026 실험(미심사)에서 오실레이터 계열은 '효과 없음'",
        advice="연구용 — 한국의 단기 반전 현상 때문에 비교해볼 가치는 있음. 실전 보류"),
)}

DEFAULT_STRATEGY = "rotation"


def get(key: str) -> Strategy:
    return STRATEGIES.get(key) or STRATEGIES[DEFAULT_STRATEGY]
