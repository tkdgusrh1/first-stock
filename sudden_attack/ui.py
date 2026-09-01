"""브라우저에 뜨는 화면. 외부 라이브러리 없이 파이썬 표준 http.server 만 쓴다.

화면에서 제일 큰 것은 '한 번에 최적화' 버튼 하나다. 그 아래에 무엇을 왜 바꾸는지,
지금 어떤 상태인지, 그리고 되돌리는 방법이 있다. 눌러야 할 것이 하나라는 게 이
프로그램의 요점이고, 나머지는 눌러도 되는지 판단할 재료다.
"""

from __future__ import annotations

import html
import logging
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import __version__, backup, engine, guide
from .shell import relaunch_as_admin
from .tweaks import GROUPS, IMPACT_LABEL, NA, OFF, ON, UNKNOWN, catalog

log = logging.getLogger(__name__)

BADGE = {ON: ("적용됨", "ok"), OFF: ("안 됨", "no"), UNKNOWN: ("확인 불가", "hm"), NA: ("해당 없음", "hm")}


def esc(value) -> str:
    return html.escape(str(value if value is not None else ""))


class Screen:
    def __init__(self, optimizer: engine.Optimizer, root=None):
        self.optimizer = optimizer
        self.root = root
        self.notice = ""
        self.result = None
        self._lock = threading.Lock()

    # --- 화면 ---------------------------------------------------------
    def render(self) -> str:
        ctx = self.optimizer.ctx
        spec = engine.spec_of(ctx)
        statuses = self.optimizer.statuses()
        ready = [s for s in statuses if s.tweak.recommended and s.can_apply]
        record = backup.latest(self.root)

        body = [
            _head(spec, ctx),
            _notice(self.notice),
            _result(self.result),
            _hero(ready, statuses),
            _basics(spec),
            _game_box(ctx),
            _items(statuses),
            _revert_box(record, backup.history(self.root)),
            _guide(spec),
            _footer(),
        ]
        return _PAGE.format(style=_STYLE, body="\n".join(body))

    def render_bye(self) -> str:
        return _PAGE.format(
            style=_STYLE,
            body='<div class="card center"><h2>끝났습니다</h2>'
                 "<p class=muted>이 창은 닫으셔도 됩니다.</p></div>",
        )

    # --- 버튼 ---------------------------------------------------------
    def run(self, action: str, params: dict) -> str:
        # 두 번 눌러도 한 번씩 차례로 처리한다. 겹쳐 돌면 되돌리기 기록이 엉킨다.
        with self._lock:
            return self._run(action, params)

    def _run(self, action: str, params: dict) -> str:
        if action == "apply_all":
            self.result = self.optimizer.apply_recommended()
            return self.result.summary
        if action == "apply":
            keys = params.get("key") or []
            if not keys:
                self.result = None
                return "고른 항목이 없습니다."
            self.result = self.optimizer.apply(keys)
            return self.result.summary
        if action == "revert":
            which = (params.get("record") or [""])[0]
            record = self._record(which)
            if record is None:
                self.result = None
                return "되돌릴 기록이 없습니다."
            self.result = self.optimizer.revert(record)
            if not self.result.steps:
                return "되돌릴 것이 없었습니다."
            return f"{self.result.done}개를 원래대로 되돌렸습니다."
        if action == "game_path":
            value = (params.get("path") or [""])[0].strip()
            engine.remember_game_path(value, self.root)
            self.optimizer.ctx.install = _find(self.optimizer.ctx, value)
            self.result = None
            if self.optimizer.ctx.install:
                return f"찾았습니다: {self.optimizer.ctx.install.exe}"
            return "그 경로에서 실행 파일을 못 찾았습니다. 서든어택 폴더나 exe 를 넣어주세요."
        if action == "admin":
            import sys

            self.result = None
            if relaunch_as_admin(sys.argv[0]):
                return "관리자 권한으로 새 창을 띄웠습니다. 이 창은 닫으셔도 됩니다."
            return "관리자 권한으로 다시 띄우지 못했습니다. 시작 파일을 우클릭 → 관리자 권한으로 실행 해주세요."
        return ""

    def _record(self, which: str):
        if which:
            for record in backup.history(self.root):
                if record.path.name == which:
                    return record
            return None
        return backup.latest(self.root)


