"""오케스트레이션 통합 테스트. 네트워크 대신 가짜 EDGAR 응답을 물린다."""

import json
from pathlib import Path

from conftest import FakeHttp

from stock_analysis.app import Bot
from stock_analysis.config import Watch


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
    from stock_analysis.edgar import TICKER_MAP_URLS

    assert len(broken.calls) == len(TICKER_MAP_URLS)
    # cik 를 직접 준 종목은 목록 없이도 살아남는다
    assert [t.cik for t in targets] == ["0001045810"]


def test_a_blocked_sec_does_not_hide_my_stocks_forever(bot):
    """SEC 가 잠깐 막혔다고 '감시 중인 종목 없음' 이 굳어버리면 안 된다.

    창 없이 뒤에서 도는 지금은 로그를 안 보면 이유를 알 길이 없어서,
    네트워크가 돌아오면 화면도 스스로 돌아와야 한다.
    """
    class Broken(FakeHttp):
        def get(self, url, **kwargs):
            raise RuntimeError("network down")

    working = bot.edgar.http
    bot.edgar.http = Broken()
    bot.edgar._ticker_map = None
    assert bot.targets() == []
    assert bot.unresolved_tickers() == ["AAPL"]      # 화면에 이유를 적을 수 있다

    bot.edgar.http = working                          # 네트워크 복구
    assert bot.targets() == [], "화면을 그리는 중에 네트워크를 다시 쓰면 안 된다"

    bot.retry_unresolved()                            # 다시 시도는 백그라운드에서만
    assert [t.ticker for t in bot.targets()] == ["AAPL"]
    assert bot.unresolved_tickers() == []


def test_a_good_target_list_is_not_refetched(bot):
    first = bot.targets()
    assert bot.targets() is first        # 다 찾았으면 다시 묻지 않는다


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

    from stock_analysis.earnings import Earnings

    info = Earnings(ticker="AAPL", day=_date(2026, 8, 17), estimated=False, history=[])
    monkeypatch.setattr(bot, "earnings_for", lambda target: info)
    monkeypatch.setattr("stock_analysis.app.now", lambda tz: __import__("datetime").datetime(2026, 8, 10, 9, 0))

    assert bot.send_earnings_reminders() == ["AAPL D-7"]
    assert "실적 발표가 <b>7일 뒤</b>입니다" in bot.sent[0]
    assert "1) <b>가이던스</b>" in bot.sent[0]

    bot.sent.clear()
    assert bot.send_earnings_reminders() == []      # 같은 날 중복 발송 없음
    assert bot.sent == []


def test_earnings_appear_in_daily_brief(bot, monkeypatch):
    from datetime import timedelta

    from stock_analysis.earnings import Earnings
    from stock_analysis.timeutil import now

    # 날짜를 박아두면 그날이 지나는 순간 테스트가 깨진다. 오늘 기준으로 잡는다.
    soon = now(bot.config.timezone).date() + timedelta(days=2)
    info = Earnings(ticker="AAPL", day=soon, estimated=True, history=[])
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

    from stock_analysis.config import Watch

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

    from stock_analysis.config import Watch

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
    from stock_analysis.metrics import Metrics

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
    from stock_analysis.metrics import Metrics

    target = bot.targets()[0]
    bot._metrics_cache[target.cik] = Metrics(ticker="AAPL", price=50.0, price_change_pct=5.0)
    assert bot.check_price_alerts() == []       # 기본 7% 미만

    bot.config.raw["price_alert_pct"] = 3
    assert bot.check_price_alerts() == ["AAPL moveup"]


# --- 알림 노이즈 줄이기 ------------------------------------------------------
def _form4(code, shares=1000, price=40.0):
    from stock_analysis.edgar import Filing

    filing = Filing(cik="0000320193", ticker="AAPL", company="Apple", form="4",
                    accession="0000320193-26-000099", filing_date="2026-08-10",
                    accepted=None, report_date="2026-08-10", primary_doc="")
    filing.transactions = [{"code": code, "shares": shares, "price": price,
                            "value": shares * price, "derivative": False}]
    return filing


