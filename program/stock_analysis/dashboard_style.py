"""대시보드의 겉모습 — HTML 틀 · CSS · 화면 스크립트.

화면을 그리는 로직(dashboard.py)과 떼어 둔다. 한 파일에 3,300줄이 섞여
있으면 색 하나를 바꾸려 해도 로직 사이를 헤집어야 하고, 로직을 고치다
스타일을 건드리는 일도 생긴다.

_PAGE 는 str.format 으로 채운다. 그래서 CSS 의 중괄호는 두 겹({{ }})이다.
"""

# 테마 전환. 화면이 그려지기 전에 적용해야 새로고침할 때마다 흰 화면이 번쩍이지 않는다.
# (이 페이지는 5분마다 자동 새로고침되므로 특히 중요하다)
_THEME_SCRIPT = """<script>
// 화면 밝기. 기본은 '시간' — 낮에는 밝게, 저녁부터 어둡게 알아서 바뀐다.
// 이 페이지는 5분마다 새로고침되므로, 그려지기 전에 정해야 흰 화면이 번쩍이지 않는다.
var DAY_START = 7;    // 07:00 부터 밝게
var DAY_END = 19;     // 19:00 부터 어둡게
var THEME_ORDER = ['auto', 'system', 'light', 'dark'];
var THEME_LABEL = {
  auto: '🕗 시간에 맞춰', system: '🌗 시스템', light: '☀️ 밝게', dark: '🌙 어둡게'
};

function themeByClock() {
  var hour = new Date().getHours();
  return (hour >= DAY_START && hour < DAY_END) ? 'light' : 'dark';
}
function currentTheme() {
  try { return localStorage.getItem('theme') || 'auto'; } catch (e) { return 'auto'; }
}
function applyTheme(choice) {
  var root = document.documentElement;
  if (choice === 'auto') { root.setAttribute('data-theme', themeByClock()); }
  else if (choice === 'system') { root.removeAttribute('data-theme'); }
  else { root.setAttribute('data-theme', choice); }
}
applyTheme(currentTheme());          // 첫 그림 전에 바로 적용

function paintThemeButton() {
  var button = document.getElementById('themebtn');
  if (!button) { return; }
  var choice = currentTheme();
  var text = THEME_LABEL[choice];
  if (choice === 'auto') {
    text += themeByClock() === 'dark' ? ' · 지금 어둡게' : ' · 지금 밝게';
  }
  button.textContent = text;
  button.title = '화면 밝기: ' + text + ' (눌러서 변경)\\n'
    + '시간에 맞춰 = ' + DAY_START + '시~' + DAY_END + '시 밝게, 그 밖은 어둡게';
}
function cycleTheme() {
  var next = THEME_ORDER[(THEME_ORDER.indexOf(currentTheme()) + 1) % THEME_ORDER.length];
  try { localStorage.setItem('theme', next); } catch (e) {}
  applyTheme(next);
  paintThemeButton();
}
// 자동일 때는 페이지를 열어둔 채 저녁이 되어도 알아서 넘어가야 한다
setInterval(function () {
  if (currentTheme() === 'auto') { applyTheme('auto'); paintThemeButton(); }
}, 60000);
document.addEventListener('DOMContentLoaded', paintThemeButton);

// 접었다 편 것을 기억한다.
//
// 이 화면은 90초마다 스스로 새로고침된다. 그때 펼쳐둔 것이 도로 닫히면
// 읽던 자리를 잃는다. 'data-keep' 이 붙은 것만 기억하므로, 기억하지
// 말아야 할 것까지 따라 열리는 일은 없다.
function foldKey(el) { return 'fold:' + el.getAttribute('data-keep'); }

function rememberFold(event) {
  try { localStorage.setItem(foldKey(event.target), event.target.open ? '1' : '0'); }
  catch (e) {}
}

function restoreFolds() {
  var items = document.querySelectorAll('details[data-keep]');
  for (var i = 0; i < items.length; i++) {
    var el = items[i], saved = null;
    try { saved = localStorage.getItem(foldKey(el)); } catch (e) {}
    if (saved === '1') { el.open = true; }
    else if (saved === '0') { el.open = false; }
    el.addEventListener('toggle', rememberFold);
  }
}
document.addEventListener('DOMContentLoaded', restoreFolds);

// 화면은 주기적으로 통째로 다시 불러온다. 그때마다 맨 위로 튀면, 아래쪽
// 캔들을 보던 사람은 매번 다시 내려가야 한다. 보던 자리를 기억해 둔다.
// (같은 시장 화면일 때만 — 미국에서 한국으로 넘어갔으면 맨 위가 맞다.)
function scrollKey() { return 'scroll:' + location.pathname + location.search; }
window.addEventListener('beforeunload', function () {
  try { sessionStorage.setItem(scrollKey(), String(window.scrollY)); } catch (e) {}
});
window.addEventListener('load', function () {
  var saved = null;
  try { saved = sessionStorage.getItem(scrollKey()); } catch (e) {}
  if (saved !== null) { window.scrollTo(0, parseInt(saved, 10) || 0); }
});
</script>"""

