"""설정 — 열쇠 보관함 · 번역 · 업데이트 · 정보 · 종료.

열쇠는 **프로그램 폴더 바깥**에 저장한다. 폴더를 지우고 새로 받아도 남는다.
화면에는 길이와 앞뒤 두 글자만 보여준다 — 화면을 캡처해도 값은 새지 않는다.
"""

from __future__ import annotations

from .kit import action_button, card, esc, page_head

# 화면에서 넣을 수 있는 열쇠. 텔레그램은 쓰지 않아 뺐다(config.yml 에 적어두면 여전히 읽는다).
KEY_FIELDS = (
    ("dart_api_key", "DART 인증키", "한국 종목의 공시·재무제표. 없으면 한국 화면의 재무가 비어 있습니다.",
     "https://opendart.fss.or.kr", "opendart.fss.or.kr — 무료, 1분"),
    ("github_token", "GitHub 토큰", "저장소가 비공개일 때 자동 업데이트에 씁니다. 공개면 필요 없습니다.",
     "https://github.com/settings/tokens", "github.com → Settings → Developer settings"),
)


def render(ctx) -> str:
    bot = ctx.bot
    parts = [page_head("설정", "여기서 바꾼 것은 바로 적용됩니다. 다시 켤 필요가 없습니다."),
             contact_card(bot, ctx.here), keys_card(bot, ctx.here), translate_card(bot, ctx.here),
             phone_card(bot, ctx.here), about_card(bot, ctx.here)]
    return "".join(p for p in parts if p)


def contact_card(bot, here: str) -> str:
    """SEC 연락처. 처음 켤 때 묻지 않고 여기(또는 화면 위쪽 띠)에서 받는다."""
    from ..http import find_email

    email = find_email(getattr(bot.http, "user_agent", "") or "")
    state = (f'<div class="set-state"><span class="tag up">넣었음</span> <span class="muted">{esc(email)}</span></div>'
             if email else '<div class="set-state"><span class="tag">없음 — 미국 공시를 받지 않는 중</span></div>')
    row = (f'<div class="set-row"><div class="sr-name"><b>SEC 연락처 이메일</b>'
           '<span>SEC(미국 공시 서버)가 접속할 때 요구합니다. SEC 에만 전달됩니다.</span>'
           f'{state}</div>'
           '<form method="post" action="/action">'
           '<input type="hidden" name="action" value="contact">'
           f'<input type="hidden" name="back" value="{esc(here)}">'
           '<input class="field" type="email" name="email" placeholder="이메일" autocomplete="email" '
           'aria-label="SEC 연락처 이메일" required>'
           '<button type="submit" class="btn primary">저장</button></form></div>')
    return card(f'<div class="set-list">{row}</div>', "SEC 연락처", id_="contact")


def keys_card(bot, here: str) -> str:
    from .. import secrets

    stored = secrets.load()
    sources = getattr(bot.config, "key_sources", {}) or {}
    rows = []
    for name, label, why, url, where_from in KEY_FIELDS:
        source = sources.get(name) or (str(secrets.path()) if stored.get(name) else "")
        value = stored.get(name, "")
        if source:
            place = "이 화면(폴더 밖)" if source == str(secrets.path()) else source
            shown = f" · {esc(secrets.masked(value))}" if value else ""
            state = f'<div class="set-state"><span class="tag up">넣었음</span> <span class="muted">{esc(place)}{shown}</span></div>'
        else:
            state = '<div class="set-state"><span class="tag">없음</span></div>'
        rows.append(
            f'<div class="set-row"><div class="sr-name"><b>{esc(label)}</b><span>{esc(why)}</span>'
            f'<span>받는 곳: <a href="{esc(url)}" target="_blank" rel="noopener">{esc(where_from)}</a></span>'
            f'{state}</div>'
            '<form method="post" action="/action">'
            '<input type="hidden" name="action" value="key">'
            f'<input type="hidden" name="name" value="{esc(name)}">'
            f'<input type="hidden" name="back" value="{esc(here)}">'
            '<input class="field" type="password" name="value" placeholder="붙여넣기 (비우고 저장하면 지움)" '
            f'autocomplete="off" aria-label="{esc(label)}">'
            '<button type="submit" class="btn primary">저장</button></form></div>')
    note = (f'<div class="card-body"><p class="hint" style="margin-top:0">저장 자리: <code>{esc(str(secrets.path()))}</code> — '
            "프로그램 폴더 바깥이라 폴더를 지워도 남습니다. 열쇠는 이 컴퓨터에만 있고 어디로도 보내지 않습니다. "
            "config.yml 에 적어둔 값이 있으면 그쪽이 먼저입니다. <b>열쇠를 다른 사람(저 포함)에게 보여주지 마세요.</b></p></div>")
    return card(f'<div class="set-list">{"".join(rows)}</div>{note}', "열쇠 보관함", id_="keys")