def test_only_real_insider_trades_are_alerted(bot):
    """RSU 수령·세금 반납까지 알리면 '자기 돈으로 샀다' 는 신호가 파묻힌다."""
    from stock_analysis.app import _worth_alerting

    assert _worth_alerting(_form4("P"), bot.config)      # 공개시장 매수
    assert _worth_alerting(_form4("S"), bot.config)      # 공개시장 매도
    for code in ("A", "F", "M", "G", "C", "X"):
        assert not _worth_alerting(_form4(code), bot.config), code


def test_unparsed_form4_is_still_alerted(bot):
    """거래 내역을 못 읽었으면 놓치지 않도록 알린다."""
    from stock_analysis.app import _worth_alerting
    from stock_analysis.edgar import Filing

    blank = Filing(cik="1", ticker="X", company="", form="4", accession="a",
                   filing_date="2026-08-10", accepted=None, report_date=None, primary_doc="")
    assert _worth_alerting(blank, bot.config)


def test_all_form4_can_be_turned_back_on(bot):
    from stock_analysis.app import _worth_alerting

    bot.config.raw["insider_alerts"] = "all"
    assert _worth_alerting(_form4("A"), bot.config)


def test_13g_is_not_watched_by_default():
    """대형주는 기관마다 13G 를 올려 수십 건이 된다. 판단에 쓸 내용이 없다."""
    from stock_analysis.config import _DEFAULTS

    assert "SC 13G" not in _DEFAULTS["forms"]
    assert "SC 13D" in _DEFAULTS["forms"]       # 경영 참여 목적은 남긴다


# --- 스스로 갱신 -------------------------------------------------------------
def test_a_new_version_is_installed_without_being_asked(bot, monkeypatch):
    """사람이 버튼을 누르지 않아도 최신으로 돌아야 한다."""
    monkeypatch.setattr("stock_analysis.app.now",
                        lambda tz: __import__("datetime").datetime(2026, 9, 1, 9, 0))
    import updater

    monkeypatch.setattr(updater, "check_latest", lambda *a, **k: ("9.9.9", True))
    monkeypatch.setattr(updater, "auto_update", lambda: (True, "갱신함"))
    restarted = []
    monkeypatch.setattr(bot, "restart", lambda: restarted.append(True))

    latest, done = bot.check_update(force=True)

    assert latest == "9.9.9" and done is True
    assert restarted == [True]
    assert "갱신했습니다" in bot.sent[0]


def test_it_can_be_turned_off(bot, monkeypatch):
    """자동으로 코드가 바뀌는 게 싫으면 끌 수 있어야 한다."""
    bot.config.raw["auto_update"] = False
    import updater

    monkeypatch.setattr(updater, "check_latest", lambda *a, **k: ("9.9.9", True))
    monkeypatch.setattr(updater, "auto_update",
                        lambda: (_ for _ in ()).throw(AssertionError("꺼져 있는데 갱신했습니다")))

    bot.check_update(force=True)
    assert "새 버전" in bot.sent[0]              # 알리기만 한다


def test_a_failed_self_update_does_not_restart(bot, monkeypatch):
    """되돌려진 상태로 다시 켜면 같은 일이 반복된다."""
    import updater

    monkeypatch.setattr(updater, "check_latest", lambda *a, **k: ("9.9.9", True))
    monkeypatch.setattr(updater, "auto_update", lambda: (False, "되돌렸습니다"))
    restarted = []
    monkeypatch.setattr(bot, "restart", lambda: restarted.append(True))

    bot.check_update(force=True)

    assert restarted == []
    assert "자동으로 깔지 못했습니다" in bot.sent[0]


# --- 눈여겨볼 종목 (추천) ---------------------------------------------------
#
# 후보를 훑는 일은 뒤에서 돌아간다. 여기서 틀리면 증상이 '아무 일도 안 남'
# 이거나, 더 나쁘게는 **내가 보는 종목이 뒤로 밀린다.**


def test_the_candidate_pool_includes_my_own_watchlist(bot):
    """내가 보는 종목도 같은 잣대로 줄 세워야 비교가 된다."""
    bot.config.raw["recommend"] = {"extra": ["TSM"], "exclude": ["NVDA"]}
    pool = bot.universe()

    assert "AAPL" in pool          # 감시 목록
    assert "TSM" in pool           # 직접 넣은 것
    assert "NVDA" not in pool      # 뺀 것
    assert len(pool) == len(set(pool))


