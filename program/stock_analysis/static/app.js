// 화면에서 움직이는 것 전부. 쪽을 통째로 다시 불러오지 않는 것이 원칙이다 —
// 통째로 다시 불러오면 스크롤·펼친 칸·차트 확대가 다 풀린다.
(function () {
  'use strict';

  var LIVE_MS = 5000;        // 주가 칸을 새로 받는 간격
  var BUSY_MS = 1500;        // 작업 중일 때 진행 상황을 묻는 간격
  var IDLE_MS = 20000;       // 평소 '새로 들어온 게 있나' 를 묻는 간격

  var boot = readJson('boot') || {};
  var market = boot.market === 'kr' ? 'kr' : 'us';
  var here = boot.here || location.pathname + location.search;

  function readJson(id) {
    var el = document.getElementById(id);
    if (!el) { return null; }
    try { return JSON.parse(el.textContent); } catch (e) { return null; }
  }
  function store(kind, key, value) {
    try {
      var s = kind === 'session' ? sessionStorage : localStorage;
      if (value === undefined) { return s.getItem(key); }
      if (value === null) { s.removeItem(key); } else { s.setItem(key, value); }
    } catch (e) { return null; }
    return null;
  }
  function qs(sel, root) { return (root || document).querySelector(sel); }
  function qsa(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }
  function escapeHtml(text) {
    return String(text).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  // --- 밝기 -------------------------------------------------------------------
  var DAY_START = 7, DAY_END = 19;
  var THEME_ORDER = ['auto', 'light', 'dark', 'system'];
  var THEME_LABEL = { auto: '시간에 맞춰 (7시~19시 밝게)', light: '밝게', dark: '어둡게', system: '윈도우 설정대로' };
  function byClock() { var h = new Date().getHours(); return h >= DAY_START && h < DAY_END ? 'light' : 'dark'; }
  function currentTheme() { return store('local', 'theme') || 'auto'; }
  function applyTheme(choice) {
    var root = document.documentElement;
    if (choice === 'auto') { root.setAttribute('data-theme', byClock()); }
    else if (choice === 'system') { root.removeAttribute('data-theme'); }
    else { root.setAttribute('data-theme', choice); }
    var button = document.getElementById('themebtn');
    if (button) {
      var dark = root.getAttribute('data-theme') === 'dark' ||
        (!root.hasAttribute('data-theme') && window.matchMedia('(prefers-color-scheme: dark)').matches);
      button.innerHTML = dark ? ICON_MOON : ICON_SUN;
      button.title = '화면 밝기: ' + THEME_LABEL[choice] + ' (눌러서 바꾸기)';
    }
  }
  var ICON_SUN = '<svg class="i" viewBox="0 0 24 24"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>';
  var ICON_MOON = '<svg class="i" viewBox="0 0 24 24"><path d="M20 14.5A8 8 0 019.5 4 8 8 0 1020 14.5z"/></svg>';

  function wireTheme() {
    applyTheme(currentTheme());
    var button = document.getElementById('themebtn');
    if (button) {
      button.addEventListener('click', function () {
        var next = THEME_ORDER[(THEME_ORDER.indexOf(currentTheme()) + 1) % THEME_ORDER.length];
        store('local', 'theme', next);
        applyTheme(next);
        toast(THEME_LABEL[next], {});
      });
    }
    setInterval(function () { if (currentTheme() === 'auto') { applyTheme('auto'); } }, 60000);
  }

  // --- 알림 ---------------------------------------------------------------------
  var busyToast = null;
  function toast(text, opts) {
    opts = opts || {};
    var zone = document.getElementById('toasts');
    if (!zone || !text) { return null; }
    var el = document.createElement('div');
    el.className = 'toast' + (opts.bad ? ' bad' : '');
    el.innerHTML = (opts.busy ? '<span class="spin"></span>' : (opts.bad ? '⚠️' : '✅')) +
      '<span class="t-text">' + escapeHtml(text) + '</span>' +
      (opts.busy ? '' : '<button type="button" aria-label="닫기">×</button>');
    zone.appendChild(el);
    var close = el.querySelector('button');
    if (close) { close.addEventListener('click', function () { el.remove(); }); }
    if (!opts.busy && !opts.bad) { setTimeout(function () { el.remove(); }, opts.ms || 6000); }
    return el;
  }
  function showBusy(text) {
    if (!busyToast) { busyToast = toast(text, { busy: true }); return; }
    var t = busyToast.querySelector('.t-text');
    if (t && t.textContent !== text) { t.textContent = text; }
  }

  // --- 작업 상태 · 새 소식 ---------------------------------------------------
  var firstStamp = null, wasBusy = false, freshShown = false;
  function pollStatus() {
    fetch('/status', { cache: 'no-store' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (s) {
        if (!s) { return later(IDLE_MS); }
        if (s.busy) {
          wasBusy = true;
          showBusy(s.busy + ' — 끝나면 저절로 바뀝니다');
          return later(BUSY_MS);
        }
        if (wasBusy) { saveScroll(); location.reload(); return; }   // 끝났다 → 결과를 보여준다
        if (firstStamp === null) { firstStamp = s.stamp; }
        else if (s.stamp !== firstStamp && !freshShown) { onFresh(); }
        later(IDLE_MS);
      })
      .catch(function () { later(IDLE_MS); });
  }
  function later(ms) { setTimeout(pollStatus, ms); }
  function onFresh() {
    var quiet = window.scrollY < 80 && !document.querySelector('input:focus, textarea:focus, details[open].rank-row');
    if (quiet && /^\/(news|filings)?(\?|$)/.test(location.pathname + location.search)) {
      location.reload();
      return;
    }
    freshShown = true;
    var pill = document.createElement('button');
    pill.className = 'fresh-pill';
    pill.type = 'button';
    pill.textContent = '새 소식이 들어왔습니다 · 눌러서 새로고침';
    pill.addEventListener('click', function () { saveScroll(); location.reload(); });
    document.body.appendChild(pill);
  }

  // --- 숫자 갈아끼우기 --------------------------------------------------------
  function liveTickers() {
    var seen = {};
    qsa('[data-live][data-t]').forEach(function (el) { seen[el.getAttribute('data-t')] = 1; });
    qsa('.tv-chart[data-ticker]').forEach(function (el) { seen[el.getAttribute('data-ticker')] = 1; });
    return Object.keys(seen);
  }
  var lastPrice = {};
  function pollLive() {
    if (document.hidden) { return; }           // 안 보이는 탭은 쉬게 둔다
    var tickers = liveTickers();
    var url = '/live?m=' + market + '&t=' + encodeURIComponent(tickers.join(','));
    fetch(url, { cache: 'no-store' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (!data) { return; }
        var tape = qs('[data-live-tape]');
        if (tape && data.tape && tape.innerHTML !== data.tape) { tape.innerHTML = data.tape; }
        Object.keys(data.items || {}).forEach(function (ticker) {
          var item = data.items[ticker];
          var before = lastPrice[ticker];
          qsa('[data-live][data-t="' + CSS.escape(ticker) + '"]').forEach(function (el) {
            var field = el.getAttribute('data-live');
            var html = item[field];
            if (html === undefined || html === null || el.innerHTML === html) { return; }
            el.innerHTML = html;
            if (field === 'price' && before !== undefined && item.num !== null && item.num !== before) {
              el.classList.remove('flash-up', 'flash-down');
              void el.offsetWidth;                                 // 애니메이션을 다시 걸기 위해
              el.classList.add(item.num > before ? 'flash-up' : 'flash-down');
            }
          });
          if (item.num !== null && item.num !== undefined) { lastPrice[ticker] = item.num; }
          if (item.chg !== null && item.chg !== undefined) {              // 목록 왼쪽 띠 색 · 정렬 값
            qsa('.row[data-t="' + CSS.escape(ticker) + '"]').forEach(function (row) {
              row.classList.toggle('r-up', item.chg > 0);
              row.classList.toggle('r-down', item.chg < 0);
              row.setAttribute('data-chg', String(item.chg));
            });
          }
          if (item.bar && window.__charts && window.__charts[ticker]) { window.__charts[ticker].push(item.bar); }
        });
      })
      .catch(function () { /* 서버가 잠깐 바쁘면 다음 차례에 다시 */ });
  }

  // --- 나중에 받아 끼우는 칸 ---------------------------------------------------
  function loadLazy(el) {
    if (el.getAttribute('data-loading')) { return; }
    el.setAttribute('data-loading', '1');
    fetch(el.getAttribute('data-lazy'), { cache: 'no-store' })
      .then(function (r) { return r.ok ? r.text() : ''; })
      .then(function (html) { el.innerHTML = html; restoreFolds(el); })
      .catch(function () { el.innerHTML = '<div class="empty">불러오지 못했습니다. 새로고침하면 다시 시도합니다.</div>'; });
  }
  function wireLazy() {
    var items = qsa('[data-lazy]');
    if (!('IntersectionObserver' in window)) { items.forEach(loadLazy); return; }
    var seen = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) { if (e.isIntersecting) { seen.unobserve(e.target); loadLazy(e.target); } });
    }, { rootMargin: '800px 0px' });
    items.forEach(function (el) { seen.observe(el); });
  }

  // --- 검색 ---------------------------------------------------------------------
  function wireSearch() {
    var box = qs('[data-search]');
    if (!box) { return; }
    var input = box.querySelector('input');
    var menu = box.querySelector('.search-menu');
    var data = readJson('search-data') || { stocks: [], terms: [] };
    var active = -1, options = [];

    function addForm(ticker) {
      var f = document.createElement('form');
      f.method = 'post'; f.action = '/action';
      [['action', 'add'], ['ticker', ticker], ['back', here]].forEach(function (pair) {
        var i = document.createElement('input'); i.type = 'hidden'; i.name = pair[0]; i.value = pair[1]; f.appendChild(i);
      });
      document.body.appendChild(f);
      f.submit();
    }
    function render() {
      var q = input.value.trim();
      var low = q.toLowerCase();
      if (!q) { menu.classList.add('hide'); return; }
      var stocks = data.stocks.filter(function (s) {
        return s.t.toLowerCase().indexOf(low) >= 0 || s.d.toLowerCase().indexOf(low) >= 0 ||
               (s.n || '').toLowerCase().indexOf(low) >= 0;
      }).slice(0, 8);
      var terms = data.terms.filter(function (t) { return t.n.toLowerCase().indexOf(low) >= 0; }).slice(0, 4);
      var html = '';
      options = [];
      stocks.forEach(function (s) {
        options.push({ href: '/stock/' + encodeURIComponent(s.t) });
        html += '<a href="/stock/' + encodeURIComponent(s.t) + '"><b>' + escapeHtml(s.d) + '</b>' +
          '<span class="muted">' + escapeHtml(s.n || '') + '</span><span class="sm-kind">' +
          (s.m === 'kr' ? '한국' : '미국') + ' · 관심 종목</span></a>';
      });
      terms.forEach(function (t) {
        options.push({ href: '/glossary#term-' + t.k });
        html += '<a href="/glossary#term-' + encodeURIComponent(t.k) + '">📖 ' + escapeHtml(t.n) +
          '<span class="sm-kind">용어</span></a>';
      });
      var exact = data.stocks.some(function (s) { return s.t.toLowerCase() === low || s.d.toLowerCase() === low; });
      if (!exact && q.length <= 24) {
        options.push({ add: q });
        html += '<button type="button" data-add-q>＋ <b>' + escapeHtml(q) + '</b> 관심 종목에 추가' +
          '<span class="sm-kind">티커 · 종목 코드 · 한글 회사 이름</span></button>';
      }
      if (!html) { html = '<div class="sm-empty">찾는 것이 없습니다.</div>'; }
      menu.innerHTML = html;
      menu.classList.remove('hide');
      active = -1;
      var add = menu.querySelector('[data-add-q]');
      if (add) { add.addEventListener('click', function () { addForm(q); }); }
    }
    function mark() {
      qsa('a, button', menu).forEach(function (el, i) { el.classList.toggle('on', i === active); });
    }
    input.addEventListener('input', render);
    input.addEventListener('focus', render);
    input.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowDown') { active = Math.min(active + 1, options.length - 1); mark(); e.preventDefault(); }
      else if (e.key === 'ArrowUp') { active = Math.max(active - 1, 0); mark(); e.preventDefault(); }
      else if (e.key === 'Escape') { menu.classList.add('hide'); input.blur(); }
      else if (e.key === 'Enter') {
        e.preventDefault();
        var pick = options[active >= 0 ? active : 0];
        if (!pick) { return; }
        if (pick.add) { addForm(pick.add); } else { location.href = pick.href; }
      }
    });
    document.addEventListener('click', function (e) { if (!box.contains(e.target)) { menu.classList.add('hide'); } });
    document.addEventListener('keydown', function (e) {
      var typing = /input|textarea|select/i.test((document.activeElement || {}).tagName || '');
      if (e.key === '/' && !typing) { e.preventDefault(); input.focus(); }
    });
  }

  // --- 새로고침 메뉴 -----------------------------------------------------------
  function wireMenus() {
    qsa('[data-menu]').forEach(function (menu) {
      var button = menu.querySelector('button');
      var pop = menu.querySelector('.menu-pop');
      button.addEventListener('click', function (e) { e.stopPropagation(); pop.classList.toggle('hide'); });
      document.addEventListener('click', function (e) { if (!menu.contains(e.target)) { pop.classList.add('hide'); } });
    });
  }

  // --- 홈: 보기 전환 · 정렬 · 추가 ----------------------------------------------
  function wireHome() {
    var switcher = qs('[data-view-switch]');
    if (switcher) {
      var set = function (view) {
        qsa('button', switcher).forEach(function (b) { b.classList.toggle('on', b.getAttribute('data-view') === view); });
        qsa('[data-view-pane]').forEach(function (p) { p.classList.toggle('hide', p.getAttribute('data-view-pane') !== view); });
        var sort = qs('[data-sort]');
        if (sort) { sort.classList.toggle('hide', view !== 'list'); }
        store('local', 'view', view);
      };
      qsa('button', switcher).forEach(function (b) {
        b.addEventListener('click', function () { set(b.getAttribute('data-view')); });
      });
      set(store('local', 'view') === 'table' ? 'table' : 'list');
    }
    var sorter = qs('[data-sort]');
    var list = qs('[data-view-pane="list"]');
    if (sorter && list) {
      var original = qsa('.row', list);
      var apply = function (how) {
        var rows = original.slice();
        var chg = function (r) { var v = parseFloat(r.getAttribute('data-chg')); return isNaN(v) ? null : v; };
        if (how === 'up' || how === 'down') {
          rows.sort(function (a, b) {
            var x = chg(a), y = chg(b);
            if (x === null) { return 1; }            // 등락을 모르는 종목은 늘 뒤로
            if (y === null) { return -1; }
            return how === 'up' ? y - x : x - y;
          });
        } else if (how === 'name') {
          rows.sort(function (a, b) { return (a.getAttribute('data-name') || '').localeCompare(b.getAttribute('data-name') || '', 'ko'); });
        }
        rows.forEach(function (r) { list.appendChild(r); });
        qsa('[data-sort-by]', sorter).forEach(function (b) { b.classList.toggle('on', b.getAttribute('data-sort-by') === how); });
        store('local', 'sort', how);
      };
      qsa('[data-sort-by]', sorter).forEach(function (b) {
        b.addEventListener('click', function () { apply(b.getAttribute('data-sort-by')); });
      });
      apply(store('local', 'sort') || 'none');
    }
    qsa('[data-add]').forEach(function (box) {
      var open = box.querySelector('[data-add-open]');
      var form = box.querySelector('[data-add-form]');
      if (!open || !form) { return; }
      open.addEventListener('click', function () {
        form.classList.remove('hide');
        open.classList.add('hide');
        var input = form.querySelector('input[type=text]');
        if (input) { input.focus(); }
      });
    });
  }

  // --- 종목 화면 칸 이동 --------------------------------------------------------
  function wireTabs() {
    var nav = qs('[data-tabs]');
    if (!nav) { return; }
    var links = qsa('a[data-tab]', nav);
    var sections = links.map(function (a) { return document.getElementById(a.getAttribute('data-tab')); });
    links.forEach(function (a) {
      a.addEventListener('click', function (e) {
        var target = document.getElementById(a.getAttribute('data-tab'));
        if (!target) { return; }
        e.preventDefault();
        var top = target.getBoundingClientRect().top + window.scrollY - 130;
        window.scrollTo({ top: top, behavior: 'smooth' });
        history.replaceState(null, '', '#' + a.getAttribute('data-tab'));
      });
    });
    var spy = function () {
      var current = 0;
      sections.forEach(function (s, i) { if (s && s.getBoundingClientRect().top < 160) { current = i; } });
      links.forEach(function (a, i) { a.classList.toggle('on', i === current); });
    };
    window.addEventListener('scroll', spy, { passive: true });
    spy();
  }

  // --- 접은 칸 기억 · 스크롤 기억 ------------------------------------------------
  function foldKey(el) { return 'fold:' + el.getAttribute('data-keep'); }
  function restoreFolds(root) {
    qsa('details[data-keep]', root).forEach(function (el) {
      var saved = store('local', foldKey(el));
      if (saved === '1') { el.open = true; } else if (saved === '0') { el.open = false; }
      el.addEventListener('toggle', function () { store('local', foldKey(el), el.open ? '1' : '0'); });
    });
  }
  function scrollKey() { return 'scroll:' + location.pathname + location.search; }
  function saveScroll() { store('session', scrollKey(), String(window.scrollY)); }
  function restoreScroll() {
    var saved = store('session', scrollKey());
    if (saved && !location.hash) { window.scrollTo(0, parseInt(saved, 10) || 0); }
    store('session', scrollKey(), null);
    window.addEventListener('beforeunload', saveScroll);
  }

  // --- 사전 찾기 -----------------------------------------------------------------
  function wireGlossary() {
    var box = qs('[data-glossary-filter] input');
    if (!box) { return; }
    box.addEventListener('input', function () {
      var q = box.value.trim().toLowerCase();
      qsa('.g-item').forEach(function (el) {
        el.classList.toggle('hide', q && (el.getAttribute('data-text') || '').indexOf(q) < 0);
      });
      qsa('[data-group]').forEach(function (g) {
        g.classList.toggle('hide', !g.querySelector('.g-item:not(.hide)'));
      });
    });
  }

  // --- 단추를 누르면 곧바로 반응 -------------------------------------------------
  function wireForms() {
    document.addEventListener('submit', function (e) {
      var form = e.target;
      if (!(form instanceof HTMLFormElement) || form.method.toLowerCase() !== 'post') { return; }
      saveScroll();
      var button = form.querySelector('button[type=submit]');
      if (button) { setTimeout(function () { button.disabled = true; }, 0); }  // 두 번 눌러 두 번 도는 일을 막는다
    }, true);
  }

  document.addEventListener('DOMContentLoaded', function () {
    wireTheme();
    restoreFolds(document);
    restoreScroll();
    wireSearch();
    wireMenus();
    wireHome();
    wireTabs();
    wireLazy();
    wireGlossary();
    wireForms();
    var n = boot.notice;
    if (n && n.busy) { showBusy(n.busy + ' — 끝나면 저절로 바뀝니다'); wasBusy = true; if (n.text) { toast(n.text, {}); } }
    else if (n && n.text) { toast(n.text, { bad: !!n.bad, ms: 8000 }); }
    setTimeout(pollStatus, n && n.busy ? BUSY_MS : 3000);
    if (liveTickers().length || qs('[data-live-tape]')) { setInterval(pollLive, LIVE_MS); }
  });
})();