_PAGE = """<!doctype html>
<html lang="ko"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="{refresh}">
<title>관심 종목 감시</title>
<link rel="icon" href="data:,">
<!--THEME-->
<style>
/* 색은 여기 한 곳에서만 정한다.
   기본값 = 밝은 화면. 시스템이 어두우면 자동으로, 사람이 고르면 그 선택이 이긴다. */
:root {{
  color-scheme: light;
  --bg:#f6f7f9; --fg:#1b1d21; --muted:#6b7280; --card:#ffffff; --line:#e5e7eb;
  --accent:#2563eb; --good:#15803d; --bad:#b91c1c; --alert:#b45309; --zebra:#fafbfc;
  --quote:#f3f4f6;
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    color-scheme: dark;
    --bg:#14161a; --fg:#e8eaed; --muted:#9aa1ab; --card:#1c1f24; --line:#2b2f36;
    --accent:#60a5fa; --good:#4ade80; --bad:#f87171; --alert:#fbbf24; --zebra:#191c21;
    --quote:#22262c;
  }}
}}
:root[data-theme="dark"] {{
  color-scheme: dark;
  --bg:#14161a; --fg:#e8eaed; --muted:#9aa1ab; --card:#1c1f24; --line:#2b2f36;
  --accent:#60a5fa; --good:#4ade80; --bad:#f87171; --alert:#fbbf24; --zebra:#191c21;
  --quote:#22262c;
}}
* {{ box-sizing:border-box; }}
body {{
  margin:0; padding:24px; background:var(--bg); color:var(--fg); max-width:1600px;
  margin-inline:auto;
  font-family:-apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Malgun Gothic",
              "Noto Sans KR",Segoe UI,sans-serif; line-height:1.6;
}}
h1 {{ font-size:1.5rem; margin:0 0 4px; }}
h2 {{ font-size:1.1rem; margin:0 0 12px; padding-bottom:6px; border-bottom:2px solid var(--line); }}

/* --- 섹션은 카드로 ---------------------------------------------------------
   전에는 제목 밑줄 하나로만 나뉘어서, 화면을 내리면 어디서 어디까지가 한
   덩어리인지 알 수 없었다. 바탕색이 다른 카드로 감싸면 경계가 눈에 잡힌다. */
body > section {{
  background:var(--card); border:1px solid var(--line); border-radius:14px;
  padding:18px 20px; margin:16px 0; }}
body > section:empty {{ display:none; }}          /* 빈 섹션이 자리를 먹지 않게 */

/* 접히는 섹션은 제목 줄 전체가 누르는 자리가 되게 카드 끝까지 넓힌다.
   제목 글자만 눌리면 어디를 눌러야 하는지 손이 먼저 헷갈린다. */
body > section > details.fold > summary {{ margin:-18px -20px 0; padding:18px 20px; }}
body > section > details.fold > summary h2 {{ margin:0; padding:0; border:none; }}
body > section > details.fold[open] > summary h2 {{
  padding-bottom:6px; border-bottom:2px solid var(--line); }}
h3 {{ font-size:1.25rem; margin:0; }}
h4 {{ font-size:.85rem; margin:18px 0 8px; color:var(--muted); font-weight:700; }}
p {{ margin:3px 0; }}
a {{ color:var(--accent); text-decoration:none; }}
a:hover {{ text-decoration:underline; }}
sup {{ font-size:.65em; color:var(--accent); margin-left:1px; }}
.term {{ color:inherit; border-bottom:1px dotted var(--muted); }}
.term:hover {{ color:var(--accent); text-decoration:none; }}
.sub {{ color:var(--muted); font-size:.85rem; }}
.hint {{ color:var(--muted); font-size:.8rem; margin-bottom:10px; }}
.muted {{ color:var(--muted); }}
.small {{ font-size:.75rem; }}
.count {{ color:var(--muted); font-weight:400; font-size:.85rem; }}
.up {{ color:var(--good); }} .down {{ color:var(--bad); }} .warnmark {{ color:var(--alert); }}
.flat {{ color:var(--muted); }}

/* --- 종목 표식 ------------------------------------------------------------
   목록에서 사람이 먼저 찾는 건 '어느 회사인가' 다. 글자만 있으면 한 줄씩
   읽어야 하지만, 색 있는 동그라미는 스치듯 봐도 잡힌다. 색은 티커에서
   정해지므로 같은 종목은 늘 같은 색이다. */
.tk {{ display:flex; align-items:center; gap:9px; color:inherit; }}
.tk:hover {{ color:var(--accent); }}
.tk-text {{ display:flex; flex-direction:column; line-height:1.25; min-width:0; }}
.tk-text .small {{ white-space:nowrap; overflow:hidden; text-overflow:ellipsis; max-width:11ch; }}
.tk-badge {{ display:inline-flex; align-items:center; justify-content:center;
  width:26px; height:26px; flex:0 0 26px; border-radius:50%;
  color:#fff; font-size:.72rem; font-weight:700; letter-spacing:-.02em; }}
.tk-logo {{ position:relative; display:inline-flex; width:26px; height:26px; flex:0 0 26px; }}
.tk-logo img {{ width:26px; height:26px; border-radius:50%; object-fit:contain;
  background:var(--card); position:relative; z-index:1; }}
.tk-logo .tk-badge {{ position:absolute; inset:0; }}   /* 그림이 실패하면 드러난다 */

/* --- 흐름(스파크라인) -----------------------------------------------------
   눈금도 숫자도 없다. 값을 읽는 그림이 아니라 방향을 보는 그림이다. */
.spark {{ display:block; overflow:visible; }}
.spark .sp-line {{ fill:none; stroke-width:1.6; vector-effect:non-scaling-stroke;
  stroke-linejoin:round; stroke-linecap:round; }}
.spark .sp-fill {{ stroke:none; opacity:.14; }}
.sp-up .sp-line, .sp-up .sp-fill {{ stroke:var(--good); fill:var(--good); }}
.sp-down .sp-line, .sp-down .sp-fill {{ stroke:var(--bad); fill:var(--bad); }}
/* --- 캔들 ------------------------------------------------------------------
   스파크라인과 달리 이건 **값을 읽는 그림**이라 눈금을 함께 그린다. */
/* TradingView 차트. 그려지면 서버가 그린 SVG 는 숨긴다. */
.tv-chart {{ margin:6px 0 14px; }}
.tv-canvas {{ height:0; }}
.tv-chart.tv-ready .tv-canvas {{ height:340px; }}
.tv-chart.tv-ready .tv-fallback {{ display:none; }}
.tv-legend {{ min-height:1.4em; color:var(--muted); font-variant-numeric:tabular-nums; }}
.tv-legend b {{ color:var(--fg); }}
.tv-ma20 {{ color:#f59e0b; }} .tv-ma60 {{ color:#3b82f6; }}
.tv-help {{ margin:4px 0 0; }}
.tv-chart:not(.tv-ready) .tv-help {{ display:none; }}
.candle-wrap {{ overflow-x:auto; margin:6px 0 14px; }}
.candles {{ width:100%; min-width:520px; height:190px; display:block; }}
.candles .c-wick {{ stroke-width:1; }}
.candles .c-up {{ stroke:var(--good); fill:var(--good); }}
.candles .c-down {{ stroke:var(--bad); fill:var(--bad); }}
.candles .c-body {{ stroke:none; }}
.candles .c-grid {{ stroke:var(--line); stroke-width:1; }}
.candles .c-tick {{ fill:var(--muted); font-size:10px;
  font-variant-numeric:tabular-nums; }}
.candles .c-end {{ text-anchor:end; }}
/* 오늘 봉은 아직 끝나지 않았다. 확정된 종가처럼 보이면 안 된다. */
.candles .c-live {{ stroke:var(--accent); stroke-width:1; stroke-dasharray:3 3; opacity:.5; }}

.spark-cell {{ width:96px; padding-top:10px !important; padding-bottom:10px !important; }}

/* 가로로 긴 표에서 종목 칸은 늘 보이게. 오른쪽 끝까지 밀고 나면
   어느 회사 줄을 보고 있는지 알 수 없어진다. */
.summary tbody td:first-child, .summary thead th:first-child {{
  position:sticky; left:0; z-index:2; background:var(--card); }}
.summary tbody tr:nth-child(even) td:first-child {{ background:var(--zebra); }}
.summary thead th:first-child {{ z-index:3; }}
.warn {{ color:var(--alert); font-size:.85rem; }}
header {{ display:flex; flex-wrap:wrap; gap:16px; justify-content:space-between; align-items:flex-start; }}
.actions {{ display:flex; flex-wrap:wrap; gap:8px; }}
button {{ font:inherit; padding:7px 12px; border-radius:8px; border:1px solid var(--line);
  background:var(--card); color:var(--fg); cursor:pointer; white-space:nowrap; }}
button:hover {{ border-color:var(--accent); color:var(--accent); }}
button.ghost {{ border:none; background:none; color:var(--muted); padding:2px 6px; }}
.badge {{ display:inline-block; padding:1px 8px; border-radius:999px; font-size:.78rem; white-space:nowrap; }}
.badge.open {{ background:rgba(21,128,61,.14); color:var(--good); }}
.badge.closed {{ background:rgba(185,28,28,.14); color:var(--bad); }}
.notice {{ margin:16px 0; padding:10px 14px; border-radius:8px; background:var(--card); border:1px solid var(--line); }}
.notice.busy {{ border-color:var(--alert); color:var(--alert); }}
.notice.bad {{ border-color:var(--bad); color:var(--bad); }}
.notice.update {{ border-color:var(--accent); display:flex; gap:10px; align-items:center; flex-wrap:wrap; }}
/* 열쇠가 없으면 한국 화면이 통째로 빈다. 눈에 띄어야 한다. */
.notice.keyneed {{ border-color:var(--accent); border-width:2px;
  display:flex; gap:10px; align-items:center; flex-wrap:wrap; }}
.notice.keyneed input[type=password] {{ font:inherit; padding:8px 12px; border-radius:8px;
  border:1px solid var(--line); background:var(--card); color:var(--fg); width:11rem; }}
.notice.keyneed .inline-form {{ display:flex; gap:6px; align-items:center; }}
.inline-form {{ display:inline; }}
/* 속보 패널 — 작게, 접어서 */
.right-col {{ display:flex; flex-direction:column; gap:10px; align-items:stretch;
  flex:1 1 560px; max-width:680px; }}
.right-col .actions {{ justify-content:flex-end; }}
.newsbox {{ border:1px solid var(--line); border-radius:12px; background:var(--card);
  padding:9px 13px; font-size:.9rem; }}
.newsbox > summary {{ font-weight:700; color:var(--fg); display:flex; gap:8px; align-items:center; }}
.newsbox > summary .muted {{ font-weight:400; margin-left:auto; }}
.badge-count {{ background:var(--bad); color:#fff; border-radius:999px; padding:0 7px;
  font-size:.72rem; font-weight:700; }}
ul.news {{ list-style:none; padding:0; margin:8px 0 0; }}
ul.news li {{ display:flex; gap:8px; padding:8px 0; border-top:1px solid var(--line); }}
ul.news li:first-child {{ border-top:none; }}
.n-ic {{ font-size:.9rem; line-height:1.5; }}
.n-t {{ font-size:.87rem; font-weight:600; line-height:1.45; }}
.n-t a {{ color:var(--fg); }}
.n-t a:hover {{ color:var(--accent); }}
.n-m {{ display:flex; gap:6px; align-items:center; flex-wrap:wrap; margin-top:3px; font-size:.72rem; }}
.n-why {{ color:var(--alert); }}
.tag.macro {{ background:rgba(185,28,28,.12); color:var(--bad); border-color:transparent; }}
.submore {{ margin:6px 0 0; }}
/* 기사 시각과 매체 신뢰도 */
.n-m .when {{ color:var(--muted); font-variant-numeric:tabular-nums; }}
.fresh {{ color:var(--accent); font-weight:700; }}
.src {{ padding:1px 7px; border-radius:999px; border:1px solid var(--line); font-size:.7rem; }}
.src.t3 {{ background:rgba(21,128,61,.13); color:var(--good); border-color:transparent; font-weight:700; }}
.src.t2 {{ background:var(--bg); color:var(--muted); }}
.src.t1 {{ background:transparent; color:var(--muted); font-style:italic; }}

/* 환율·지수 한 줄 */
.strip {{ display:flex; flex-wrap:wrap; gap:10px 22px; align-items:center;
  background:var(--card); border:1px solid var(--line); border-radius:12px;
  padding:9px 14px; margin-top:14px; font-size:.85rem; }}
.strip.loading {{ color:var(--muted); }}
.strip-group {{ display:flex; gap:12px; align-items:center; flex-wrap:wrap; }}
.strip-label {{ font-size:.74rem; color:var(--muted); font-weight:700; }}
.strip .q {{ display:flex; gap:5px; align-items:baseline; }}
.q-name {{ color:var(--muted); font-size:.76rem; }}
.q-val {{ font-weight:700; font-variant-numeric:tabular-nums; }}
.strip-when {{ margin-left:auto; }}

/* ETF */
.tag.etf {{ background:rgba(37,99,235,.12); color:var(--accent); border-color:transparent; font-weight:700; }}
.tag.etf.risky {{ background:rgba(180,83,9,.15); color:var(--alert); }}
.fundwarn {{ border:1px solid var(--alert); border-radius:10px; padding:10px 14px;
  margin:10px 0; background:rgba(180,83,9,.07); font-size:.85rem; }}
.fundwarn ul {{ margin:6px 0 0; padding-left:18px; }}
table.track td {{ font-variant-numeric:tabular-nums; }}
td.etfnote {{ text-align:left !important; white-space:normal; font-size:.8rem; }}
table.translate td {{ text-align:left; white-space:normal; }}
table.translate input[type=password] {{ width:180px; }}

/* 카드 안의 접이식 구획 — 접힌 줄에도 결론이 보인다 */
details.grp {{ border:1px solid var(--line); border-radius:10px; margin:10px 0;
  background:var(--bg); }}
details.grp > summary {{ padding:10px 14px; cursor:pointer; list-style:none;
  display:flex; gap:8px; align-items:center; flex-wrap:wrap; font-size:.92rem; }}
details.grp > summary::-webkit-details-marker {{ display:none; }}
details.grp > summary::after {{ content:"▸"; color:var(--muted); margin-left:auto; font-size:.8rem; }}
details.grp[open] > summary::after {{ content:"▾"; }}
details.grp > summary:hover {{ background:var(--zebra); }}
details.grp[open] > summary {{ border-bottom:1px solid var(--line); }}
.grp-body {{ padding:2px 14px 14px; }}
.grp-body > h4:first-child {{ margin-top:12px; }}
.grp-note {{ color:var(--muted); font-size:.8rem; font-weight:400; }}
.dot {{ width:9px; height:9px; border-radius:50%; display:inline-block; flex:none;
  background:var(--muted); }}
.dot.d-good {{ background:var(--good); }}
.dot.d-fair {{ background:var(--alert); }}
.dot.d-poor {{ background:var(--bad); }}
.dot.d-unknown {{ background:var(--line); border:1px solid var(--muted); }}

/* 내 보유 */
.mine {{ border:1px solid var(--accent); border-radius:10px; padding:10px 14px;
  margin:10px 0; background:color-mix(in srgb, var(--accent) 7%, transparent); }}
.mine-head {{ font-size:.95rem; margin-bottom:6px; }}
dl.stats.tight div {{ padding:6px 10px; }}

/* 한글 요약 + 영어 원문 */
ul.ko-list {{ list-style:none; padding-left:0; }}
li.ko, div.ko {{ padding:9px 0; border-top:1px solid var(--line); }}
ul.ko-list > li.ko:first-child {{ border-top:none; }}
.ko-line {{ font-size:.92rem; line-height:1.55; }}
.ko-topic {{ display:inline-block; font-size:.72rem; font-weight:700; color:var(--accent);
  background:var(--bg); border:1px solid var(--line); border-radius:999px;
  padding:1px 8px; margin-right:6px; }}
.ko-mark {{ font-size:.68rem; color:var(--muted); border:1px solid var(--line);
  border-radius:4px; padding:0 5px; margin-left:6px; white-space:nowrap; }}
.ko-src, .ko-more {{ margin-top:5px; }}
.ko-src > summary, .ko-more > summary {{ font-size:.74rem; color:var(--muted); cursor:pointer; }}
.ko-src > summary:hover, .ko-more > summary:hover {{ color:var(--accent); }}
.ko-src .quote {{ margin-top:5px; font-size:.82rem; }}

/* 화면 밝기 버튼 */
button.theme {{ border:1px solid var(--line); border-radius:8px; padding:6px 10px;
  background:var(--card); font-size:.82rem; }}
.left-col {{ flex:1 1 460px; min-width:0; }}
.strip {{ margin-top:12px; }}

/* 종목 카드 — 접이식 */
details.stock {{ padding:0; }}
details.stock > summary .tk-badge, details.stock > summary .tk-logo {{ margin-right:2px; }}
details.stock > summary {{ padding:14px 18px; list-style:none; cursor:pointer;
  display:flex; gap:10px; align-items:center; flex-wrap:wrap; }}
details.stock > summary::-webkit-details-marker {{ display:none; }}
details.stock > summary::before {{ content:"▸"; color:var(--muted); font-size:.8rem; }}
details.stock[open] > summary::before {{ content:"▾"; }}
details.stock[open] > summary {{ border-bottom:1px solid var(--line); }}
details.stock > summary:hover {{ background:var(--zebra); }}
.card-body {{ padding:4px 18px 18px; }}
.cname {{ font-weight:400; }}
button.ghost.small {{ font-size:.72rem; padding:0; text-decoration:underline; }}

.scroll {{ overflow-x:auto; border:1px solid var(--line); border-radius:12px; background:var(--card); }}
table.summary {{ border-collapse:collapse; width:100%; font-size:.86rem; white-space:nowrap; }}
table.summary th {{ text-align:right; padding:10px 12px; font-size:.74rem; color:var(--muted);
  font-weight:600; border-bottom:1px solid var(--line); background:var(--card); }}
table.summary th:first-child {{ text-align:left; }}
table.summary td {{ padding:10px 12px; border-bottom:1px solid var(--line); vertical-align:middle; }}
table.summary td.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
table.summary tbody tr:nth-child(even) {{ background:var(--zebra); }}
table.summary tbody tr:last-child td {{ border-bottom:none; }}
.verdict {{ padding:2px 8px; border-radius:999px; font-size:.78rem; white-space:nowrap; }}
.v-good {{ background:rgba(21,128,61,.14); color:var(--good); }}
.v-fair {{ background:rgba(180,83,9,.14); color:var(--alert); }}
.v-poor {{ background:rgba(185,28,28,.14); color:var(--bad); }}
.v-unknown {{ background:var(--zebra); color:var(--muted); }}

.addform {{ display:flex; gap:8px; margin-top:14px; }}
.addform input, .inline input {{ font:inherit; padding:8px 12px; border-radius:8px;
  border:1px solid var(--line); background:var(--card); color:var(--fg); }}
.addform input {{ flex:0 1 320px; }}
/* 열쇠 칸은 좁게. 넓으면 '저장' 단추가 아래로 밀려 한 줄에 안 들어온다. */
#keys input[type=password] {{ width:9rem; }}
#keys form.inline {{ display:flex; gap:6px; align-items:center; }}
#keys .nowrap {{ white-space:nowrap; }}
.stack {{ display:flex; flex-direction:column; gap:18px; }}
.card {{ background:var(--card); border:1px solid var(--line); border-radius:14px; padding:20px;
  scroll-margin-top:16px; }}
.card.wide.stock {{ padding:0; }}
.card-head {{ display:flex; justify-content:space-between; align-items:center; gap:10px; }}
.card-head .price {{ margin-left:auto; font-size:1.05rem; font-weight:600;
  font-variant-numeric:tabular-nums; display:flex; gap:8px; align-items:center; flex-wrap:wrap; }}
.card-head .ext {{ font-size:.78rem; font-weight:500; color:var(--muted); }}
.guidance {{ list-style:none; padding:0; margin:6px 0; }}
.guidance li {{ margin:10px 0; }}
.g-line {{ display:flex; gap:6px; align-items:center; flex-wrap:wrap; margin-bottom:4px; }}
.gv {{ font-size:.95rem; color:var(--accent); font-variant-numeric:tabular-nums; }}
.line {{ font-size:.88rem; margin:12px 0; }}
.group {{ margin-top:20px; padding-top:14px; border-top:1px solid var(--line); }}
.group-title {{ font-size:.8rem; font-weight:700; color:var(--muted); letter-spacing:.02em;
  margin-bottom:10px; }}
.group h4:first-of-type {{ margin-top:0; }}

.verdict-box {{ border:1px solid var(--line); border-left:4px solid var(--muted);
  border-radius:10px; padding:14px 16px; margin:14px 0; background:var(--zebra); }}
.verdict-box.v-good {{ border-left-color:var(--good); }}
.verdict-box.v-fair {{ border-left-color:var(--alert); }}
.verdict-box.v-poor {{ border-left-color:var(--bad); }}
.verdict-head {{ display:flex; gap:12px; align-items:flex-start; }}
.verdict-head .big {{ font-size:1.6rem; line-height:1; }}
.axes {{ display:grid; gap:10px; grid-template-columns:repeat(auto-fit,minmax(240px,1fr)); margin-top:14px; }}
.axis {{ background:var(--card); border:1px solid var(--line); border-radius:8px; padding:10px 12px; }}
.axis-head {{ display:flex; align-items:center; gap:6px; font-size:.9rem; }}
.axis-head .tag {{ margin-left:auto; }}
.axis-line {{ font-size:.82rem; margin:6px 0; }}
.evidence {{ list-style:none; padding:0; margin:0; font-size:.76rem; color:var(--muted); }}
.evidence li {{ padding:1px 0; font-variant-numeric:tabular-nums; }}
.watch {{ margin-top:14px; font-size:.83rem; }}
.watch ul {{ margin:6px 0 0; padding-left:18px; color:var(--muted); }}
.watch li {{ padding:2px 0; }}

.stats {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(148px,1fr)); gap:8px; margin:0; }}
.stats div {{ background:var(--bg); border-radius:8px; padding:7px 10px; }}
.stats dt {{ font-size:.7rem; color:var(--muted); }}
.stats dd {{ margin:0; font-size:.92rem; font-weight:600; font-variant-numeric:tabular-nums; }}
/* '300조 8,709억원' 에서 '원' 만 다음 줄로 떨어지면 값이 잘린 것처럼 보인다 */
.stats dd {{ white-space:nowrap; overflow-x:auto; }}
.charts {{ display:grid; gap:16px; grid-template-columns:repeat(auto-fit,minmax(240px,1fr)); }}
.chart-title {{ font-size:.72rem; color:var(--muted); }}
.bars {{ display:flex; align-items:flex-end; gap:5px; height:70px; margin-top:6px; }}
.barwrap {{ flex:1; display:flex; flex-direction:column; align-items:center; justify-content:flex-end; height:100%; }}
.bar {{ width:100%; background:var(--accent); border-radius:3px 3px 0 0; opacity:.75; }}
.bar.neg {{ background:var(--bad); }}
.barlabel {{ font-size:.6rem; color:var(--muted); margin-top:3px; }}

.checks {{ list-style:none; padding:0; margin:0; font-size:.85rem; }}
.checks li {{ display:flex; gap:6px; padding:7px 0; border-top:1px solid var(--line); flex-wrap:wrap; }}
.checks li:first-child {{ border-top:none; }}
.checks .detail {{ color:var(--muted); flex:1 1 100%; font-size:.8rem; }}
.bullets {{ margin:0; padding-left:18px; font-size:.83rem; color:var(--muted); }}
.bullets li {{ padding:2px 0; }}

details {{ margin:10px 0; }}
summary {{ cursor:pointer; font-size:.85rem; color:var(--accent); padding:4px 0; }}
summary:hover {{ text-decoration:underline; }}
.quote {{ background:var(--quote); border-radius:6px; padding:9px 12px; margin:6px 0;
  font-size:.82rem; line-height:1.65; }}
.quotes {{ list-style:none; padding:0; margin:6px 0; }}
.quotes li {{ background:var(--quote); border-radius:6px; padding:8px 11px; margin:5px 0; font-size:.82rem; }}
.inputs .inline {{ display:flex; flex-wrap:wrap; gap:10px; align-items:flex-end; margin:8px 0; }}
.inputs label {{ display:flex; flex-direction:column; font-size:.75rem; color:var(--muted); gap:3px; }}
.inputs input {{ min-width:200px; }}

/* 공시는 날짜별로 묶는다. 스무 줄이 시각만 달고 쭉 이어지면 어디까지가
   오늘 것인지 알 수 없다. */
.f-day + .f-day {{ margin-top:14px; }}
.f-daytop {{ font-size:.78rem; font-weight:700; color:var(--muted);
  padding:6px 0 4px; position:sticky; top:0; background:var(--card); z-index:1; }}
ul.filings li {{ display:flex; gap:9px; align-items:flex-start; }}
ul.filings li .tk-badge, ul.filings li .tk-logo {{ margin-top:1px; }}
.f-text {{ min-width:0; flex:1; }}
ul.filings, ul.plain {{ list-style:none; padding:0; margin:0; }}
ul.filings li, ul.plain li {{ padding:8px 10px; border-bottom:1px solid var(--line); font-size:.88rem;
  display:flex; gap:8px; align-items:baseline; flex-wrap:wrap; }}
ul.filings li.tone-alert {{ border-left:3px solid var(--alert); }}
ul.filings li.tone-good {{ border-left:3px solid var(--good); }}
ul.filings li.tone-bad {{ border-left:3px solid var(--bad); }}
.when {{ color:var(--muted); font-variant-numeric:tabular-nums; font-size:.8rem; min-width:118px; }}
.tag {{ font-size:.7rem; padding:1px 7px; border-radius:999px; background:var(--bg); border:1px solid var(--line); }}
.detail {{ color:var(--muted); }}
.two-col {{ display:grid; gap:24px; grid-template-columns:repeat(auto-fit,minmax(320px,1fr)); }}

/* 종료 안내 — 가운데 카드 하나만 */
.gate {{ max-width:420px; margin:9vh auto; background:var(--card); border:1px solid var(--line);
  border-radius:16px; padding:30px 28px; text-align:center; }}
.gate h1 {{ font-size:1.25rem; margin-bottom:6px; }}
.gate .sub {{ margin-bottom:18px; }}
.gate-note {{ color:var(--muted); font-size:.8rem; margin-top:12px; line-height:1.6; }}

/* 경제 지표 — 숫자를 크게, 뜻을 바로 밑에 */
.picks {{ display:flex; flex-direction:column; gap:8px; }}
.pk {{ display:flex; align-items:flex-start; gap:8px;
      background:var(--card); border:1px solid var(--line); border-radius:12px;
      padding:4px 12px 4px 4px; }}
.pk-d {{ flex:1 1 auto; min-width:0; }}
.pk-sum {{ display:flex; gap:9px; align-items:baseline; flex-wrap:wrap;
      padding:8px 10px; cursor:pointer; border-radius:8px; }}
.pk-sum:hover {{ background:var(--bg); }}
.pk-rank {{ display:inline-flex; align-items:center; justify-content:center;
      width:20px; height:20px; border-radius:999px; background:var(--line);
      font-size:.7rem; font-weight:700; flex:0 0 auto; }}
.pk-ticker {{ font-weight:700; font-size:1.05rem; }}
.pk-name {{ min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
.pk-body {{ padding:2px 10px 10px 39px; display:flex; flex-direction:column; gap:8px; }}
.pk-line {{ margin:0; font-size:.85rem; }}
.pk-why {{ margin:0; }}
.pk-why li {{ margin:2px 0; }}
.pk-care {{ border-top:1px dashed var(--line); padding-top:7px; }}
.pk-note {{ border-top:1px dashed var(--line); padding-top:7px; opacity:.72; }}
.tabs {{ display:flex; gap:6px; margin:0 0 14px; }}
.tab {{ display:inline-flex; align-items:center; gap:6px; padding:7px 15px;
      border:1px solid var(--line); border-radius:999px; background:var(--card);
      color:var(--muted); text-decoration:none; font-size:.87rem; font-weight:600; }}
.tab:hover {{ color:var(--fg); }}
.tab.on {{ background:var(--accent); border-color:transparent; color:#fff; }}
.tab-n {{ font-size:.75rem; opacity:.75; font-variant-numeric:tabular-nums; }}
.tab-open {{ font-size:.72rem; opacity:.85; font-weight:500; }}
.tab-warn {{ font-size:.7rem; padding:1px 6px; border-radius:999px;
      background:rgba(185,28,28,.14); color:var(--bad); }}
.tab.on .tab-warn {{ background:rgba(255,255,255,.22); color:#fff; }}
.f-orig {{ font-size:.74rem; color:var(--muted); margin:2px 0 0 2px; }}
.f-why {{ font-size:.78rem; margin:3px 0 0 2px; }}
.src-parts {{ margin:4px 0 2px 2px; }}
.src-parts > summary {{ cursor:pointer; font-size:.76rem; color:var(--muted); }}
.src-parts > summary:hover {{ color:var(--accent); }}
.pk-care-h {{ font-size:.75rem; font-weight:700; color:var(--muted); margin-bottom:3px; }}
.pk-act {{ flex:0 0 auto; padding-top:9px; }}
.pk-add button {{ font-size:.78rem; padding:4px 10px; }}
.fold > summary {{ display:block; cursor:pointer; list-style:none; }}
.fold > summary::-webkit-details-marker {{ display:none; }}
.fold > summary h2::before {{ content:"▾ "; color:var(--muted); font-weight:400; }}
.fold:not([open]) > summary h2::before {{ content:"▸ "; }}
.fold > summary:hover h2 {{ color:var(--accent); }}
.macro {{ display:grid; gap:12px; grid-template-columns:repeat(auto-fit,minmax(240px,1fr)); }}
.mi {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:13px 15px; }}
.mi-top {{ display:flex; gap:8px; align-items:baseline; justify-content:space-between; }}
.mi-name {{ font-weight:700; font-size:.88rem; }}
.mi-when {{ white-space:nowrap; }}
.mi-val {{ font-size:1.5rem; font-weight:700; font-variant-numeric:tabular-nums;
  display:flex; gap:9px; align-items:baseline; margin:4px 0 2px; }}
.mi-move {{ font-size:.78rem; font-weight:600; }}
.mi-move.good {{ color:var(--good); }}
.mi-move.bad {{ color:var(--bad); }}
.mi-move.flat {{ color:var(--muted); }}
.mi-read {{ font-size:.8rem; font-weight:600; color:var(--muted); }}

.glossary {{ display:grid; gap:18px; grid-template-columns:repeat(auto-fit,minmax(300px,1fr)); }}
details.g-group {{ border:1px solid var(--line); border-radius:10px; background:var(--card); }}
details.g-group > summary {{ padding:9px 13px; cursor:pointer; font-weight:700;
  font-size:.9rem; color:var(--fg); }}
details.g-group > summary:hover {{ color:var(--accent); }}
details.g-group[open] > summary {{ border-bottom:1px solid var(--line); }}
.g-items {{ padding:10px 12px; display:grid; gap:10px; }}
.g-item {{ background:var(--card); border:1px solid var(--line); border-radius:10px;
  padding:12px 14px; margin-bottom:10px; scroll-margin-top:20px; }}
.g-item h4 {{ margin:0 0 6px; color:var(--fg); font-size:.95rem; }}
.g-short {{ font-size:.85rem; margin-bottom:6px; }}
.g-row {{ font-size:.78rem; color:var(--muted); margin:4px 0; }}
.g-row.caution {{ color:var(--alert); }}
.g-key {{ display:inline-block; min-width:52px; font-weight:700; }}
code {{ background:var(--quote); padding:1px 5px; border-radius:4px; font-size:.9em; }}

footer {{ margin-top:36px; padding-top:16px; border-top:1px solid var(--line); font-size:.8rem; }}
@media (max-width:600px) {{
  body {{ padding:14px; }}
  .axes, .charts, .stats {{ grid-template-columns:1fr; }}
}}
</style></head>
<body>
{body}
<script src="/static/lightweight-charts.js"></script>
<script src="/static/live.js"></script>
</body></html>
"""