def test_my_watchlist_is_never_pushed_behind_the_recommendations(bot):
    """추천 때문에 내 종목이 밀리면 주객이 뒤바뀐다.

    후보 하나를 보는 데 재무 원자료 수 MB 를 받는다. 감시 목록이 아직 비어
    있는데 후보부터 훑으면, 정작 내가 보는 화면이 '불러오는 중' 에 남는다.
    """
    assert bot.missing_metrics()           # 아직 안 채워진 상태
    assert bot.screen_step(limit=3) == []


def test_turning_it_off_stops_the_work_entirely(bot):
    bot.config.raw["recommend"] = {"enabled": False}
    assert bot.screen_step(limit=3) == []
    assert bot.top_picks() == []


def test_only_a_few_candidates_are_looked_at_each_round(bot, monkeypatch):
    """한 번에 다 훑으면 감시 주기가 통째로 날아간다."""
    monkeypatch.setattr(bot, "missing_metrics", lambda: [])
    monkeypatch.setattr(bot, "judge_candidate", lambda ticker, keep_facts=False: None)

    looked = bot.screen_step(limit=3)

    assert len(looked) == 3
    assert bot.screen_progress()[0] == 3


def test_the_next_round_moves_on_to_new_candidates(bot, monkeypatch):
    monkeypatch.setattr(bot, "missing_metrics", lambda: [])
    monkeypatch.setattr(bot, "judge_candidate", lambda ticker, keep_facts=False: None)

    first = bot.screen_step(limit=3)
    second = bot.screen_step(limit=3)

    assert not set(first) & set(second)


def test_a_candidate_that_fails_does_not_stop_the_rest(bot, monkeypatch):
    """한 종목의 재무를 못 받았다고 추천 기능 전체가 멈추면 안 된다."""
    monkeypatch.setattr(bot, "missing_metrics", lambda: [])

    def explode(ticker, keep_facts=False):
        raise RuntimeError("SEC 가 막았습니다")

    monkeypatch.setattr(bot, "judge_candidate", explode)

    assert len(bot.screen_step(limit=3)) == 3


def test_picks_say_which_ones_i_am_already_watching(bot, monkeypatch):
    """이미 보고 있는 종목에 '추가' 버튼을 띄우면 안 된다."""
    from stock_analysis.screener import Pick

    monkeypatch.setattr(bot, "missing_metrics", lambda: [])
    bot._picks.remember("AAPL", Pick(ticker="AAPL", score=20.0), "2026-09-02")
    bot._picks.remember("COST", Pick(ticker="COST", score=19.0), "2026-09-02")

    marked = {p.ticker: p.in_watchlist for p in bot.top_picks()}

    assert marked["AAPL"] is True
    assert marked["COST"] is False


def test_the_raw_filings_of_a_candidate_are_not_kept(bot, monkeypatch, tmp_path):
    """후보 250개의 원자료를 남기면 남의 컴퓨터에 1GB 넘게 쌓인다."""
    forgotten = []
    monkeypatch.setattr(bot.xbrl, "forget", lambda cik: forgotten.append(cik))
    monkeypatch.setattr(bot.xbrl, "company_facts", lambda cik, **kw: None)
    monkeypatch.setattr(bot.edgar, "resolve", lambda t, cik=None: ("0000000001", "아무회사"))
    monkeypatch.setattr(bot.edgar, "is_fund_ticker", lambda t: False)
    monkeypatch.setattr(bot, "_submissions_quietly", lambda cik: None)

    bot.judge_candidate("COST")

    assert forgotten == ["0000000001"]


def test_a_watchlist_stock_keeps_its_filings(bot, monkeypatch):
    """내가 보는 종목은 자주 다시 본다. 지웠다 받았다 하면 그게 더 느리다."""
    forgotten = []
    monkeypatch.setattr(bot.xbrl, "forget", lambda cik: forgotten.append(cik))
    monkeypatch.setattr(bot.xbrl, "company_facts", lambda cik, **kw: None)
    monkeypatch.setattr(bot.edgar, "resolve", lambda t, cik=None: ("0000320193", "Apple Inc."))
    monkeypatch.setattr(bot.edgar, "is_fund_ticker", lambda t: False)
    monkeypatch.setattr(bot, "_submissions_quietly", lambda cik: None)

    bot.judge_candidate("AAPL", keep_facts=True)

    assert forgotten == []