def _find(ctx, value: str):
    from . import game

    return game.find(registry=ctx.registry, saved=value)


# ---------------------------------------------------------------------------
# 조각들
# ---------------------------------------------------------------------------
def _head(spec, ctx) -> str:
    facts = []
    if spec.cpu:
        cores = f" ({spec.cores}코어)" if spec.cores else ""
        facts.append(("CPU", f"{spec.cpu}{cores}"))
    if spec.gpu:
        facts.append(("그래픽", spec.gpu))
    if spec.ram_gb:
        facts.append(("메모리", f"{spec.ram_gb:g} GB"))
    if spec.windows:
        facts.append(("윈도우", spec.windows))
    for screen in spec.monitors:
        mark = "" if screen.already_best else f" → 최대 {screen.best_hz}Hz 가능"
        facts.append(("모니터", f"{screen.width}×{screen.height} · {screen.hz}Hz{mark}"))

    rows = "".join(
        f'<div class="fact"><span>{esc(name)}</span><b>{esc(value)}</b></div>'
        for name, value in facts
    )
    warn = ""
    if not ctx.windows:
        warn = ('<div class="warn">지금은 윈도우가 아닙니다. 화면은 볼 수 있지만 '
                "실제로 바뀌지는 않습니다.</div>")
    elif not ctx.admin:
        warn = (
            '<div class="warn">관리자 권한이 아닙니다. 네트워크·전원처럼 컴퓨터 전체에 '
            "걸리는 항목은 잠겨 있습니다."
            '<form method="post" action="/action" class="inline">'
            '<input type="hidden" name="action" value="admin">'
            '<button class="mini">관리자 권한으로 다시 실행</button></form></div>'
        )
    return (
        '<header><h1>서든어택 최적화</h1>'
        '<p class="muted">윈도우 설정을 게임에 맞게 한 번에 바꿉니다. '
        "바꾼 값은 전부 기록해 두므로 언제든 되돌릴 수 있습니다.</p>"
        f'<div class="facts">{rows}</div>{warn}</header>'
    )


def _hero(ready, statuses) -> str:
    total = len([s for s in statuses if s.tweak.recommended])
    done = len([s for s in statuses if s.tweak.recommended and s.state == ON])
    if ready:
        line = f"권장 {total}개 중 <b>{len(ready)}개</b>를 아직 안 했습니다."
        button = f'<button class="go">한 번에 최적화 ({len(ready)}개)</button>'
    else:
        locked = [s for s in statuses if s.tweak.recommended and s.blocked]
        if locked:
            line = (f"권장 {total}개 중 {len(locked)}개가 <b>잠겨 있습니다.</b> "
                    f"{esc(locked[0].blocked)}.")
        else:
            line = f"권장 {total}개 중 {done}개가 이미 적용돼 있습니다. 더 할 게 없습니다."
        button = '<button class="go" disabled>지금 할 수 있는 것이 없습니다</button>'
    return (
        '<section class="hero">'
        f"<p>{line}</p>"
        '<form method="post" action="/action" onsubmit="wait(this)">'
        '<input type="hidden" name="action" value="apply_all">'
        f"{button}</form>"
        '<p class="muted small">누르면 아래 목록에서 ✅ 표시된 권장 항목만 적용합니다. '
        "몇 초 걸립니다.</p></section>"
    )


