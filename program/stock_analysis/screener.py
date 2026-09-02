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
from .metrics import Metrics, _pct
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


@dataclass
class Pick:
    ticker: str
    name: str = ""
    cik: str = ""
    score: float = 0.0
    level: str = UNKNOWN
    headline: str = ""
    reasons: list[str] = field(default_factory=list)     # 뽑힌 이유 (숫자 포함)
    cautions: list[str] = field(default_factory=list)    # 주의할 점·확인 못 한 것
    in_watchlist: bool = False


# --------------------------------------------------------------------------
# 티커 정리
# --------------------------------------------------------------------------
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
    """회사 하나를 점수로 바꾼다. 추천할 만하지 않으면 None.

    recap 은 실적 3자 대조(실제 · 컨센서스 · 가이던스). 메모의 1·2순위가
    거기 다 들어 있어서, 따로 계산하지 않고 그대로 가져다 쓴다.
    """
    known = [x for x in a.axes if x.level != UNKNOWN]
    if len(known) < MIN_KNOWN_AXES:
        return None
    if a.level == POOR:
        return None

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
    if m.roic is not None and m.roic >= ROIC_TARGET:
        score += 2
        reasons.append(f"ROIC {_pct(m.roic)} — 끌어다 쓴 돈에 비해 잘 벌고 있습니다.")

    # 희석 — 같은 회사를 사고도 내 몫이 줄어든다
    if m.share_growth_1y is not None and m.share_growth_1y > DILUTION_LIMIT:
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
        score=round(score, 2),
        level=a.level,
        headline=a.headline,
        reasons=reasons,
        cautions=cautions,
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


def summary_line(picks: list[Pick], looked_at: int, total: int) -> str:
    """텔레그램·로그에 쓸 한 줄. 몇 개 중에서 골랐는지 반드시 밝힌다."""
    if not picks:
        return f"추천할 만한 종목을 아직 찾지 못했습니다. (후보 {total}개 중 {looked_at}개 확인)"
    names = " · ".join(f"{p.ticker}({p.score:g})" for p in picks)
    return f"{names} — 후보 {total}개 중 {looked_at}개를 본 결과입니다."


def format_pick(pick: Pick) -> str:
    """텔레그램용 여러 줄 설명."""
    lines = [f"{pick.ticker} — {pick.name}"]
    lines += [f"  · {reason}" for reason in pick.reasons[:4]]
    if pick.cautions:
        lines.append(f"  ⚠ {pick.cautions[0]}")
    return "\n".join(lines)




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
    checked: str = ""       # 언제 봤는지 (YYYY-MM-DD)
    pick: Pick | None = None
    error: str = ""         # 못 본 이유 (있으면)


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
            found = item.get("pick")
            self.seen[str(ticker).upper()] = Seen(
                ticker=str(ticker).upper(),
                checked=str(item.get("checked") or ""),
                pick=_pick_from(found),
                error=str(item.get("error") or ""),
            )

    def save(self) -> bool:
        import json
        from dataclasses import asdict

        payload = {
            ticker: {
                "checked": item.checked,
                "pick": asdict(item.pick) if item.pick else None,
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

    def remember(self, ticker: str, pick: Pick | None, today: str, error: str = "") -> None:
        key = ticker.upper()
        self.seen[key] = Seen(ticker=key, checked=today, pick=pick, error=error)

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
        return [item.pick for item in self.seen.values() if item.pick is not None]

    @property
    def looked_at(self) -> int:
        return len(self.seen)


__all__ = [
    "Pick", "PickStore", "Seen", "score_company", "rank", "summary_line",
    "format_pick", "tickers", "excluded_fund", "RECHECK_DAYS",
]
