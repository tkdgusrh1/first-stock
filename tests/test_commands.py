"""텔레그램 명령으로 감시 목록을 실행 중에 고칠 수 있는지 확인한다."""

from datetime import date

import pytest


def test_help_lists_commands(bot):
    reply = bot.commands.handle("/help")
    assert "/add" in reply and "/remove" in reply and "/consensus" in reply


def test_unknown_command(bot):
    reply = bot.commands.handle("/무슨명령")
    assert "모르는 명령" in reply


def test_bot_mention_suffix_is_stripped(bot):
    assert bot.commands.handle("/list@my_stock_bot").startswith("👀")


def test_add_ticker_takes_effect_immediately(bot):
    assert [t.ticker for t in bot.targets()] == ["AAPL"]

    reply = bot.commands.handle("/add NVDA 엔비디아")
    assert "추가했습니다" in reply
    assert [t.ticker for t in bot.targets()] == ["AAPL", "NVDA"]
    # 파일에도 남아서 재시작 후에도 유지된다
    assert "NVDA" in bot.config.overrides_path.read_text(encoding="utf-8")


def test_added_ticker_does_not_replay_old_filings(bot):
    bot.check_filings()                      # 기준선 저장
    bot.commands.handle("/add NVDA")
    bot.sent.clear()
    bot.check_filings()
    assert bot.sent == []                    # 새로 추가해도 과거 공시를 쏟지 않는다


def test_add_rejects_unknown_ticker(bot):
    reply = bot.commands.handle("/add ZZZZ")
    assert "찾지 못했습니다" in reply
    assert "CIK 를 직접" in reply          # 우회 방법을 알려준다
    assert [t.ticker for t in bot.targets()] == ["AAPL"]


def _block_ticker_map(bot):
    """SEC 티커 목록만 막힌 상태를 흉내 낸다 (403 상황)."""

    def blocked(*a, **k):
        raise RuntimeError("SEC가 접속을 거부했습니다 (403)")

    bot.edgar.ticker_map = blocked
    bot.edgar._ticker_map = None


def test_add_with_explicit_cik_skips_sec_lookup(bot):
    """SEC 티커 목록이 막혀도 CIK 를 알면 등록할 수 있어야 한다."""
    _block_ticker_map(bot)

    reply = bot.commands.handle("/add RKLB:1819994 로켓랩")
    assert "추가했습니다" in reply
    target = [t for t in bot.targets() if t.ticker == "RKLB"][0]
    assert target.cik == "0001819994"
    assert target.name == "로켓랩"


def test_add_with_cik_as_separate_word(bot):
    _block_ticker_map(bot)
    assert "추가했습니다" in bot.commands.handle("/add RKLB 1819994")
    assert [t.cik for t in bot.targets() if t.ticker == "RKLB"] == ["0001819994"]


def test_cik_only_watch_survives_blocked_ticker_map(bot):
    """회사명을 못 찾아도 CIK 만으로 감시가 되어야 한다."""
    _block_ticker_map(bot)
    cik, name = bot.edgar.resolve(None, "1819994")
    assert cik == "0001819994"
    assert name == ""


def test_add_duplicate(bot):
    assert "이미 감시 중" in bot.commands.handle("/add AAPL")


def test_remove_ticker(bot):
    bot.commands.handle("/add NVDA")
    reply = bot.commands.handle("/remove NVDA")
    assert "뺐습니다" in reply
    assert [t.ticker for t in bot.targets()] == ["AAPL"]

    assert "감시 목록에 없습니다" in bot.commands.handle("/remove NVDA")


def test_remove_config_ticker(bot):
    assert "뺐습니다" in bot.commands.handle("/remove AAPL")
    assert bot.targets() == []


def test_consensus_is_stored_for_surprise_calculation(bot):
    reply = bot.commands.handle("/consensus AAPL eps=1.01 rev=95,000,000,000")
    assert "컨센서스 저장" in reply
    watch = bot.targets()[0].watch
    assert watch.consensus_eps == 1.01
    assert watch.consensus_revenue == 95_000_000_000


def test_consensus_rejects_bad_number(bot):
    assert "숫자를 읽지 못했습니다" in bot.commands.handle("/consensus AAPL eps=하나")


def test_earnings_date_can_be_set(bot):
    reply = bot.commands.handle("/earnings AAPL 2026-10-29")
    assert "2026-10-29" in reply
    assert bot.targets()[0].watch.earnings_date == date(2026, 10, 29)


def test_earnings_rejects_bad_date(bot):
    assert "YYYY-MM-DD" in bot.commands.handle("/earnings AAPL 10월 29일")