def _basics(spec) -> str:
    """프레임·주사율·지연·핑이 각각 뭔지, 그리고 이 게임에서 뭐가 진짜 병목인지.

    '체감이 어느 정도냐' 는 질문에 답하려면 먼저 무엇의 체감인지가 갈려야 한다.
    프레임과 주사율을 같은 것으로 알고 있으면 어떤 설명도 와닿지 않는다.
    """
    counts: dict[str, int] = {}
    for tweak in catalog():
        if tweak.affects:
            counts[tweak.affects] = counts.get(tweak.affects, 0) + 1
    chips = "".join(
        f'<span class="chip">{esc(name)} {count}</span>'
        for name, count in sorted(counts.items(), key=lambda pair: -pair[1])
    )

    hz = max((screen.hz for screen in spec.monitors), default=0)
    best = max((screen.best_hz for screen in spec.monitors), default=0)
    mine = ""
    if hz and best > hz:
        mine = (f'<p class="mine">지금 이 컴퓨터는 <b>{hz}Hz</b> 로 돌고 있고 '
                f'<b>{best}Hz</b> 까지 됩니다. 위 버튼이 고쳐줍니다.</p>')
    elif hz:
        mine = f'<p class="mine">지금 이 컴퓨터는 <b>{hz}Hz</b> — 이미 최대입니다.</p>'

    return f"""<details class="basics"><summary>먼저 — 서든어택은 '프레임'이 문제가 아닙니다.
그럼 뭐가 문제일까요?</summary>
<h4>네 가지는 서로 다른 것입니다</h4>
<div class="g-row"><b>프레임 (FPS)</b><span>컴퓨터가 1초에 <b>그리는</b> 장면 수 ·
그래픽카드와 게임 옵션이 정합니다</span></div>
<div class="g-row"><b>주사율 (Hz)</b><span>모니터가 1초에 <b>보여주는</b> 장면 수 ·
모니터와 윈도우 설정이 정합니다</span></div>
<div class="g-row"><b>입력 지연</b><span>마우스를 움직이고 화면에 나타나기까지 걸리는 시간</span></div>
<div class="g-row"><b>핑 (ms)</b><span>내가 쏜 신호가 서버까지 갔다 오는 시간</span></div>

<h4>서든어택은 프레임이 남습니다</h4>
<p>2005년에 나온 게임이라 요즘 컴퓨터에서는 프레임이 이미 넘칩니다.
그런데 <b>프레임이 300이든 400이든, 모니터가 60Hz 면 눈에 보이는 건 초당 60장입니다.</b>
남는 프레임을 더 늘리는 건 아무 의미가 없습니다.</p>
{mine}

<h4>그래서 진짜 병목은 셋입니다</h4>
<div class="g-row"><b>① 모니터가 몇 장 보여주나</b><span>주사율 — 셋 중 제일 큽니다</span></div>
<div class="g-row"><b>② 내 손이 화면에 얼마나 빨리 나타나나</b>
<span>마우스 가속, 전체 화면 최적화</span></div>
<div class="g-row"><b>③ 쏜 게 서버에 얼마나 빨리 닿나</b><span>Nagle, 네트워크 제한</span></div>

<h4>숫자로 보면</h4>
<p>60Hz 는 장면 하나가 <b>16.7ms</b> 동안 그대로 멈춰 있습니다. 그래서 방금 일어난 일이
화면에 뜨기까지 평균 8ms 를 기다립니다. 144Hz 는 6.9ms 라 평균 3.5ms 입니다.<br>
이 4~5ms 차이는 '반응 속도가 빨라진다' 기보다 <b>움직이는 적이 끊기지 않고 이어져
보인다</b> 는 쪽으로 옵니다. 그래서 숫자보다 체감이 큽니다.</p>

<h4>프레임을 정말 올리고 싶다면</h4>
<p>그건 윈도우가 아니라 <b>게임 안 옵션</b>입니다. 그림자·효과 끄기, 수직 동기 끄기.
이 화면 맨 아래 <b>직접 하셔야 하는 것</b> 을 보세요. 거기가 제일 크게 갈립니다.</p>

<h4>이 프로그램이 손대는 곳</h4>
<p class="chips">{chips}</p>
<p class="muted small">프레임 숫자를 실제로 올려주는 항목은 배경 녹화 끄기 하나뿐입니다.
나머지는 지연과 끊김 쪽입니다. 부풀려 적지 않았습니다.</p>
</details>"""


