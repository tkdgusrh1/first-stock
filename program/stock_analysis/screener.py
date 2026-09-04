"""후보 회사를 훑어서 '지금 지표가 괜찮은' 다섯 개를 골라낸다.

**사라는 말이 아니다.** 여기서 하는 일은 공시된 재무제표를 같은 잣대로
줄 세워, 직접 들여다볼 만한 것을 앞으로 끌어오는 것뿐이다. 그래서 뽑힌
이유를 숫자와 함께 항상 같이 보여주고, 확인하지 못한 항목도 숨기지 않는다.

후보 목록은 손으로 적지 않는다. SEC 가 공개한 매출 순위에서 만든다
(universe.py). 무엇을 후보에 넣느냐가 곧 무엇을 추천받느냐라서, 그 결정을
사람 판단에 맡기면 추천 전체가 그 판단을 따라간다.

ETF 는 추천하지 않는다. ETF 를 줄 세우려면 규모나 보수를 알아야 하는데
무료로 공개된 자료에 그게 없다. 근거 없이 "이 ETF 가 낫다" 고 하느니
아예 다루지 않는다. (감시 목록에 넣은 ETF 는 지금까지대로 다 보여준다.)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .assessment import GOOD, POOR, UNKNOWN, Assessment
from .metrics import Metrics, _money, _pct
from .trust import doubts, notes_from
from .recap import MISS
from .recap import UNKNOWN as UNKNOWN_VERDICT

# 후보로 삼지 않는 것. 어느 쪽도 '괜찮은 종목' 으로 권할 물건이 아니다.
#   · 단일 종목 ETF — 회사 하나에 파생을 얹은 상품 (사용자가 직접 빼달라고 했다)
#   · 배수·인버스 — 하루 단위로 되맞추는 단기 매매용
def excluded_fund(info) -> str:
    if info is None:
        return ""
    if getattr(info, "single_stock", False):
        return "단일 종목 ETF"
    if getattr(info, "leverage", None) or getattr(info, "inverse", False):
        return "배수·인버스 ETF"
    return ""


# 추천은 한 줄로 세우지 않는다. 묻는 질문이 다르기 때문이다.
#   · 탄탄한가       — 지금 돈을 잘 벌고 재무가 튼튼한가
#   · 커지고 있는가   — 적자여도 매출이 빠르게 늘고 버틸 돈이 있는가
#   · 시장이 사고 있는가 — 최근 시장보다 더 올랐는가 (지나간 사실이다)
# 한 회사가 여러 갈래에 들어갈 수 있다. 같은 회사를 다른 질문으로 본 것이라
# 그게 오히려 정보다. 갈래를 섞어 한 점수로 견주지는 않는다.
BLUE, GROWTH, MOMENTUM = "blue", "growth", "momentum"

CATEGORY_NAME = {
    BLUE: "탄탄한 회사",
    GROWTH: "성장 가능성",
    MOMENTUM: "시장 흐름",
}

CATEGORY_HOW = {
    BLUE: "흑자에 재무가 튼튼하고, 다섯 축이 고르게 괜찮은 회사입니다",
    GROWTH: "매출이 빠르게 늘고 있는 회사입니다 — 적자여도 들어옵니다",
    MOMENTUM: "최근 시장(S&P 500)보다 더 오른 회사입니다 — 지나간 사실입니다",
}

def category_how(key: str, market: str = "us") -> str:
    """갈래 설명. 시장이 다르면 견주는 지수도, 볼 수 있는 축의 수도 다르다."""
    if market == "kr":
        if key == MOMENTUM:
            return "최근 코스피보다 더 오른 회사입니다 — 지나간 사실입니다"
        if key == BLUE:
            # 한국은 PER·PSR 을 못 구한다. '다섯 축' 이라고 하면 거짓말이 된다.
            return "흑자에 재무가 튼튼하고, 네 축이 고르게 괜찮은 회사입니다"
    return CATEGORY_HOW.get(key, "")


# 갈래마다 반드시 함께 읽어야 하는 경고. 빼먹으면 숫자만 남는다.
CATEGORY_WARNING = {
    BLUE: "탄탄하다는 것은 지금까지의 재무제표 이야기입니다. 앞으로도 그러리라는 보장은 없습니다.",
    GROWTH: "적자 기업은 돈이 떨어지면 증자(주식 추가 발행)로 내 몫이 줄어듭니다. "
            "성장이 꺾이는 순간 주가가 크게 빠지는 것도 이 갈래의 특징입니다.",
    MOMENTUM: "**최근에 올랐다는 사실일 뿐, 앞으로 오른다는 뜻이 전혀 아닙니다.** "
              "많이 오른 뒤에 사는 것은 그만큼 비싸게 사는 것이기도 합니다.",
}


@dataclass
class Pick:
    ticker: str
    name: str = ""
    category: str = BLUE
    score: float = 0.0
    headline: str = ""
    reasons: list[str] = field(default_factory=list)     # 뽑힌 이유 (숫자 포함)
    cautions: list[str] = field(default_factory=list)    # 주의할 점·확인 못 한 것
    notes: list[str] = field(default_factory=list)       # 판단에서 뺀 값 (참고용)
    in_watchlist: bool = False


# --------------------------------------------------------------------------
# 티커 정리
# --------------------------------------------------------------------------
def _span(m: Metrics) -> str:
    """이 숫자가 어느 기간의 것인지. 미국은 최근 4개 분기 합(TTM),
    한국은 사업보고서의 연간 확정치다. 라벨이 사실과 달라선 안 된다."""
    from . import money

    return "연간" if money.is_won(getattr(m, "currency", money.USD)) else "TTM"


def tickers(items) -> list[str]:
    """중복과 빈 값을 걸러 순서를 지킨 목록으로."""
    out: list[str] = []
    for item in items or []:
        ticker = str(item or "").strip().upper()
        if ticker and ticker not in out:
            out.append(ticker)
    return out


# --------------------------------------------------------------------------
# 점수
#
# 메모의 우선순위를 그대로 쓴다: 가이던스 > 어닝 서프라이즈 > 마진 방향.
# ROE 보다 ROIC 를 먼저 본다 (빚을 늘려도 올라가는 ROE 와 달리, ROIC 는
# 끌어다 쓴 돈 전체에 대해 얼마를 벌었는지를 본다).
# --------------------------------------------------------------------------
AXIS_POINT = {GOOD: 2.0, "fair": 1.0, POOR: 0.0}

MIN_KNOWN_AXES = 3          # 이보다 적게 확인됐으면 순위를 매기지 않는다
UNKNOWN_PENALTY = 1.5       # 확인 못 한 축 하나당 깎는 점수
DILUTION_LIMIT = 0.05       # 1년 새 발행주식이 이만큼 늘면 감점
ROIC_TARGET = 0.10          # 이 정도면 끌어다 쓴 돈으로 값어치를 한다고 본다


def score_company(m: Metrics, a: Assessment, recap=None) -> Pick | None:
    """'탄탄한 회사' 갈래의 점수. 추천할 만하지 않으면 None.

    recap 은 실적 3자 대조(실제 · 컨센서스 · 가이던스). 메모의 1·2순위가
    거기 다 들어 있어서, 따로 계산하지 않고 그대로 가져다 쓴다.
    """
    known = [x for x in a.axes if x.level != UNKNOWN]
    if len(known) < MIN_KNOWN_AXES:
        return None
    if a.level == POOR:
        return None

    shaky = doubts(m)
    score = sum(AXIS_POINT[x.level] for x in known) / len(known) * 10
    reasons: list[str] = []
    cautions: list[str] = []

    # 양호한 축은 그 자체가 뽑힌 이유다. 근거 숫자를 같이 옮긴다.
    for axis in a.axes:
        if axis.level == GOOD:
            detail = f" ({axis.evidence[0]})" if axis.evidence else ""
            reasons.append(f"{axis.name}: {axis.headline}{detail}")

    # 가이던스·컨센서스 대조는 **점수에 넣지 않는다.**
    #
    # 메모의 1·2순위이니 넣고 싶지만, 이 둘은 감시 목록에 있는 종목에만
    # 있다 (가이던스는 공시 원문을 통째로 받아 읽어야 나오고, 컨센서스는
    # 직접 넣거나 따로 모아야 한다). 후보 250개에 그걸 다 할 수는 없다.
    # 어떤 종목에는 있고 어떤 종목에는 없는 값으로 가점을 주면, 이미 보고
    # 있던 종목이 그 이유만으로 위로 올라간다. 그건 순위가 아니라 편향이다.
    #
    # 그래서 순위는 모두가 똑같이 가진 값으로만 매기고, 대조 결과는
    # '확인된 추가 정보' 로 옆에 적어만 둔다.
    for line in _recap_lines(recap):
        detail = f"{line.actual_text} (기대 {line.expected_text}) {line.verdict}"
        if line.verdict == MISS:
            cautions.append(f"[참고] {line.label}: {detail}")
        else:
            reasons.append(f"[참고] {line.label}: {detail}")

    # ③ 마진 방향 — 좋아지고 있는가
    if m.op_margin is not None and m.op_margin_prior is not None:
        moved = m.op_margin - m.op_margin_prior
        if moved > 0.005:
            score += 2
            reasons.append(
                f"마진 방향: 영업이익률이 {_pct(m.op_margin_prior)} → {_pct(m.op_margin)} 로 좋아졌습니다."
            )
        elif moved < -0.005:
            score -= 2
            cautions.append(
                f"마진 방향: 영업이익률이 {_pct(m.op_margin_prior)} → {_pct(m.op_margin)} 로 나빠졌습니다."
            )

    # ④ ROIC — 끌어다 쓴 돈으로 값어치를 하는가 (ROE 보다 먼저 본다)
    #    분모가 무너진 값이면 점수에 넣지 않는다. 맞는 숫자로 틀린 판단을
    #    하게 되기 때문이다. 대신 아래 '참고' 에 그대로 남는다.
    if m.roic is not None and m.roic >= ROIC_TARGET and "roic" not in shaky:
        score += 2
        reasons.append(f"ROIC {_pct(m.roic)} — 끌어다 쓴 돈에 비해 잘 벌고 있습니다.")

    # 희석 — 같은 회사를 사고도 내 몫이 줄어든다
    if (m.share_growth_1y is not None and m.share_growth_1y > DILUTION_LIMIT
            and "share_growth_1y" not in shaky):
        score -= 1.5
        cautions.append(f"발행주식수가 1년 새 {_pct(m.share_growth_1y)} 늘었습니다(희석).")

    # 확인 못 한 축은 감점한다. 모르는 것을 좋다고 하면 안 된다.
    missing = [x.name for x in a.axes if x.level == UNKNOWN]
    score -= UNKNOWN_PENALTY * len(missing)
    if missing:
        cautions.append("확인 못 한 항목: " + " · ".join(missing))

    for axis in a.axes:
        if axis.level == POOR:
            cautions.append(f"{axis.name}: {axis.headline}")

    return Pick(
        ticker=m.ticker,
        name=m.company,
        category=BLUE,
        score=round(score, 2),
        headline=a.headline,
        reasons=reasons,
        cautions=cautions,
        notes=notes_from(shaky),
    )


# --------------------------------------------------------------------------
# 성장 가능성 — 적자여도 본다
#
# 이 갈래를 따로 둔 이유가 있다. 다섯 축 판정은 흑자 기업에 유리하게 짜여
# 있어서, 매출이 두 배로 늘고 있어도 적자면 '주의' 로 떨어진다. 그런데
# 그런 회사를 아예 안 보겠다는 것은 판단이 아니라 회피다. 대신 적자
# 기업에 실제로 중요한 것 — 얼마나 빨리 크는가, 버틸 돈이 있는가,
# 손실이 줄고 있는가 — 을 따로 본다.
# --------------------------------------------------------------------------
GROWTH_MIN = 0.20           # 이보다 느리게 크면 '성장' 이라 부르지 않는다
GROWTH_STRONG = 0.40
RUNWAY_MIN = 1.5            # 적자 기업이 버틸 수 있어야 하는 최소 햇수


def score_growth(m: Metrics, a: Assessment) -> Pick | None:
    """매출이 빠르게 늘고 있는 회사. 흑자가 아니어도 된다."""
    growth = m.revenue_growth
    if growth is None or growth < GROWTH_MIN:
        return None
    if not m.revenue_ttm:
        return None                     # 매출 자체가 없으면 성장률은 뜻이 없다

    # 성장률이 이 갈래의 전부다. 그 값을 못 믿겠으면 갈래에 넣지 않는다.
    # 밑이 작아서 나온 +300% 로 '성장주' 라고 부르면 그게 곧 거짓말이다.
    shaky = doubts(m)
    if "revenue_growth" in shaky:
        return None

    score = min(growth, 2.0) * 20       # 성장률이 이 갈래의 본체
    reasons = [f"매출이 1년 새 {_pct(growth)} 늘었습니다. "
               f"({_span(m)} 매출 {_money(m.revenue_ttm, m.currency)}, "
               f"직전 1년 {_money(m.revenue_ttm_prior, m.currency)})"]
    cautions: list[str] = []

    if growth >= GROWTH_STRONG:
        reasons.append("성장 속도가 빠른 편입니다(+40% 이상).")

    # 적자 기업은 '버틸 돈' 이 첫 번째 질문이다
    if m.profitable is False:
        if m.runway_years is None or "runway_years" in shaky:
            score -= 8
            cautions.append("남은 현금으로 몇 년을 버틸 수 있는지 확인하지 못했습니다.")
        elif m.runway_years < RUNWAY_MIN:
            return None                 # 1년 반도 못 버티는 적자 회사는 권하지 않는다
        else:
            score += min(m.runway_years, 5) * 2
            reasons.append(f"적자지만 남은 현금으로 {m.runway_years:.1f}년을 버틸 수 있습니다.")
        cautions.append("아직 적자입니다. 흑자 전환 시점은 아무도 모릅니다.")
    elif m.profitable:
        score += 6
        reasons.append("빠르게 크면서 이미 흑자입니다.")

    # 손실이 줄고 있는가 / 마진이 좋아지고 있는가
    if m.op_margin is not None and m.op_margin_prior is not None:
        moved = m.op_margin - m.op_margin_prior
        if moved > 0.005:
            score += 5
            reasons.append(
                f"영업이익률이 {_pct(m.op_margin_prior)} → {_pct(m.op_margin)} 로 좋아졌습니다."
            )
        elif moved < -0.005:
            score -= 5
            cautions.append(
                f"영업이익률이 {_pct(m.op_margin_prior)} → {_pct(m.op_margin)} 로 나빠졌습니다. "
                "매출은 느는데 남는 게 줄고 있습니다."
            )

    # 희석은 이 갈래에서 특히 무겁다. 적자 기업이 돈을 구하는 방법이기 때문이다.
    if (m.share_growth_1y is not None and m.share_growth_1y > DILUTION_LIMIT
            and "share_growth_1y" not in shaky):
        score -= 6
        cautions.append(f"발행주식수가 1년 새 {_pct(m.share_growth_1y)} 늘었습니다(희석).")

    for axis in a.axes:
        if axis.level == POOR:
            cautions.append(f"{axis.name}: {axis.headline}")

    return Pick(
        ticker=m.ticker, name=m.company, category=GROWTH, score=round(score, 2),
        headline=a.headline, reasons=reasons, cautions=cautions,
        notes=notes_from(shaky),
    )


# --------------------------------------------------------------------------
# 시장 흐름 — 최근에 시장보다 더 올랐는가
#
# 이건 재무제표가 아니라 주가 이야기다. **지나간 값이고, 앞으로를 말해주지
# 않는다.** 그래도 넣는 이유는, 시장이 지금 어디에 돈을 넣고 있는지가
# 재무제표에는 안 나오기 때문이다. 대신 화면에서 그 한계를 매번 밝힌다.
# --------------------------------------------------------------------------
BEAT_MARKET = 5.0           # 시장보다 이만큼(%p) 더 올라야 의미를 둔다


def score_momentum(m: Metrics, a: Assessment, market_3m=None, market_6m=None) -> Pick | None:
    """최근 시장보다 더 오른 회사. market_* 는 같은 기간 S&P 500 수익률(%)."""
    if m.return_3m is None or market_3m is None:
        return None

    lead_3m = m.return_3m - market_3m
    if lead_3m < BEAT_MARKET:
        return None

    score = lead_3m
    reasons = [
        f"최근 3개월 {m.return_3m:+.1f}% — 같은 기간 시장({market_3m:+.1f}%)보다 "
        f"{lead_3m:+.1f}%p 더 올랐습니다."
    ]
    cautions: list[str] = []

    if m.return_6m is not None and market_6m is not None:
        lead_6m = m.return_6m - market_6m
        if lead_6m >= BEAT_MARKET:
            score += lead_6m * 0.5
            reasons.append(
                f"6개월로 넓혀 봐도 {m.return_6m:+.1f}% 로 시장({market_6m:+.1f}%)보다 앞섭니다."
            )
        else:
            cautions.append(
                f"6개월로 보면 {m.return_6m:+.1f}% 로 시장({market_6m:+.1f}%)에 앞서지 못합니다. "
                "최근 3개월에만 오른 것일 수 있습니다."
            )

    if m.pct_from_high is not None and m.pct_from_high > -5:
        cautions.append(f"52주 최고가 부근입니다(최고가 대비 {m.pct_from_high:+.1f}%).")

    # 오른 이유가 재무에 있는지 없는지를 함께 보여준다. 판단은 사용자가 한다.
    if a.level == POOR:
        cautions.append("다만 재무 지표는 '주의' 입니다. 주가만 오른 상태일 수 있습니다.")
    elif a.level == UNKNOWN:
        cautions.append("재무 지표를 확인하지 못했습니다. 주가 움직임만 보고 있는 것입니다.")
    else:
        reasons.append(f"재무 지표 판정도 '{a.label}' 입니다.")

    return Pick(
        ticker=m.ticker, name=m.company, category=MOMENTUM, score=round(score, 2),
        headline=a.headline, reasons=reasons, cautions=cautions,
        notes=notes_from(doubts(m)),
    )


def _recap_lines(recap) -> list:
    """실적 3자 대조에서 판정이 난 줄만. 없으면 빈 목록."""
    return [line for line in getattr(recap, "lines", None) or [] if line.verdict != UNKNOWN_VERDICT]


# --------------------------------------------------------------------------
# 줄 세우기
# --------------------------------------------------------------------------
def rank(picks: list[Pick], limit: int = 5) -> list[Pick]:
    """점수 높은 순. 점수가 같으면 이름순으로 고정한다.

    이름순 고정이 없으면 화면을 새로 그릴 때마다 순서가 흔들려서, 바뀐 게
    없는데도 뭔가 달라진 것처럼 보인다.
    """
    if limit <= 0:
        return []
    return sorted(picks, key=lambda p: (-p.score, p.ticker))[:limit]


def rank_by_category(picks: list[Pick], limit: int = 5) -> dict[str, list[Pick]]:
    """갈래마다 따로 줄 세운다.

    갈래를 섞어 한 점수로 견주지 않는다. '탄탄함 22점' 과 '시장보다 18%p'
    는 단위부터 다른 값이라, 나란히 놓는 순간 없는 뜻이 생긴다.
    """
    return {
        key: rank([p for p in picks if p.category == key], limit)
        for key in (BLUE, GROWTH, MOMENTUM)
    }


# --------------------------------------------------------------------------
# 본 결과를 파일에 남긴다
#
# 후보 하나를 보려면 재무 원자료를 받아야 하고 그게 수 MB 다. 250개를 다
# 보는 데 반나절이 걸린다. 껐다 켤 때마다 처음부터 다시 하면 순위가 영영
# 안 나온다. 그래서 본 것은 파일에 적어둔다.
# --------------------------------------------------------------------------
RECHECK_DAYS = 7            # 이만큼 지난 후보는 다시 본다 (분기 실적이 바뀐다)


@dataclass
class Seen:
    ticker: str
    checked: str = ""             # 언제 봤는지 (YYYY-MM-DD)
    picks: list[Pick] = field(default_factory=list)   # 갈래마다 최대 하나
    error: str = ""               # 못 본 이유 (있으면)


def _pick_from(raw) -> Pick | None:
    """저장해둔 결과를 되읽는다. 모르는 항목은 버린다.

    예전 버전이 남긴 파일에는 지금 없는 항목이 들어 있다 (ETF 추천을
    걷어내면서 is_fund 가 없어졌다). 그걸로 터지면 그동안 훑어둔 것이
    통째로 날아가고, 반나절을 다시 돌려야 한다.
    """
    if not isinstance(raw, dict):
        return None
    fields = Pick.__dataclass_fields__
    return Pick(**{k: v for k, v in raw.items() if k in fields})


class PickStore:
    """후보를 본 결과. 껐다 켜도 남는다."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.seen: dict[str, Seen] = {}
        self.load()

    def load(self) -> None:
        import json

        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for ticker, item in (raw or {}).items():
            if not isinstance(item, dict):
                continue
            # 예전 파일은 갈래가 없던 시절이라 'pick' 하나만 들어 있다
            raw_picks = item.get("picks")
            if raw_picks is None:
                raw_picks = [item.get("pick")] if item.get("pick") else []
            self.seen[str(ticker).upper()] = Seen(
                ticker=str(ticker).upper(),
                checked=str(item.get("checked") or ""),
                picks=[p for p in (_pick_from(x) for x in raw_picks) if p],
                error=str(item.get("error") or ""),
            )

    def save(self) -> bool:
        import json
        from dataclasses import asdict

        payload = {
            ticker: {
                "checked": item.checked,
                "picks": [asdict(p) for p in item.picks],
                "error": item.error,
            }
            for ticker, item in self.seen.items()
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            return True
        except OSError:
            return False

    def remember(self, ticker: str, picks, today: str, error: str = "") -> None:
        """한 후보를 본 결과. picks 는 갈래별 판정 목록 (하나만 줘도 된다)."""
        key = ticker.upper()
        if picks is None:
            found = []
        elif isinstance(picks, Pick):
            found = [picks]
        else:
            found = [p for p in picks if p]
        self.seen[key] = Seen(ticker=key, checked=today, picks=found, error=error)

    def forget_missing(self, universe: list[str]) -> None:
        """후보에서 빠진 종목은 결과도 지운다. 없는 것을 추천하면 안 된다."""
        live = {t.upper() for t in universe}
        for ticker in [t for t in self.seen if t not in live]:
            self.seen.pop(ticker, None)

    def stale(self, universe: list[str], today: str, days: int = RECHECK_DAYS) -> list[str]:
        """아직 안 봤거나 오래된 후보. 안 본 것을 먼저 돌려준다."""
        from datetime import date

        fresh, old = [], []
        for ticker in universe:
            item = self.seen.get(ticker.upper())
            if item is None:
                fresh.append(ticker)
                continue
            try:
                age = (date.fromisoformat(today) - date.fromisoformat(item.checked)).days
            except ValueError:
                age = days + 1
            if age >= days:
                old.append(ticker)
        return fresh + old

    def picks(self) -> list[Pick]:
        return [pick for item in self.seen.values() for pick in item.picks]

    @property
    def looked_at(self) -> int:
        return len(self.seen)


__all__ = [
    "Pick", "PickStore", "Seen", "rank", "rank_by_category",
    "tickers", "excluded_fund", "RECHECK_DAYS",
    "score_company", "score_growth", "score_momentum",
    "BLUE", "GROWTH", "MOMENTUM", "CATEGORY_NAME", "CATEGORY_HOW", "CATEGORY_WARNING",
]
