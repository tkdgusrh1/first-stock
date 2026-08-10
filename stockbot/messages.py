"""텔레그램 메시지 조립 (HTML parse_mode)."""

from __future__ import annotations

from datetime import date

from .econ_calendar import EconEvent
from .edgar import CRITICAL_8K_ITEMS, ITEM_8K, Filing
from .market_calendar import MarketDay
from .metrics import STATUS_ICON, Metrics, _money, _pct
from .telegram import esc
from .timeutil import dday, kdate, parse_sec_datetime

IMPORTANCE_MARK = {3: "🔴", 2: "🟠", 1: "🟡"}

# 메모의 우선순위를 알림마다 붙여주는 고정 문구
PRIORITY_HINT = (
    "💡 <b>확인 순서</b>\n"
    "1) <b>가이던스</b> — 다음 분기 회사 전망 + 과거 가이던스 이행 이력, 현금흐름표\n"
    "2) <b>어닝 서프라이즈</b> — 컨센서스 대비 차이\n"
    "3) <b>마진 방향</b> — 영업이익률이 오르는가 내리는가"
)


def format_filing(filing: Filing, tz_name: str) -> str:
    if filing.form == "4":
        return _format_form4(filing, tz_name)
    if filing.form.startswith("8-K"):
        return _format_8k(filing, tz_name)
    return _format_generic(filing, tz_name)


def _header(filing: Filing) -> str:
    company = filing.company or filing.ticker
    return f"<b>{esc(filing.ticker or company)}</b> · {esc(company)}"


def _timestamp(filing: Filing, tz_name: str) -> str:
    accepted = parse_sec_datetime(filing.accepted, tz_name)
    if accepted:
        stamp = accepted.strftime("%Y-%m-%d %H:%M")
        return f"🕒 접수 {stamp} ({tz_name})"
    return f"🕒 제출일 {filing.filing_date}"


def _links(filing: Filing) -> str:
    return f'🔗 <a href="{filing.doc_url}">원문</a> · <a href="{filing.index_url}">전체 문서</a>'


def _format_8k(filing: Filing, tz_name: str) -> str:
    critical = [i for i in filing.items if i in CRITICAL_8K_ITEMS]
    icon = "🚨" if critical else "📄"
    lines = [f"{icon} <b>[8-K]</b> {_header(filing)}"]

    if filing.items:
        for item in filing.items:
            mark = "‼️" if item in CRITICAL_8K_ITEMS else "•"
            lines.append(f"{mark} <b>{esc(item)}</b> {esc(ITEM_8K.get(item, '기타 항목'))}")
    else:
        lines.append("• 항목 코드 없음 (원문 확인 필요)")

    if filing.report_date:
        lines.append(f"📅 사건일 {filing.report_date}")
    lines.append(_timestamp(filing, tz_name))
    lines.append(_links(filing))

    if "2.02" in filing.items or "7.01" in filing.items:
        lines.append("")
        lines.append(PRIORITY_HINT)
    elif critical:
        lines.append("")
        lines.append("💡 주가에 즉시 영향을 줄 수 있는 항목입니다. 원문 먼저 확인하세요.")
    return "\n".join(lines)


def _format_form4(filing: Filing, tz_name: str) -> str:
    buys = [t for t in filing.transactions if t.get("code") == "P"]
    sells = [t for t in filing.transactions if t.get("code") == "S"]
    icon = "🟢" if buys and not sells else ("🔴" if sells and not buys else "👤")

    lines = [f"{icon} <b>[Form 4 내부자 거래]</b> {_header(filing)}"]
    who = filing.insider or "내부자"
    if filing.insider_title:
        who += f" ({filing.insider_title})"
    lines.append(f"👤 {esc(who)}")

    if filing.transactions:
        for tx in filing.transactions[:8]:
            lines.append("• " + esc(_tx_line(tx)))
        if len(filing.transactions) > 8:
            lines.append(f"• … 외 {len(filing.transactions) - 8}건")

        net_buy = sum(t["value"] or 0 for t in buys)
        net_sell = sum(t["value"] or 0 for t in sells)
        if net_buy or net_sell:
            lines.append("")
            if net_buy and not net_sell:
                lines.append(f"➡️ 공개시장 <b>매수</b> 합계 {_money(net_buy)} — 내부자 매수는 통상 긍정 신호")
            elif net_sell and not net_buy:
                lines.append(
                    f"➡️ 공개시장 <b>매도</b> 합계 {_money(net_sell)} "
                    "— 사전계획(10b5-1) 매도인지 원문에서 확인하세요"
                )
            else:
                lines.append(f"➡️ 매수 {_money(net_buy)} / 매도 {_money(net_sell)}")
    else:
        lines.append("• 거래 내역 파싱 실패 — 원문을 확인하세요")

    lines.append(_timestamp(filing, tz_name))
    lines.append(_links(filing))
    return "\n".join(lines)