def _game_box(ctx) -> str:
    if ctx.install:
        return (
            '<section class="card slim"><b>서든어택</b> '
            f'<span class="path">{esc(ctx.install.exe)}</span> '
            f'<span class="muted small">({esc(ctx.install.source)})</span>'
            '<form method="post" action="/action" class="inline right">'
            '<input type="hidden" name="action" value="game_path">'
            '<input name="path" placeholder="다른 경로로 바꾸기" size="28">'
            '<button class="mini">바꾸기</button></form></section>'
        )
    return (
        '<section class="card slim miss"><b>서든어택을 못 찾았습니다.</b> '
        '<span class="muted small">설치 폴더나 실행 파일 경로를 넣어주세요. '
        "게임에 직접 거는 3개 항목(전체 화면 최적화·우선순위·검사 제외)에만 필요합니다.</span>"
        '<form method="post" action="/action" class="inline right">'
        '<input type="hidden" name="action" value="game_path">'
        '<input name="path" placeholder="C:\\Nexon\\SuddenAttack" size="30">'
        '<button class="mini">찾기</button></form></section>'
    )


def _items(statuses) -> str:
    blocks = []
    for group in GROUPS:
        rows = [s for s in statuses if s.tweak.group == group]
        if not rows:
            continue
        blocks.append(f'<h3 class="group">{esc(group)}</h3>' + "".join(_item(s) for s in rows))
    return (
        '<form method="post" action="/action" onsubmit="wait(this)">'
        '<input type="hidden" name="action" value="apply">'
        '<section class="list">' + "".join(blocks) + "</section>"
        '<div class="bar"><button class="sub">고른 것만 적용</button>'
        '<span class="muted small">체크를 풀면 그 항목은 건드리지 않습니다.</span></div>'
        "</form>"
    )


def _item(status) -> str:
    tweak = status.tweak
    label, kind = BADGE.get(status.state, ("?", "hm"))
    tags = []
    if tweak.admin:
        tags.append("관리자")
    if tweak.reboot:
        tags.append("재부팅 필요")
    if not tweak.recommended:
        tags.append("선택")
    tag_html = "".join(f'<span class="tag">{esc(t)}</span>' for t in tags)

    checked = " checked" if (tweak.recommended and status.can_apply) else ""
    disabled = " disabled" if status.blocked else ""
    blocked = f'<div class="blocked">{esc(status.blocked)}</div>' if status.blocked else ""
    note = f'<div class="note">{esc(tweak.note)}</div>' if tweak.note else ""
    area = f'<span class="area">{esc(tweak.affects)}</span>' if tweak.affects else ""
    return (
        f'<label class="row{" off" if status.blocked else ""}">'
        f'<input type="checkbox" name="key" value="{esc(tweak.key)}"{checked}{disabled}>'
        '<div class="body">'
        f'<div class="title">{esc(tweak.title)}{tag_html}'
        f'<span class="badge {kind}">{esc(label)}</span></div>'
        f'<div class="what">{esc(tweak.what)}</div>'
        f'<div class="gain"><span class="imp {esc(tweak.impact)}">'
        f'{esc(IMPACT_LABEL.get(tweak.impact, ""))}</span>{area}{esc(tweak.gain)}</div>'
        f"{note}{blocked}"
        "</div></label>"
    )


