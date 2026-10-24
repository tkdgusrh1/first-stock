"""검증 — '이 결과가 운이 아닐까' 를 두 가지로 잰다.

1. 시도 횟수를 반영한 샤프(Deflated Sharpe Ratio, Bailey·López de Prado 2014)
   설정을 바꿔 가며 백테스트를 여러 번 돌리면, 그중 가장 좋은 결과는 실력이 없어도 운만으로 좋아 보인다.
   그래서 '지금까지 돌린 횟수만큼 운이 좋았을 때 기대되는 최고 샤프(SR0)' 를 기준선으로 삼아, 이번 결과의
   진짜 샤프가 그 기준선보다 클 확률을 낸다. 수익률 분포가 한쪽으로 치우치거나(왜도) 꼬리가 두꺼우면(첨도)
   같은 샤프라도 덜 믿는다.
       SR0 = √V[SR] · ((1 − γ) Φ⁻¹(1 − 1/N) + γ Φ⁻¹(1 − 1/(N·e)))     γ = 0.5772(오일러-마스케로니 상수)
       DSR = Φ( (SR − SR0) · √(T − 1) / √(1 − 왜도·SR + (첨도 − 1)/4 · SR²) )
   SR 은 하루 단위(연율화하지 않은 값), N 은 이 시장에서 지금까지 돌린 백테스트 수, V 는 그 결과들의 SR 분산.
   시도들이 서로 비슷하면 실제 '독립 시도' 수는 N 보다 적다 — N 을 그대로 쓰는 것은 더 엄격한 쪽이다.

2. 무작위 비교 — 같은 바구니·같은 자격 조건·같은 보유 수·같은 청산 규칙에, 점검일마다 **순위 때문에 판 수와
   새로 살 후보 수를 원래 전략과 같게** 맞추고, 무엇을 팔고 무엇을 살지만 무작위로 골라 여러 번 돌린다.
   내 전략이 그 무작위 결과들의 몇 %보다 나았는지 본다(회전형 전략만). 손절·200일선 이탈·자격 상실처럼
   종목에 따라 일어나는 매도는 고른 종목에 따라 달라지므로 거래 수는 같지 않다 — 화면에 두 쪽을 함께 보인다.
   점검마다 순위를 새로 뽑으면 무작위 쪽은 갈아타기가 훨씬 잦아 거래 비용만큼 부당하게 불리해진다
   (4.13 까지의 방식 — 실제 500종목 백테스트, 모멘텀 회전·$10,000 에서 무작위 쪽 연수익 중간값 −10.5%).
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from functools import cached_property
from statistics import NormalDist

from . import strategies as strat
from .engine import period_of

EULER = 0.5772156649015329
MIN_DAYS = 60            # 이보다 짧으면 확률을 내지 않는다
RANDOM_RUNS = 30


def moments(rets: list[float]) -> dict:
    """하루 수익률 → {sr, skew, kurt(보통 첨도, 정규분포 = 3), n}. 샤프는 연율화하지 않는다."""
    n = len(rets)
    if n < 3:
        return {}
    mean = sum(rets) / n
    sd = math.sqrt(sum((r - mean) ** 2 for r in rets) / (n - 1))
    m2 = sum((r - mean) ** 2 for r in rets) / n
    if sd <= 0 or m2 <= 0:
        return {}
    m3 = sum((r - mean) ** 3 for r in rets) / n
    m4 = sum((r - mean) ** 4 for r in rets) / n
    return {"sr": mean / sd, "skew": m3 / m2 ** 1.5, "kurt": m4 / m2 ** 2, "n": n, "mean": mean, "sd": sd}


def expected_max_sr(trial_srs: list[float]) -> float:
    """실력이 없어도 N 번 시도하면 기대되는 최고 샤프(하루 단위). 시도가 하나뿐이면 0."""
    n = len(trial_srs)
    if n < 2:
        return 0.0
    mean = sum(trial_srs) / n
    sd = math.sqrt(sum((s - mean) ** 2 for s in trial_srs) / (n - 1))
    z = NormalDist()
    return sd * ((1 - EULER) * z.inv_cdf(1 - 1 / n) + EULER * z.inv_cdf(1 - 1 / (n * math.e)))


def deflated_sharpe(daily: dict, trial_srs: list[float]) -> dict | None:
    """{p: 운이 아닐 확률, sr0: 기준선(연율), trials: N}. 자료가 모자라면 None."""
    if not daily or daily.get("n", 0) < MIN_DAYS:
        return None
    sr, skew, kurt, t = daily["sr"], daily["skew"], daily["kurt"], daily["n"]
    sr0 = expected_max_sr(trial_srs)
    denom = 1 - skew * sr + (kurt - 1) / 4 * sr * sr
    if denom <= 0:
        return None
    p = NormalDist().cdf((sr - sr0) * math.sqrt(t - 1) / math.sqrt(denom))
    return {"p": p, "sr0": sr0 * math.sqrt(252), "trials": max(1, len(trial_srs))}


@dataclass(frozen=True)
class RandomRank(strat.Rotation):
    """비교용 — 원래 전략과 같은 자격(200일선 위, 성장 전략은 매출 성장 10% 이상 …)·같은 주기·같은 파는 규칙에,
    점검일마다 **2n위 밖으로 판 수와 새로 살 후보 수를 원래 전략과 같게** 맞춘다. 자격은 원래 전략이 그 날 순위에
    올린 종목 그대로고, 들고 있는 것 중 무엇을 팔지와 후보로 무엇을 올릴지만 무작위로 고른다.

    moves = 원래 전략 백테스트의 rank_moves — ((점검일, 2n위 밖으로 판 수, 후보 수), …).
    """

    seed: int = 0
    base_key: str = strat.DEFAULT_STRATEGY
    moves: tuple = ()
    cross = True

    @cached_property
    def _moves(self) -> dict:
        return {day: (pushed, offered) for day, pushed, offered in self.moves}

    @property
    def base(self) -> strat.Strategy:
        return strat.get(self.base_key)

    @property
    def rebalance(self):
        return self.base.rebalance

    @property
    def needs_growth(self) -> bool:
        return getattr(self.base, "needs_growth", False)

    @property
    def MIN_GROWTH(self) -> float:
        return getattr(self.base, "MIN_GROWTH", 0.0)

    def keep(self, bars, closes, i, **kw):
        return self.base.keep(bars, closes, i, **kw)

    def rank(self, engine, series, day):
        """엔진은 상위 n 개 중 안 든 종목을 후보로 사고, 순위 2n 밖(또는 순위에 없는) 보유 종목을 판다.
        원래 전략의 그 날 결정에 맞춰 줄을 세운다:
            [계속 들 것 일부] + [안 든 종목 무작위] + [계속 들 나머지] + [그날 다른 이유로 파는 중인 것]
            + [나머지 안 든 종목] + [이번에 순위로 팔 것]
        상위 n 에 안 든 종목이 c 개(원래 전략의 후보 수)만 들어가게 계속 들 것으로 앞을 채운다. 계속 들 것이 모자라면
        (빈자리가 더 많으면) 안 든 종목이 그만큼 더 올라온다. 파는 중인 것은 엔진이 빈자리로 세므로 앞에 두지 않는다.
        자격 목록은 원래 전략과 같으므로(같은 날·같은 바구니) 원래 전략이 2n 밖으로 k 개를 팔았다면 목록이
        2n + k 개 이상이다 — 팔 것은 늘 2n 밖에 놓인다."""
        pushed, offered = self._moves.get(day.isoformat(), (0, None))
        rng = random.Random(f"{self.seed}:{period_of(day, self.rebalance)}")
        eligible = sorted(t for _score, t in engine._ranked(self.base, series, day))
        selling = {o.ticker for o in engine.orders if o.side == "sell"}
        fresh = [t for t in eligible if t not in engine.positions]
        rng.shuffle(fresh)

        def sellable(t):          # 엔진이 '순위 밖으로' 팔 보유 종목(파는 중·최소 보유일 전·keep() 실패는 뺀다)
            if t in selling or engine.too_young(t, series):
                return False
            bars, closes, _days, i = series[t]
            return self.base.keep(bars, closes, i, **engine.growth_kw(self.base, t, day))

        held = [t for t in eligible if t in engine.positions]
        can = [t for t in held if sellable(t)]
        drop = set(rng.sample(can, min(len(can), pushed)))
        staying = [t for t in held if t not in drop and t not in selling]
        leaving = [t for t in held if t in selling]
        n = engine.rules.max_positions
        lead = min(len(staying), n - (n if offered is None else min(offered, n)))
        order = staying[:lead] + fresh[:n - lead] + staying[lead:] + leaving + fresh[n - lead:] + sorted(drop)
        return [(float(len(order) - k), t) for k, t in enumerate(order)]


def random_twin(key: str, seed: int, moves: dict | None = None) -> RandomRank:
    """moves = 원래 전략 백테스트 결과의 rank_moves({점검일: (2n위 밖으로 판 수, 후보 수)}).
    없으면 순위로는 팔지 않고 후보는 n 개까지 둔다."""
    base = strat.get(key)
    return RandomRank(key=f"random-{key}", name=f"무작위 순위({base.name})", kind="rotation", buy_rule="",
                      sell_rule="", hold=base.hold, evidence="", advice="", warmup=base.warmup, seed=seed,
                      base_key=key, moves=tuple(sorted((d, int(p), int(c)) for d, (p, c) in (moves or {}).items())))


def compare_random(result: dict, runs: list[dict]) -> dict:
    """내 전략 결과 vs 무작위 결과들 → 화면용 요약. runs = 무작위 백테스트 결과(metrics 만 있으면 된다)."""
    mine = result.get("metrics") or {}

    def values(name):
        return sorted(m.get(name) for m in (r.get("metrics") or {} for r in runs) if m.get(name) is not None)

    def beat(name, higher=True):
        got, value = values(name), mine.get(name)
        if not got or value is None:
            return None
        better = sum(1 for v in got if (value > v if higher else value < v))
        return better / len(got)

    def median(name):
        got = values(name)
        return got[len(got) // 2] if got else None

    return {"runs": len(runs), "beat_cagr": beat("cagr"), "beat_sharpe": beat("sharpe"),
            "beat_mdd": beat("mdd", higher=False), "median_cagr": median("cagr"), "median_mdd": median("mdd"),
            "median_sharpe": median("sharpe"), "trades": mine.get("trades"), "median_trades": median("trades"),
            "matched": True}