def translate_card(bot, here: str) -> str:
    """번역 설정. 열쇠를 화면에서 붙여넣게 한다 (config 파일을 안 열어도 되게)."""
    try:
        translator = bot.translator
        settings = bot.translate_settings()
        ready = translator.available()
    except Exception:
        return ""
    from ..translate import PROVIDER_BY_KEY, PROVIDERS

    if not bool(settings.get("enabled", True)):
        now_using = "번역 꺼짐 — 규칙으로 옮긴 한글만 나옵니다"
    elif ready:
        now_using = "지금 쓰는 번역기: " + PROVIDER_BY_KEY[ready[0]].label
        if len(ready) > 1:
            now_using += " (안 되면 " + " → ".join(PROVIDER_BY_KEY[k].label for k in ready[1:]) + " 순서로)"
    else:
        now_using = "쓸 수 있는 번역기가 없습니다"

    fields = {"deepl": "deepl_key", "azure": "azure_key", "papago": "papago_id_key",
              "google_cloud": "google_cloud_key"}
    rows = []
    for provider in PROVIDERS:
        if not provider.needs_key:
            continue
        has_key = bool(str(settings.get(fields[provider.key], "")).strip()) or provider.key in ready
        state = ('<span class="tag up">열쇠 있음</span>' if has_key else '<span class="tag">열쇠 없음</span>')
        rows.append(
            f'<div class="set-row"><div class="sr-name"><b>{esc(provider.label)}</b>'
            f'<span>{esc(provider.note)}</span><div class="set-state">{state}</div></div>'
            '<form method="post" action="/action">'
            '<input type="hidden" name="action" value="translator">'
            f'<input type="hidden" name="provider" value="{esc(provider.key)}">'
            f'<input type="hidden" name="back" value="{esc(here)}">'
            '<input class="field" type="password" name="key" placeholder="열쇠 붙여넣기" autocomplete="off" '
            f'aria-label="{esc(provider.label)} 열쇠">'
            '<button type="submit" class="btn">저장</button></form></div>')
    test = action_button("translate_test", "🧪 번역 시험", here, "btn sm")
    body = (f'<div class="card-body"><p class="line">{esc(now_using)}</p>'
            '<p class="hint" style="margin-top:0">영어 공시를 한글로 옮기는 데 씁니다. <b>아무것도 안 하셔도 됩니다</b> — '
            "열쇠 없이 쓰는 무료 번역이 기본으로 켜져 있습니다. 더 정확한 번역을 원하면 열쇠를 하나 넣으세요. "
            f"넣어둔 것이 실패하면 자동으로 다음 번역기로 넘어갑니다.</p>{test}</div>"
            f'<div class="set-list">{"".join(rows)}</div>')
    return card(body, "번역", id_="translate")


def phone_card(bot, here: str) -> str:
    """휴대폰에서 보기 — Tailscale(무료 사설망)로 내 휴대폰에만 연다. 인터넷에는 열지 않는다."""
    link = getattr(bot, "phone_link", None)
    on = bool(link and link.url)
    if on:
        state = (f'<div class="set-state"><span class="tag up">켜짐</span> '
                 f'<a href="{esc(link.url)}" target="_blank" rel="noopener"><code>{esc(link.url)}</code></a></div>')
        button = action_button("phone_off", "휴대폰 연결 끄기", here, "btn sm")
    else:
        problem = f'<p class="line down">{esc(link.problem)}</p>' if link and link.problem else ""
        if link and link.enable_url:
            problem += (f'<p class="line"><a href="{esc(link.enable_url)}" target="_blank" rel="noopener">'
                        f'{esc(link.enable_url)}</a></p>')
        state = f'<div class="set-state"><span class="tag">꺼짐 — 이 PC 에서만 열림</span></div>{problem}'
        button = action_button("phone_on", "휴대폰 연결 켜기", here, "btn sm primary")
    steps = ("<ol class=\"steps\">"
             "<li><b>PC</b>: <a href=\"https://tailscale.com/download\" target=\"_blank\" rel=\"noopener\">tailscale.com/download</a> "
             "에서 Tailscale 을 설치하고 구글 계정 등으로 로그인 (무료)</li>"
             "<li><b>휴대폰</b>: Play 스토어에서 <b>Tailscale</b> 앱을 받아 <b>같은 계정</b>으로 로그인하고 연결(Connect)</li>"
             "<li>여기서 <b>휴대폰 연결 켜기</b> → 나온 <code>https://…ts.net/</code> 주소를 휴대폰 크롬에서 열기</li>"
             "<li>크롬 메뉴(⋮) → <b>앱 설치</b>(또는 홈 화면에 추가) — 앱 서랍에 First Stock 아이콘이 생깁니다</li></ol>")
    body = (f'<div class="set-list"><div class="set-row"><div class="sr-name"><b>휴대폰에서 보기</b>'
            "<span>이 PC 가 켜져 있고 프로그램이 돌고 있을 때, 내 휴대폰에서 같은 화면을 봅니다.</span>"
            f"{state}</div><div>{button}</div></div></div>"
            f'<div class="card-body">{steps}<p class="hint" style="margin-top:0">인터넷에 공개하지 않습니다. '
            "같은 Tailscale 계정으로 로그인한 내 기기만 닿고, 그중에서도 이 PC 주인 계정만 받습니다. "
            "PC 를 끄거나 절전에 들어가면 휴대폰에서도 안 열립니다(앱은 '꺼져 있음' 안내만 보여줍니다). "
            "텔레그램 알림은 이것과 상관없이 계속 옵니다.</p></div>")
    return card(body, "휴대폰에서 보기", id_="phone")