def _revert_box(record, records) -> str:
    if record is None:
        past = ""
        if records:
            past = ('<p class="muted small">되돌린 기록만 남아 있습니다. '
                    "지금 적용된 것은 없습니다.</p>")
        return ('<section class="card"><h3>되돌리기</h3>'
                '<p class="muted">아직 바꾼 것이 없어서 되돌릴 것도 없습니다.</p>'
                f"{past}</section>")

    changed = ", ".join(record.keys[:6]) + (" …" if len(record.keys) > 6 else "")
    others = ""
    rest = [r for r in records if r.path != record.path]
    if rest:
        lines = "".join(
            f"<li>{esc(r.label)} — {'되돌림' if r.reverted else f'{len(r.keys)}개'}</li>"
            for r in rest[:5]
        )
        others = f'<details><summary>지난 기록 {len(rest)}건</summary><ul>{lines}</ul></details>'
    return (
        '<section class="card"><h3>되돌리기</h3>'
        f'<p><b>{esc(record.label)}</b> 에 {len(record.keys)}개를 바꿨습니다.</p>'
        f'<p class="muted small">{esc(changed)}</p>'
        '<form method="post" action="/action" onsubmit="wait(this)">'
        '<input type="hidden" name="action" value="revert">'
        f'<input type="hidden" name="record" value="{esc(record.path.name)}">'
        '<button class="undo">원래대로 되돌리기</button></form>'
        '<p class="muted small">바꾸기 전 값을 그대로 다시 넣습니다. '
        "원래 없던 값은 지웁니다.</p>"
        f"{others}</section>"
    )


def _guide(spec) -> str:
    blocks = []
    for section in guide.sections(spec):
        rows = "".join(
            f'<div class="g-row"><b>{esc(what)}</b><span>{esc(how)}</span></div>'
            for what, how in section.items
        )
        lead = f'<p class="muted small">{esc(section.lead)}</p>' if section.lead else ""
        blocks.append(
            f'<details class="g"><summary>{esc(section.title)}</summary>{lead}{rows}</details>'
        )
    return '<section class="card"><h3>직접 하셔야 하는 것</h3>' + "".join(blocks) + "</section>"


def _notice(text: str) -> str:
    return f'<div class="notice">{esc(text)}</div>' if text else ""


def _result(outcome) -> str:
    if not outcome or not outcome.steps:
        return ""
    rows = "".join(
        f'<li class="{"ok" if step.ok else "bad"}">{esc(step.title)} — {esc(step.message)}</li>'
        for step in outcome.steps
    )
    reboot = ('<p class="warn-inline">재부팅해야 적용되는 항목이 있습니다.</p>'
              if outcome.reboot else "")
    return f'<section class="card result"><h3>{esc(outcome.summary)}</h3><ul>{rows}</ul>{reboot}</section>'


def _footer() -> str:
    return (
        '<footer class="muted small">서든어택 최적화 v'
        f"{esc(__version__)} · 바꾼 값은 <code>backup/</code> 폴더에 기록됩니다 · "
        "게임 파일은 건드리지 않습니다</footer>"
    )


