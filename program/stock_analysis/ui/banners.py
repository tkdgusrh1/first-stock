"""쪽 맨 위 띠 — 새 버전 알림, DART 인증키 요청."""

from __future__ import annotations

from .kit import action_button, esc


def _as_tuple(value) -> tuple:
    return tuple(int(p) if str(p).isdigit() else 0 for p in str(value).split("."))


def update_banner(latest, here: str) -> str:
    from .. import __version__

    if not latest or _as_tuple(latest) <= _as_tuple(__version__):
        return ""
    button = action_button("update", "지금 업데이트", here, "btn sm primary")
    return (f'<div class="banner"><div class="b-text">🆕 <b>새 버전 {esc(latest)}</b> 이 나왔습니다 '
            f'(지금 {esc(__version__)}). 설정과 기록, 열쇠는 그대로 남습니다.</div>{button}</div>')


def _key_form(here: str, placeholder: str) -> str:
    return ('<form method="post" action="/action">'
            '<input type="hidden" name="action" value="key">'
            '<input type="hidden" name="name" value="dart_api_key">'
            f'<input type="hidden" name="back" value="{esc(here)}">'
            f'<input class="field" type="password" name="value" placeholder="{esc(placeholder)}" '
            'autocomplete="off" aria-label="DART 인증키">'
            '<button type="submit" class="btn sm primary">저장</button></form>')


def key_banner(bot, here: str) -> str:
    """DART 인증키가 없거나 거절됐으면 한국 화면 맨 위에서 바로 넣게 한다.

    처음엔 이 상자를 맨 아래 접어 뒀는데 못 찾았다. 한국 종목이 통째로 비는
    이유가 이 열쇠 하나라서, 없을 때는 가장 먼저 보이는 자리에 둔다.
    """
    dart = getattr(bot, "dart", None)
    if dart is not None and dart.ready:
        why = getattr(dart, "last_error", "")
        if not why:
            return ""
        return ('<div class="banner bad"><div class="b-text">🔑 <b>DART 인증키가 통하지 않습니다.</b> '
                f'{esc(why)} — <a href="https://opendart.fss.or.kr" target="_blank" rel="noopener">'
                'opendart.fss.or.kr</a> 에서 받은 키를 그대로 붙여넣으세요. 저장하면 바로 확인합니다.</div>'
                f'{_key_form(here, "인증키 다시 넣기")}</div>')
    return ('<div class="banner warn"><div class="b-text">🔑 <b>DART 인증키가 없습니다.</b> 넣으면 한국 종목의 '
            '공시·재무제표가 채워집니다 (무료·1분 · <a href="https://opendart.fss.or.kr" target="_blank" '
            'rel="noopener">opendart.fss.or.kr</a>). 이 컴퓨터의 <b>프로그램 폴더 바깥</b>에 저장돼서, '
            '폴더를 지우고 새로 받아도 남습니다.</div>'
            f'{_key_form(here, "인증키 붙여넣기")}</div>')


__all__ = ["key_banner", "update_banner"]