def about_card(bot, here: str) -> str:
    from .. import __version__

    latest = bot.state.known_latest()
    update = action_button("update", "업데이트 확인·적용", here, "btn sm")
    quit_ = ('<form method="post" action="/action" class="inline-form" '
             "onsubmit=\"return confirm('감시를 완전히 멈춥니다.\\n\\n다시 보려면 시작하기 파일을 더블클릭하세요. 계속할까요?')\">"
             '<input type="hidden" name="action" value="quit">'
             '<button type="submit" class="btn sm danger">⏻ 감시 종료</button></form>')
    logos = bool(bot.config.raw.get("show_logos", True))
    rows = [
        ("버전", f"{esc(__version__)}" + (f" · 새 버전 {esc(latest)} 있음" if latest and latest != __version__ else ""),
         update),
        ("회사 로고", ("켜짐 — 미국 종목 로고를 공개 로고 저장소(parqet)에서 받습니다. 받을 때 그 서버에 티커가 남습니다. "
                    "끄려면 config.yml 에 <code>show_logos: false</code>") if logos else
         "꺼짐 — 글자 배지만 씁니다. 켜려면 config.yml 에 <code>show_logos: true</code>", ""),
        ("마지막 확인", esc(bot.state.last_check() or "아직 없음") + _step_errors(bot), ""),
        ("시간대", esc(bot.config.timezone), ""),
        ("공시 확인 간격", f"{bot.config.poll_interval_sec // 60}분", ""),
        ("주가 갱신", "20초마다 받고, 화면은 5초마다 숫자만 바꿉니다", ""),
        ("종료", "창 없이 뒤에서 도는 프로그램이라, 브라우저를 닫아도 감시는 계속됩니다.", quit_),
    ]
    body = "".join(
        f'<div class="set-row"><div class="sr-name"><b>{esc(k)}</b><span>{v}</span></div>'
        f'<div>{a}</div></div>' for k, v, a in rows)
    sources = ('<div class="card-body"><p class="hint" style="margin-top:0">자료: 재무 = SEC XBRL·DART 사업보고서 원본, '
               "보고서 본문 = 원문 발췌, 주가 = Yahoo Finance(안 되면 Stooq, 지연 시세), 목표가·공매도 = Yahoo Finance 집계, "
               "경제지표 = FRED, 뉴스 = 각 매체 RSS. 이 화면은 내 컴퓨터(127.0.0.1)와, 켰을 때만 내 휴대폰(Tailscale)에서 열립니다. "
               "정보를 모아 보여줄 뿐 매매 신호가 아니며, 투자 판단의 책임은 본인에게 있습니다.</p></div>")
    return card(f'<div class="set-list">{body}</div>{sources}', "프로그램")


def _step_errors(bot) -> str:
    """주기 안에서 실패하고 있는 일. 하나가 실패해도 나머지는 계속 돈다 — 무엇이 멈췄는지만 알린다."""
    errors = getattr(bot, "step_errors", {}) or {}
    if not errors:
        return " · 모든 일이 정상입니다"
    items = "".join(f"<li><b>{esc(name)}</b> ({esc(when)}) {esc(why)}</li>"
                    for name, (when, why) in sorted(errors.items()))
    return ("<br><span class=\"tag down\">실패 중인 일</span> 다른 일은 계속 돕니다. "
            f"자세한 내용은 logs/실행기록.log<ul class=\"small\" style=\"margin:4px 0 0 18px\">{items}</ul>")


__all__ = ["KEY_FIELDS", "render"]