# ---------------------------------------------------------------------------
_STYLE = """
:root {
  color-scheme: light dark;
  --bg:#f6f7f9; --fg:#1b1d21; --muted:#6b7280; --card:#fff; --line:#e5e7eb;
  --go:#2563eb; --go-fg:#fff; --ok:#16a34a; --no:#9ca3af; --bad:#dc2626; --warn:#b45309;
  --warnbg:#fef3c7;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg:#14161a; --fg:#e8eaed; --muted:#9aa1ab; --card:#1c1f24; --line:#2b2f36;
    --go:#3b82f6; --ok:#22c55e; --no:#6b7280; --bad:#f87171; --warn:#fbbf24; --warnbg:#3a2f14;
  }
}
* { box-sizing:border-box; }
body { margin:0; padding:28px 20px 60px; background:var(--bg); color:var(--fg);
  font-family:-apple-system,"Segoe UI","Malgun Gothic",system-ui,sans-serif;
  max-width:860px; margin-inline:auto; line-height:1.6; }
h1 { font-size:1.6rem; margin:0 0 6px; }
h3 { margin:0 0 12px; font-size:1.05rem; }
.muted { color:var(--muted); }
.small { font-size:.85rem; }
.center { text-align:center; }
header { margin-bottom:22px; }
.facts { display:grid; grid-template-columns:repeat(auto-fit,minmax(230px,1fr));
  gap:8px; margin-top:14px; }
.fact { background:var(--card); border:1px solid var(--line); border-radius:10px;
  padding:9px 12px; font-size:.86rem; display:flex; gap:8px; justify-content:space-between; }
.fact span { color:var(--muted); white-space:nowrap; }
.fact b { font-weight:600; text-align:right; overflow-wrap:anywhere; }
.warn { margin-top:14px; padding:12px 14px; border-radius:10px; background:var(--warnbg);
  color:var(--warn); border:1px solid var(--warn); font-size:.9rem; }
.warn-inline { color:var(--warn); font-size:.9rem; }
.hero { background:var(--card); border:1px solid var(--line); border-radius:16px;
  padding:24px; text-align:center; margin-bottom:16px; }
.hero p { margin:0 0 14px; }
button { font:inherit; cursor:pointer; border-radius:10px; border:1px solid var(--line);
  background:var(--card); color:var(--fg); padding:9px 16px; }
button:disabled { opacity:.5; cursor:default; }
.go { background:var(--go); color:var(--go-fg); border:none; font-size:1.15rem;
  font-weight:700; padding:16px 34px; border-radius:12px; }
.go:disabled { background:var(--no); }
.sub { font-weight:600; }
.undo { border-color:var(--bad); color:var(--bad); font-weight:600; }
.mini { padding:6px 12px; font-size:.85rem; }
.card { background:var(--card); border:1px solid var(--line); border-radius:14px;
  padding:18px 20px; margin-bottom:16px; }
.card.slim { padding:12px 16px; font-size:.9rem; }
.card.miss { border-color:var(--warn); }
.path { font-family:ui-monospace,Consolas,monospace; font-size:.85rem; overflow-wrap:anywhere; }
.inline { display:inline; }
.right { float:right; }
.list { margin-bottom:12px; }
.group { margin:22px 0 8px; font-size:.8rem; letter-spacing:.08em; color:var(--muted); }
.row { display:flex; gap:12px; align-items:flex-start; background:var(--card);
  border:1px solid var(--line); border-radius:12px; padding:14px 16px; margin-bottom:8px;
  cursor:pointer; }
.row.off { opacity:.62; cursor:default; }
.row input { margin-top:5px; width:17px; height:17px; flex:none; }
.body { flex:1; min-width:0; }
.title { font-weight:600; display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
.what { color:var(--muted); font-size:.88rem; margin-top:3px; }
.gain { font-size:.88rem; margin-top:5px; }
.imp { font-size:.72rem; font-weight:700; padding:1px 8px; border-radius:999px;
  margin-right:6px; white-space:nowrap; }
.imp.big { background:var(--go); color:#fff; }
.imp.mid { background:var(--warnbg); color:var(--warn); }
.imp.small { background:var(--bg); color:var(--muted); border:1px solid var(--line); }
.area { font-size:.72rem; padding:1px 8px; border-radius:999px; margin-right:8px;
  border:1px solid var(--line); color:var(--muted); white-space:nowrap; }
details.basics { background:var(--card); border:1px solid var(--line); border-radius:14px;
  padding:16px 20px; margin-bottom:16px; }
details.basics > summary { cursor:pointer; font-weight:700; font-size:1rem; }
details.basics h4 { margin:18px 0 8px; font-size:.92rem; }
details.basics p { font-size:.9rem; margin:6px 0; }
.mine { background:var(--bg); border-radius:8px; padding:9px 12px; }
.chips { display:flex; gap:6px; flex-wrap:wrap; }
.chip { font-size:.78rem; padding:2px 10px; border-radius:999px; background:var(--bg);
  border:1px solid var(--line); }
.note { font-size:.82rem; margin-top:5px; color:var(--warn); }
.blocked { font-size:.82rem; margin-top:5px; color:var(--bad); }
.badge { margin-left:auto; font-size:.75rem; padding:2px 9px; border-radius:999px;
  border:1px solid var(--line); white-space:nowrap; }
.badge.ok { color:var(--ok); border-color:var(--ok); }
.badge.no { color:var(--muted); }
.badge.hm { color:var(--warn); border-color:var(--warn); }
.tag { font-size:.7rem; padding:1px 7px; border-radius:999px; background:var(--bg);
  border:1px solid var(--line); color:var(--muted); }
.bar { display:flex; gap:12px; align-items:center; margin-bottom:22px; flex-wrap:wrap; }
.notice { background:var(--card); border:1px solid var(--go); border-radius:10px;
  padding:11px 15px; margin-bottom:16px; }
.result ul { margin:0; padding-left:18px; }
.result li.ok::marker { color:var(--ok); }
.result li.bad { color:var(--bad); }
details.g { border:1px solid var(--line); border-radius:10px; padding:10px 14px;
  margin-bottom:8px; }
details.g summary { cursor:pointer; font-weight:600; }
.g-row { display:flex; gap:12px; padding:6px 0; border-top:1px solid var(--line);
  font-size:.88rem; flex-wrap:wrap; }
.g-row b { min-width:180px; font-weight:600; }
.g-row span { color:var(--muted); flex:1; }
footer { margin-top:28px; text-align:center; }
input[type=text], input:not([type]) { font:inherit; padding:6px 10px; border-radius:8px;
  border:1px solid var(--line); background:var(--bg); color:var(--fg); }
"""

