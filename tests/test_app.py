"""오케스트레이션 통합 테스트. 네트워크 대신 가짜 EDGAR 응답을 물린다."""

import json
from pathlib import Path

from conftest import FakeHttp

from stockbot.app import Bot
from stockbot.config import Watch


def test_no_client_talks_to_the_real_network(bot):
    """하위 클라이언트가 하나라도 진짜 http 를 들고 있으면 테스트가 네트워크를 탄다."""
    for client in (bot.edgar, bot.xbrl, bot.prices, bot.estimates, bot.fx):
        assert client.http is bot.http, f"{type(client).__name__} 이 가짜 http 를 쓰지 않습니다"


def test_ticker_resolution(bot):
    target = bot.targets()[0]
    assert target.cik == "0000320193"
    assert target.name == "Apple Inc."


def test_ticker_map_failure_does_not_retry_per_ticker(bot):
    class Broken(FakeHttp):
        def get(self, url, **kwargs):
            self.calls.append(url)
            raise RuntimeError("network down")

    broken = Broken()
    bot.edgar.http = broken
    bot.edgar._ticker_map = None
    bot.config.watchlist.append(Watch(ticker="", cik="1045810"))

    targets = bot.targets()
    # 목록 URL 후보만큼만 시도하고 끝낸다 (종목 수만큼 반복하지 않는다)
    from stockbot.edgar import TICKER_MAP_URLS

    assert len(broken.calls) == len(TICKER_MAP_URLS)
    # cik 를 직접 준 종목은 목록 없이도 살아남는다
    assert [t.cik for t in targets] == ["0001045810"]


def test_first_run_does_not_spam_old_filings(bot):
    assert bot.check_filings() == []
    assert bot.sent == []
    # 기준선이 저장되어 두 번째 실행에서도 조용해야 한다
    assert bot.check_filings() == []
    assert bot.sent == []


def test_force_sends_existing_filings(bot):
    filings = bot.check_filings(force=True)
    assert [f.form for f in filings] == ["4", "8-K"]   # 오래된 것부터
    assert len(bot.sent) == 2
    assert "실적 발표" in bot.sent[1]
    assert "Hong Gildong" in bot.sent[0]               # Form 4 XML까지 파싱됨


def test_new_filing_after_baseline_is_notified(bot, submissions):
    bot.check_filings()                                # 기준선 저장
    recent = submissions["filings"]["recent"]
    recent["accessionNumber"].insert(0, "0000320193-26-000011")
    recent["form"].insert(0, "8-K")
    recent["filingDate"].insert(0, "2026-08-10")
    recent["acceptanceDateTime"].insert(0, "2026-08-10T09:00:00.000Z")
    recent["reportDate"].insert(0, "2026-08-10")
    recent["primaryDocument"].insert(0, "aapl-8k-2.htm")
    recent["items"].insert(0, "5.02")

    new = bot.check_filings()
    assert [f.accession for f in new] == ["0000320193-26-000011"]
    assert "임원·이사 선임/사임" in bot.sent[0]


def test_10q_is_filtered_out_by_forms(bot):
    filings = bot.check_filings(force=True)
    assert all(f.form != "10-Q" for f in filings)


def test_seen_filings_persist_across_restarts(bot, tmp_path):
    bot.check_filings(force=True)
    assert len(bot.sent) == 2

    restarted = Bot(bot.config, dry_run=True)
    restarted.http = bot.http
    restarted.edgar.http = bot.http
    restarted.sent = []
    restarted.notifier.send = lambda text, **kw: (restarted.sent.append(text), True)[1]
    assert restarted.check_filings() == []
    assert restarted.sent == []


def test_failed_send_leaves_filing_unmarked(bot):
    bot.notifier.send = lambda text, **kw: False
    assert bot.check_filings(force=True) == []

    bot.notifier.send = lambda text, **kw: (bot.sent.append(text), True)[1]
    assert len(bot.check_filings()) == 2   # 다음 실행에서 재시도된다


def test_daily_brief_is_sent_once_per_day(bot):
    text = bot.daily_brief()
    assert text and "데일리 브리핑" in text
    assert "다가오는 휴장·조기폐장" in text
    assert "주요 경제지표 일정" in text
    assert bot.daily_brief() is None          # 같은 날 재전송 안 함
    assert bot.daily_brief(force=True)        # 강제는 가능


def test_config_hot_reload(bot, tmp_path):
    """config.yml 을 저장하면 재시작 없이 반영된다."""
    path = tmp_path / "config.yml"
    path.write_text(
        'user_agent: "Tester t@example.com"\n'
        f'state_path: "{tmp_path / "state.json"}"\n'
        f'cache_dir: "{tmp_path / "cache"}"\n'
        f'overrides_path: "{tmp_path / "w.yml"}"\n'
        "watchlist: [AAPL]\n",
        encoding="utf-8",
    )
    bot.config.path = path
    bot._config_mtime = None
    assert bot.reload_config_if_changed()
    assert [w.ticker for w in bot.config.watchlist] == ["AAPL"]

    path.write_text(
        'user_agent: "Tester t@example.com"\n'
        f'state_path: "{tmp_path / "state.json"}"\n'
        f'cache_dir: "{tmp_path / "cache"}"\n'
        f'overrides_path: "{tmp_path / "w.yml"}"\n'
        "poll_interval_sec: 60\n"
        "watchlist: [AAPL, NVDA]\n",
        encoding="utf-8",
    )
    assert bot.reload_config_if_changed()
    assert [w.ticker for w in bot.config.watchlist] == ["AAPL", "NVDA"]
    assert bot.config.poll_interval_sec == 60


