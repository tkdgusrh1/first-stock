"""주기(5분마다 도는 일)가 앞의 실패 하나 때문에 뒤를 통째로 건너뛰지 않는가.

예전에는 한 주기를 한 줄로 이어 돌려, 공시·재무·추천 후보 중 하나가 실패하면
경제 지표·환율·업데이트 확인·브리핑이 매번 건너뛰어졌다. 기준금리가 며칠째
같은 날짜에 멈춰 있던 원인이다.
"""

from datetime import date, timedelta

from stock_analysis.macro import MacroClient

STEPS = ("refresh_market", "refresh_macro", "check_update", "refresh_prices", "check_filings",
         "check_korean_filings", "ensure_all_metrics", "fill_context", "send_earnings_reminders",
         "quant_step", "check_news", "check_price_alerts", "check_deterioration", "load_dart_codes",
         "refresh_universe", "screen_step")


def _wire(bot, monkeypatch, broken=()):
    called = []
    for name in STEPS:
        def fake(*args, _name=name, **kwargs):
            called.append(_name)
            if _name in broken:
                raise RuntimeError(f"{_name} 망가짐")
            return (0, []) if _name == "ensure_all_metrics" else []
        monkeypatch.setattr(bot, name, fake)
    monkeypatch.setattr(bot, "_brief_due", lambda: False)
    monkeypatch.setattr(bot, "missing_metrics", lambda: [bot])
    return called


def test_a_failing_step_does_not_stop_the_rest(bot, monkeypatch):
    called = _wire(bot, monkeypatch, broken={"check_filings", "ensure_all_metrics", "fill_context"})
    bot._tick()

    for name in ("refresh_macro", "refresh_market", "check_update", "quant_step", "check_news", "screen_step"):
        assert name in called, name
    assert set(bot.step_errors) == {"미국 공시", "재무 지표", "부가 정보"}
    assert "망가짐" in bot.step_errors["미국 공시"][1]
    assert bot.state.last_check()               # 주기 끝까지 갔다


def test_time_sensitive_work_runs_before_the_heavy_work(bot, monkeypatch):
    called = _wire(bot, monkeypatch)
    bot._tick()
    assert called.index("refresh_macro") < called.index("check_filings") < called.index("screen_step")


def test_a_step_that_recovers_clears_its_error(bot, monkeypatch):
    _wire(bot, monkeypatch, broken={"refresh_macro"})
    bot._tick()
    assert "경제 지표" in bot.step_errors
    _wire(bot, monkeypatch)
    bot._tick()
    assert bot.step_errors == {}


def test_errors_in_the_log_do_not_carry_keys(bot, monkeypatch, caplog):
    def leak():
        raise RuntimeError("GET https://opendart.fss.or.kr/api/list.json?crtfc_key=abcdef1234567890 실패")
    monkeypatch.setattr(bot, "check_korean_filings", leak)
    bot._step("한국 공시", bot.check_korean_filings)
    assert "abcdef1234567890" not in caplog.text
    assert "abcdef1234567890" not in str(bot.step_errors)


def test_a_failed_update_check_is_retried_instead_of_waiting_a_day(bot, monkeypatch):
    import updater

    monkeypatch.setattr(updater, "check_latest", lambda: (None, False))
    bot.check_update()
    assert bot.state.last_update_check() != date.today().isoformat()


# --- 경제 지표 ---------------------------------------------------------------
class Http:
    def __init__(self, ok=True):
        self.ok, self.calls = ok, 0

    def get_text(self, url, **kwargs):
        self.calls += 1
        if not self.ok or "DGS10" not in url:
            raise RuntimeError("막힘")
        start = date(2026, 10, 1)
        rows = [f"{(start + timedelta(days=k)).isoformat()},{4.0 + k / 100}" for k in range(10)]
        return "observation_date,DGS10\n" + "\n".join(rows) + "\n"


def test_failed_series_keep_their_last_value_and_the_reason_is_kept(tmp_path):
    client = MacroClient(Http(), tmp_path)
    first = client.refresh(force=True)
    assert first.get("DGS10") is not None
    assert "기준금리" in client.last_error         # 나머지는 못 받았다고 적어 둔다


def test_after_a_failure_it_waits_before_hammering_again(tmp_path):
    http = Http(ok=False)
    client = MacroClient(http, tmp_path, ttl=0)
    client.refresh()
    calls = http.calls
    client.refresh()                               # 바로 다음 주기
    assert http.calls == calls                     # 30분은 쉰다
    client.refresh(force=True)                     # 사람이 누르면 바로
    assert http.calls > calls