_PAGE = """<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>서든어택 최적화</title>
<style>{style}</style>
<script>
// 몇 초 걸리는 작업이다. 아무 반응이 없으면 두 번 누르게 되고, 두 번 누르면
// 되돌리기 기록이 두 개 생긴다. 누른 순간 버튼을 잠근다.
function wait(form) {{
  var button = form.querySelector('button');
  if (button) {{ button.disabled = true; button.textContent = '하는 중…'; }}
}}
</script>
{body}
"""


# ---------------------------------------------------------------------------
class _Handler(BaseHTTPRequestHandler):
    screen: Screen = None

    def log_message(self, fmt, *args):
        log.debug("화면 %s", fmt % args)

    def _guard(self) -> bool:
        # 이 프로그램은 컴퓨터 설정을 바꾼다. 같은 컴퓨터에서 연 것만 받는다.
        if self.client_address[0] not in ("127.0.0.1", "::1"):
            self.send_error(403, "localhost only")
            return False
        return True

    def do_GET(self):
        if not self._guard():
            return
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._html(self.screen.render())
        elif path == "/healthz":
            self._respond(b"ok", "text/plain; charset=utf-8")
        else:
            self.send_error(404)

    def do_POST(self):
        if not self._guard():
            return
        if urlparse(self.path).path != "/action":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length") or 0)
        params = parse_qs(self.rfile.read(length).decode("utf-8"))
        action = (params.get("action") or [""])[0]

        self.screen.notice = self.screen.run(action, params)
        self.send_response(303)
        self.send_header("Location", "/")
        self.end_headers()

    def _html(self, text: str):
        self._respond(text.encode("utf-8"), "text/html; charset=utf-8")

    def _respond(self, payload: bytes, content_type: str):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)


def start(screen: Screen, port: int = 8770, open_browser: bool = True):
    handler = type("Handler", (_Handler,), {"screen": screen})
    server = None
    for candidate in range(port, port + 10):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", candidate), handler)
            port = candidate
            break
        except OSError as exc:
            log.debug("포트 %s 사용 중: %s", candidate, exc)
    if server is None:
        raise OSError(f"{port}~{port + 9} 포트가 모두 사용 중입니다.")

    url = f"http://127.0.0.1:{port}/"
    log.info("화면: %s", url)
    if open_browser:
        threading.Timer(0.7, lambda: _open(url)).start()
    return server, url


def _open(url: str) -> None:
    try:
        webbrowser.open(url)
    except Exception as exc:
        log.warning("브라우저를 열지 못했습니다(%s). 직접 %s 에 접속하세요.", exc, url)