def _tx_line(tx: dict) -> str:
    parts = [tx.get("code_label") or "거래"]
    if tx.get("shares") is not None:
        parts.append(f"{tx['shares']:,.0f}주")
    if tx.get("price"):
        parts.append(f"@ ${tx['price']:,.2f}")
    if tx.get("value"):
        parts.append(f"= {_money(tx['value'])}")
    if tx.get("date"):
        parts.append(f"({tx['date']})")
    line = " ".join(parts)
    if tx.get("shares_after") is not None:
        line += f" · 거래 후 보유 {tx['shares_after']:,.0f}주"
    if tx.get("derivative"):
        line += " [파생]"
    return line


def _format_generic(filing: Filing, tz_name: str) -> str:
    lines = [f"📄 <b>[{esc(filing.form)}]</b> {_header(filing)}"]
    if filing.report_date:
        lines.append(f"📅 보고 기준일 {filing.report_date}")
    lines.append(_timestamp(filing, tz_name))
    lines.append(_links(filing))
    return "\n".join(lines)


# --------------------------------------------------------------------------
# 데일리 브리핑
# --------------------------------------------------------------------------
def format_earnings_reminder(
    ticker: str,
    company: str | None,
    earnings,
    today: date,
    metrics: Metrics | None = None,
) -> str:
    """실적 발표 D-7 / D-1 / 당일 리마인더."""
    delta = (earnings.day - today).days
    icon = "🔔" if delta > 0 else "📣"
    when = "오늘" if delta == 0 else f"{delta}일 뒤"

    title = f"{icon} <b>{esc(ticker)}</b>"
    if company:
        title += f" · {esc(company)}"
    lines = [f"{title} 실적 발표가 <b>{when}</b>입니다"]
    kind = "추정일" if earnings.estimated else "확정일"
    lines.append(f"📆 {kdate(earnings.day)} ({dday(today, earnings.day)}, {kind})")
    if earnings.estimated:
        lines.append("<i>과거 8-K 2.02 제출 간격으로 추정한 날짜입니다. /earnings 로 확정일을 넣어두면 정확해집니다.</i>")

    if metrics:
        lines.append("")
        lines.append("📊 <b>직전 분기 기준</b>")
        lines.append(_metrics_oneliner(metrics))
        surprise_check = next((c for c in metrics.priority if "어닝 서프라이즈" in c.label), None)
        if surprise_check and surprise_check.status == "na":
            lines.append(
                "<i>컨센서스를 넣어두면 발표 직후 서프라이즈를 자동 계산합니다: "
                f"/consensus {esc(ticker)} eps=1.01</i>"
            )
        margin = next((c for c in metrics.priority if "마진 방향" in c.label), None)
        if margin and margin.status != "na":
            lines.append(f"{STATUS_ICON.get(margin.status, '•')} {esc(margin.detail)}")

    lines.append("")
    lines.append(PRIORITY_HINT)
    return "\n".join(lines)


def format_daily_brief(
    today: date,
    market_days: list[MarketDay],
    econ_events: list[EconEvent],
    metrics: list[Metrics],
    tz_name: str,
    warning: str | None = None,
) -> str:
    lines = [f"🌅 <b>데일리 브리핑</b> · {kdate(today)}", ""]

    todays = [d for d in market_days if d.day == today]
    if todays:
        entry = todays[0]
        lines.append(f"🏛 <b>오늘 미국 증시: {entry.kind}</b> — {esc(entry.name)}")
    elif today.weekday() >= 5:
        lines.append("🏛 <b>오늘 미국 증시: 주말 휴장</b>")
    else:
        lines.append("🏛 오늘 미국 증시: 정상 개장 (한국시간 22:30~05:00, 서머타임 기준)")
    lines.append("")

    upcoming = [d for d in market_days if d.day > today]
    lines.append("📅 <b>다가오는 휴장·조기폐장</b>")
    if upcoming:
        for entry in upcoming[:6]:
            lines.append(f"• {kdate(entry.day)} {esc(entry.name)} — {entry.kind} ({dday(today, entry.day)})")
    else:
        lines.append("• 예정된 일정 없음")
    lines.append("")

    # 관심 종목 실적 발표는 따로 떼서 맨 위에 보여준다 (메모 1·2순위가 결정되는 날)
    earnings_events = [e for e in econ_events if "실적" in e.tags]
    macro_events = [e for e in econ_events if "실적" not in e.tags]

    if earnings_events:
        lines.append("🗣 <b>관심 종목 실적 발표</b>")
        for event in earnings_events:
            line = f"• {kdate(event.day)} {esc(event.name)} ({dday(today, event.day)})"
            if event.estimated:
                line += " <i>(추정)</i>"
            lines.append(line)
        lines.append("")

    lines.append("📊 <b>주요 경제지표 일정</b>")
    if macro_events:
        for event in macro_events[:16]:
            mark = IMPORTANCE_MARK.get(event.importance, "•")
            bits = [f"{mark} {kdate(event.day)}"]
            if event.time_et:
                bits.append(f"{event.time_et} ET")
            bits.append(esc(event.name))
            line = " ".join(bits)
            if event.estimated:
                line += " <i>(관례 기반 추정일)</i>"
            if event.note:
                line += f" — {esc(event.note)}"
            lines.append(line)
    else:
        lines.append("• 예정된 일정 없음")

    if metrics:
        lines.append("")
        lines.append("📈 <b>관심 종목 스냅샷</b>")
        for m in metrics:
            lines.append(_metrics_oneliner(m))

    if warning:
        lines.append("")
        lines.append(f"⚠️ <i>{esc(warning)}</i>")

    return "\n".join(lines)