def test_broken_config_keeps_previous_settings(bot, tmp_path):
    path = tmp_path / "config.yml"
    path.write_text('user_agent: "T t@example.com"\nwatchlist: [AAPL]\n', encoding="utf-8")
    bot.config.path = path
    bot._config_mtime = None
    bot.reload_config_if_changed()

    path.write_text("watchlist: [\n", encoding="utf-8")   # 깨진 YAML
    assert not bot.reload_config_if_changed()
    assert [w.ticker for w in bot.config.watchlist] == ["AAPL"]


def test_earnings_reminder_is_sent_once(bot, monkeypatch):
    from datetime import date as _date

    from stockbot.earnings import Earnings

    info = Earnings(ticker="AAPL", day=_date(2026, 8, 17), estimated=False, history=[])
    monkeypatch.setattr(bot, "earnings_for", lambda target: info)
    monkeypatch.setattr("stockbot.app.now", lambda tz: __import__("datetime").datetime(2026, 8, 10, 9, 0))

    assert bot.send_earnings_reminders() == ["AAPL D-7"]
    assert "실적 발표가 <b>7일 뒤</b>입니다" in bot.sent[0]
    assert "1) <b>가이던스</b>" in bot.sent[0]

    bot.sent.clear()
    assert bot.send_earnings_reminders() == []      # 같은 날 중복 발송 없음
    assert bot.sent == []


def test_earnings_appear_in_daily_brief(bot, monkeypatch):
    from datetime import date as _date

    from stockbot.earnings import Earnings

    info = Earnings(ticker="AAPL", day=_date(2026, 8, 12), estimated=True, history=[])
    monkeypatch.setattr(bot, "earnings_for", lambda target: info)
    text = bot.daily_brief(force=True)
    assert "관심 종목 실적 발표" in text
    assert "AAPL 실적 발표" in text
    assert "(추정)" in text


def test_state_file_is_written(bot):
    bot.check_filings(force=True)
    saved = json.loads(Path(bot.config.state_path).read_text(encoding="utf-8"))
    assert "0000320193" in saved["seen"]
    assert len(saved["seen"]["0000320193"]) == 2


# --- ETF 종목 ---------------------------------------------------------------
def test_etf_ticker_resolves_from_the_fund_list(bot):
    """ETHU·CONL 처럼 일반 티커 목록에 없는 ETF 도 찾아야 한다."""
    cik, _ = bot.edgar.resolve("CONL")
    assert cik == "0001689873"
    assert bot.edgar.is_fund_ticker("CONL")
    assert not bot.edgar.is_fund_ticker("AAPL")


def test_etf_watches_fund_forms_not_10q(bot, make_config):
    from conftest import FUND_SUBMISSIONS, FakeHttp

    from stockbot.config import Watch

    bot.config.watchlist = [Watch(ticker="CONL")]
    bot._targets = None
    fake = FakeHttp(funds={"0001689873": FUND_SUBMISSIONS})
    bot.http = fake
    for client in (bot.edgar, bot.xbrl, bot.prices, bot.estimates, bot.fx):
        client.http = fake

    target = bot.targets()[0]
    info = bot.fund_for(target)

    assert info is not None
    assert info.leverage == 2.0 and info.single_stock
    assert "497" in bot.forms_for(target)
    assert "10-Q" not in bot.forms_for(target)
    assert target.name == "GraniteShares 2x Long COIN Daily ETF"


def test_etf_metrics_do_not_report_a_failure(bot, make_config):
    """ETF 는 재무제표가 없다. 그렇다고 '불러오기 실패' 로 두면 안 된다."""
    from conftest import FUND_SUBMISSIONS, FakeHttp

    from stockbot.config import Watch

    bot.config.watchlist = [Watch(ticker="CONL")]
    bot._targets = None
    fake = FakeHttp(funds={"0001689873": FUND_SUBMISSIONS})
    bot.http = fake
    for client in (bot.edgar, bot.xbrl, bot.prices, bot.estimates, bot.fx):
        client.http = fake

    done, failed = bot.ensure_all_metrics()
    assert done == 1 and failed == []

    metrics = bot.cached_metrics()[bot.targets()[0].cik]
    assert metrics.is_fund
    assert metrics.revenue_ttm is None      # 없는 숫자를 지어내지 않는다

    verdict = bot.assessment_for(bot.targets()[0])
    assert verdict.level == "poor"          # 2배 레버리지


# --- 가격 알림 --------------------------------------------------------------
def test_price_alert_is_sent_once_per_day(bot):
    from stockbot.metrics import Metrics

    target = bot.targets()[0]
    bot._metrics_cache[target.cik] = Metrics(
        ticker="AAPL", price=60.0, price_change_pct=9.1, high_52w=60.0, low_52w=30.0)

    sent = bot.check_price_alerts()
    assert "AAPL high52" in sent and "AAPL moveup" in sent
    assert "52주 신고가" in bot.sent[0]
    assert "52주 범위 $30.00 ~ $60.00" in bot.sent[0]

    bot.sent.clear()
    assert bot.check_price_alerts() == []      # 같은 날 다시 보내지 않는다
    assert bot.sent == []


def test_price_alert_threshold_is_configurable(bot):
    from stockbot.metrics import Metrics

    target = bot.targets()[0]
    bot._metrics_cache[target.cik] = Metrics(ticker="AAPL", price=50.0, price_change_pct=5.0)
    assert bot.check_price_alerts() == []       # 기본 7% 미만

    bot.config.raw["price_alert_pct"] = 3
    assert bot.check_price_alerts() == ["AAPL moveup"]
