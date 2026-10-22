"""휴대폰에서 보기 — Tailscale 을 거친 요청은 켜 둔 동안, 이 PC 주인 계정일 때만 받는다."""

import json
import urllib.error
import urllib.request

from stock_analysis import phone
from stock_analysis.dashboard import start_dashboard
from stock_analysis.ui import settings_page

STATUS = json.dumps({
    "BackendState": "Running",
    "Self": {"DNSName": "my-pc.tail1234.ts.net.", "UserID": 7},
    "User": {"7": {"LoginName": "Me@Example.com"}},
})


def test_status_gives_the_pc_name_and_owner():
    link = phone.parse_status(STATUS)
    assert (link.host, link.owner, link.problem) == ("my-pc.tail1234.ts.net", "me@example.com", "")
    assert link.url == "https://my-pc.tail1234.ts.net/"
    assert "로그인" in phone.parse_status(json.dumps({"BackendState": "NeedsLogin"})).problem
    assert phone.parse_status("not json").problem


def test_share_explains_what_is_missing(monkeypatch):
    monkeypatch.setattr(phone, "find", lambda: "")
    assert "설치" in phone.share(8765).problem

    calls = []

    def run(exe, *args):
        calls.append(args)
        if args[0] == "status":
            return 0, STATUS
        return 1, "Serve is not enabled on your tailnet.\nTo enable, visit:\n\n  https://login.tailscale.com/f/serve?node=abc\n"

    monkeypatch.setattr(phone, "find", lambda: "tailscale")
    monkeypatch.setattr(phone, "_run", run)
    link = phone.share(8765)
    assert link.enable_url == "https://login.tailscale.com/f/serve?node=abc"
    assert not link.url                          # 안 이어졌으면 주소를 보여주지 않는다
    assert ("serve", "--bg", "http://127.0.0.1:8765") in calls


def _status(base, headers, path="/healthz", data=None):
    req = urllib.request.Request(base + path, headers=headers, data=data)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code


def test_phone_requests_only_while_on_and_only_from_the_owner(bot):
    server = start_dashboard(bot, port=8981, open_browser=False, preload=False)
    base = f"http://127.0.0.1:{server.server_address[1]}"
    host = "my-pc.tail1234.ts.net"
    mine = {"Host": host, "Tailscale-User-Login": "me@example.com"}
    try:
        assert _status(base, mine) == 403                         # 꺼져 있으면 휴대폰 요청은 거절
        bot.phone_link = phone.parse_status(STATUS)
        assert _status(base, mine) == 200
        assert _status(base, {"Host": host}) == 403               # 누구인지 모르면 거절
        assert _status(base, {**mine, "Tailscale-User-Login": "other@example.com"}) == 403
        assert _status(base, {"Host": "other.ts.net", "Tailscale-User-Login": "me@example.com"}) == 403
        body = b"action=memo&ticker=ZZZ&memo=x&back=/settings"
        assert _status(base, {**mine, "Origin": f"https://{host}"}, "/action", body) in (200, 303)
        assert _status(base, {**mine, "Origin": "https://evil.example"}, "/action", body) == 403
        assert _status(base, {}) == 200                           # PC 에서는 그대로
        bot.phone_link = phone.Link()
        assert _status(base, mine) == 403                         # 끄면 바로 막힌다
    finally:
        server.shutdown()
        server.server_close()


def test_settings_show_the_steps_and_the_address(bot):
    bot.phone_link = phone.Link()
    off = settings_page.phone_card(bot, "/settings")
    assert "휴대폰 연결 켜기" in off and "tailscale.com/download" in off
    bot.phone_link = phone.parse_status(STATUS)
    on = settings_page.phone_card(bot, "/settings")
    assert "https://my-pc.tail1234.ts.net/" in on and "휴대폰 연결 끄기" in on


def test_funnel_and_tagged_pc_are_refused(bot):
    tagged = json.loads(STATUS)
    tagged["Self"]["Tags"] = ["tag:server"]
    assert "태그" in phone.parse_status(json.dumps(tagged)).problem       # 주인 계정이 없으면 켜지 않는다
    nobody = json.loads(STATUS)
    nobody["User"] = {}
    assert phone.parse_status(json.dumps(nobody)).problem

    server = start_dashboard(bot, port=8981, open_browser=False, preload=False)
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        bot.phone_link = phone.parse_status(STATUS)
        funnel = {"Host": "my-pc.tail1234.ts.net", "Tailscale-Funnel-Request": "?1"}
        assert _status(base, funnel) == 403
        assert _status(base, {**funnel, "Tailscale-User-Login": "me@example.com"}) == 403
    finally:
        server.shutdown()
        server.server_close()


def test_timeout_keeps_what_tailscale_printed(monkeypatch):
    def run(*args, **kw):
        raise phone.subprocess.TimeoutExpired("tailscale", 20, output=b"", stderr=b"visit https://login.tailscale.com/f/serve?node=x\n")

    monkeypatch.setattr(phone.subprocess, "run", run)
    code, out = phone._run("tailscale", "serve")
    assert code == -1 and "https://login.tailscale.com/f/serve?node=x" in out


def test_turning_off_while_turning_on_ends_off(bot, monkeypatch):
    """켜는 중(수십 초)에 끄기를 누르면 끝까지 꺼져 있어야 한다. 다시 잇는 스레드도 마찬가지."""
    import threading

    from stock_analysis.dashboard import Dashboard

    dash = Dashboard(bot)
    dash.port = 8765
    sharing, release, calls = threading.Event(), threading.Event(), []

    def share(port):
        calls.append("share")
        sharing.set()
        release.wait(5)
        return phone.parse_status(STATUS)

    monkeypatch.setattr(phone, "share", share)
    monkeypatch.setattr(phone, "unshare", lambda: calls.append("unshare"))
    dash.run_action("phone_on", {})
    assert sharing.wait(5)
    off = threading.Thread(target=dash.run_action, args=("phone_off", {}))
    off.start()
    release.set()
    off.join(5)
    for _ in range(100):
        if calls[-1:] == ["unshare"]:
            break
        threading.Event().wait(0.02)
    assert not bot.phone_link.url
    assert not bot.overrides.settings("phone").get("enabled")
    assert calls == ["share", "unshare"]

    dash.relink_phone()                          # 꺼 둔 뒤에는 다시 잇지 않는다
    assert calls == ["share", "unshare"] and not bot.phone_link.url


def test_startup_waits_for_tailscale_to_come_up(bot, monkeypatch):
    """PC 를 막 켰을 땐 Tailscale 이 늦게 뜰 수 있다 — 몇 번 더 해보고, 되면 멈춘다."""
    from stock_analysis.dashboard import Dashboard

    dash = Dashboard(bot)
    bot.overrides.set_setting("phone", "enabled", True)
    answers = [phone.Link(problem="Tailscale 이 꺼져 있습니다."), phone.parse_status(STATUS)]
    calls = []
    monkeypatch.setattr(phone, "share", lambda port: calls.append(port) or answers[len(calls) - 1])
    dash.relink_phone(tries=5, wait=0)
    assert len(calls) == 2 and bot.phone_link.url == "https://my-pc.tail1234.ts.net/"