def test_earnings_lookup_uses_filing_history(bot):
    reply = bot.commands.handle("/earnings AAPL")
    # 픽스처엔 8-K 2.02 가 한 건뿐이라 추정이 안 된다 — 그럴 땐 안내가 나가야 한다
    assert "추정하지 못했습니다" in reply


def test_peers_and_forms(bot):
    assert "비교 대상" in bot.commands.handle("/peers AAPL MSFT,GOOGL")
    assert bot.targets()[0].watch.peers == ["MSFT", "GOOGL"]

    assert "감시 폼" in bot.commands.handle("/forms AAPL 8-K,4,10-Q")
    assert bot.targets()[0].watch.forms == ["8-K", "4", "10-Q"]


def test_milestone_accumulates(bot):
    bot.commands.handle("/milestone AAPL 비전프로 판매량 공개")
    reply = bot.commands.handle("/milestone AAPL 인도 매장 확대")
    assert "비전프로 판매량 공개" in reply and "인도 매장 확대" in reply
    assert len(bot.targets()[0].watch.milestones) == 2


def test_commands_reject_unknown_ticker(bot):
    for command in ("/peers ZZZZ AMD", "/consensus ZZZZ eps=1", "/milestone ZZZZ 뭔가", "/earnings ZZZZ"):
        assert "감시 목록에 없습니다" in bot.commands.handle(command)


def test_list_shows_settings(bot):
    bot.commands.handle("/consensus AAPL eps=1.01")
    bot.commands.handle("/earnings AAPL 2026-10-29")
    reply = bot.commands.handle("/list")
    assert "AAPL" in reply
    assert "2026-10-29" in reply
    assert "컨센 EPS 1.01" in reply


def test_status_reports_config(bot):
    reply = bot.commands.handle("/status")
    assert "감시 종목 1개" in reply
    assert "확인 주기 900초" in reply


def test_calendar_command(bot):
    reply = bot.commands.handle("/calendar 30")
    assert "휴장·조기폐장" in reply
    assert "경제지표·실적" in reply


def test_check_command_reports_when_empty(bot):
    bot.check_filings()                       # 기준선
    assert bot.commands.handle("/check") == "새 공시가 없습니다."


def test_brief_command_sends_message(bot):
    assert bot.commands.handle("/brief") is None
    assert any("데일리 브리핑" in text for text in bot.sent)


# --------------------------------------------------------------------------
# 롱폴링 처리
# --------------------------------------------------------------------------
class FakeUpdates:
    def __init__(self, bot, updates):
        self.bot = bot
        self.updates = updates
        self.offsets: list[int | None] = []

    def __call__(self, offset=None, timeout=25):
        self.offsets.append(offset)
        out, self.updates = self.updates, []
        return out


def _update(update_id: int, text: str, chat_id: int = 777):
    return {"update_id": update_id, "message": {"text": text, "chat": {"id": chat_id}}}


def test_poll_handles_messages_and_advances_offset(bot):
    fake = FakeUpdates(bot, [_update(10, "/list"), _update(11, "/status")])
    bot.notifier.get_updates = fake

    assert bot.commands.poll(timeout=0) == 2
    assert bot.state.command_offset() == 12
    assert len(bot.sent) == 2

    # 두 번째 폴링은 방금 처리한 다음 번호부터 요청한다
    bot.commands.poll(timeout=0)
    assert fake.offsets == [None, 12]


def test_poll_ignores_other_chats(bot):
    bot.notifier.get_updates = FakeUpdates(bot, [_update(1, "/list", chat_id=999)])
    assert bot.commands.poll(timeout=0) == 0
    assert bot.sent == []
    assert bot.state.command_offset() == 2     # 무시해도 오프셋은 넘긴다


def test_poll_survives_failing_command(bot, monkeypatch):
    def boom(args):
        raise RuntimeError("터짐")

    monkeypatch.setattr(bot.commands, "cmd_list", boom)
    bot.notifier.get_updates = FakeUpdates(bot, [_update(1, "/list")])
    assert bot.commands.poll(timeout=0) == 1
    assert "오류" in bot.sent[0]


def test_allowed_chat_ids_override(bot):
    bot.config.allowed_chat_ids = ["111", "222"]
    assert bot.config.is_allowed("111")
    assert not bot.config.is_allowed("777")   # 기본 chat_id 도 명시하지 않으면 막힌다


@pytest.mark.parametrize("text", ["", "   ", "\n"])
def test_blank_messages_are_ignored(bot, text):
    bot.notifier.get_updates = FakeUpdates(bot, [_update(1, text)])
    assert bot.commands.poll(timeout=0) == 0