def _metrics_oneliner(m: Metrics) -> str:
    bits = [f"<b>{esc(m.ticker)}</b>"]
    if m.price:
        bits.append(f"${m.price:,.2f}")
    if m.profitable is False:
        bits.append("적자")
        if m.revenue_growth is not None:
            bits.append(f"매출성장 {m.revenue_growth:+.0%}")
        if m.runway_years is not None:
            bits.append(f"런웨이 {m.runway_years:.1f}년")
        if m.psr:
            bits.append(f"PSR {m.psr:.1f}x")
    else:
        if m.roe is not None:
            bits.append(f"ROE {_pct(m.roe)}")
        if m.roic is not None:
            bits.append(f"ROIC {_pct(m.roic)}")
        if m.op_margin is not None:
            arrow = ""
            if m.op_margin_prior is not None:
                arrow = "↑" if m.op_margin > m.op_margin_prior else ("↓" if m.op_margin < m.op_margin_prior else "→")
            bits.append(f"영업이익률 {_pct(m.op_margin)}{arrow}")
        if m.per:
            bits.append(f"PER {m.per:.1f}x")

    fails = sum(1 for c in m.checks if c.status == "fail")
    if fails:
        bits.append(f"❌{fails}")
    return "• " + " · ".join(bits)


# --------------------------------------------------------------------------
# 종목 지표 리포트
# --------------------------------------------------------------------------
def format_metrics(m: Metrics) -> str:
    title = f"📈 <b>{esc(m.ticker)}</b>"
    if m.company:
        title += f" · {esc(m.company)}"
    lines = [title]

    head = []
    if m.price:
        head.append(f"주가 ${m.price:,.2f}")
    if m.market_cap:
        head.append(f"시총 {_money(m.market_cap)}")
    if m.as_of:
        head.append(f"최신 실적 {m.as_of.isoformat()}")
    if head:
        lines.append("· " + " | ".join(head))

    state = "흑자" if m.profitable else ("적자" if m.profitable is False else "판단 불가")
    lines.append(f"· 상태: <b>{state}</b> (TTM 순이익 {_money(m.net_income_ttm)}, TTM 매출 {_money(m.revenue_ttm)})")

    lines.append("")
    lines.append("🎯 <b>우선순위 판단</b>")
    for check in m.priority:
        lines.append(f"{STATUS_ICON.get(check.status, '•')} <b>{esc(check.label)}</b>: {esc(check.detail)}")

    lines.append("")
    lines.append(f"🧾 <b>{state} 기업 체크리스트</b>")
    for check in m.checks:
        lines.append(f"{STATUS_ICON.get(check.status, '•')} <b>{esc(check.label)}</b>: {esc(check.detail)}")

    if m.peers:
        lines.append("")
        lines.append("🏷 <b>동종업계 비교</b>")
        for ticker, peer in m.peers.items():
            bits = []
            if peer.get("per"):
                bits.append(f"PER {peer['per']:.1f}x")
            if peer.get("psr"):
                bits.append(f"PSR {peer['psr']:.1f}x")
            if peer.get("op_margin") is not None:
                bits.append(f"영업이익률 {_pct(peer['op_margin'])}")
            if peer.get("revenue_growth") is not None:
                bits.append(f"매출성장 {peer['revenue_growth']:+.0%}")
            lines.append(f"• {esc(ticker)}: " + (" · ".join(bits) or "데이터 없음"))

    if m.warnings:
        lines.append("")
        for warning in m.warnings:
            lines.append(f"⚠️ {esc(warning)}")

    lines.append("")
    lines.append("<i>데이터: SEC XBRL(공시 기준) + Stooq 종가. 참고용이며 투자 판단의 책임은 본인에게 있습니다.</i>")
    return "\n".join(lines)
