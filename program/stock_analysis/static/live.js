// 화면을 통째로 다시 불러오지 않고, 바뀌는 칸만 몇 초마다 갈아끼운다.
//
// 통째로 다시 불러오면 스크롤·펼친 칸·차트 확대가 전부 풀린다. 그런데 몇
// 초마다 바뀌는 건 주가 몇 칸뿐이다. 그래서 그 칸만 받아온다(/live).
//
// 차트는 TradingView Lightweight Charts 로 그린다. 그림만 그리는 무료 공개
// 라이브러리이고, 숫자는 이 프로그램이 받아둔 것을 쓴다. 라이브러리를
// 못 불러오면 서버가 그려둔 캔들 그림(SVG)이 그대로 남는다.
(function () {
  'use strict';

  var POLL_MS = 5000;             // 화면이 숫자를 받아가는 간격
  var SHOW_BARS = 120;            // 처음 보이는 봉 수 (휠로 더 넓힐 수 있다)
  var charts = {};                // 종목 → 차트 상태

  function market() {
    var m = new URLSearchParams(location.search).get('m');
    return m === 'kr' ? 'kr' : 'us';
  }

  function css(name, fallback) {
    var v = getComputedStyle(document.documentElement).getPropertyValue(name);
    return (v && v.trim()) || fallback;
  }

  function hasLiveCells() {
    return document.querySelector('[data-live-price],[data-live-title],.tv-chart');
  }

  // --- 숫자 갈아끼우기 ------------------------------------------------------
  function swap(selector, html) {
    if (html === undefined || html === null) { return; }
    var nodes = document.querySelectorAll(selector);
    for (var i = 0; i < nodes.length; i++) {
      if (nodes[i].innerHTML !== html) { nodes[i].innerHTML = html; }
    }
  }

  function poll() {
    if (document.hidden || !hasLiveCells()) { return; }   // 안 보이는 탭은 쉬게 둔다
    fetch('/live?m=' + market(), { cache: 'no-store' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (!data || !data.items) { return; }
        swap('[data-live-strip]', data.strip);
        Object.keys(data.items).forEach(function (ticker) {
          var item = data.items[ticker];
          var key = CSS.escape(ticker);
          swap('[data-live-price="' + key + '"]', item.price);
          swap('[data-live-spark="' + key + '"]', item.spark);
          swap('[data-live-title="' + key + '"]', item.title);
          if (item.bar && charts[ticker]) { charts[ticker].push(item.bar); }
        });
      })
      .catch(function () { /* 서버가 잠깐 바쁘면 다음 차례에 다시 */ });
  }

  // --- 차트 -----------------------------------------------------------------
  function average(closes, window, index) {
    if (index < window - 1) { return null; }        // 창이 다 차기 전에는 내지 않는다
    var sum = 0;
    for (var i = index - window + 1; i <= index; i++) { sum += closes[i]; }
    return sum / window;
  }

  function volumeColor(bar, up, down) {
    return (bar.close >= bar.open ? up : down) + '66';   // 거래량은 옅게
  }

  function money(value, currency) {
    if (value === null || value === undefined) { return '-'; }
    if (currency === 'KRW') {
      return Math.round(value).toLocaleString('ko-KR') + '원';
    }
    return '$' + value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  function volumeText(v) {
    if (v === null || v === undefined) { return '-'; }
    if (v >= 1e8) { return (v / 1e8).toFixed(2) + '억'; }
    if (v >= 1e4) { return (v / 1e4).toFixed(1) + '만'; }
    return Math.round(v).toLocaleString('ko-KR');
  }

  // 색은 화면의 CSS 변수에서 가져온다 — 표의 초록·빨강과 같은 색이어야 한다.
  function palette() {
    return { up: css('--good', '#15803d'), down: css('--bad', '#b91c1c'),
             line: css('--line', '#e5e7eb'), muted: css('--muted', '#6b7280') };
  }

  function candleColors(c) {
    return { upColor: c.up, downColor: c.down, borderUpColor: c.up, borderDownColor: c.down,
             wickUpColor: c.up, wickDownColor: c.down };
  }

  function build(box, data) {
    if (!window.LightweightCharts || !data.bars || data.bars.length < 5) { return; }
    var colors = palette();
    var up = colors.up;
    var down = colors.down;
    var krw = data.currency === 'KRW';
    var canvas = box.querySelector('.tv-canvas');
    var legend = box.querySelector('.tv-legend');

    var chart = LightweightCharts.createChart(canvas, {
      autoSize: true,
      layout: { background: { color: 'transparent' }, textColor: css('--muted', '#6b7280'),
                fontSize: 11 },
      grid: { vertLines: { color: css('--line', '#e5e7eb') },
              horzLines: { color: css('--line', '#e5e7eb') } },
      rightPriceScale: { borderColor: css('--line', '#e5e7eb') },
      timeScale: { borderColor: css('--line', '#e5e7eb'), rightOffset: 3 },
      crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
      localization: { locale: 'ko-KR', dateFormat: 'yyyy-MM-dd',
                      priceFormatter: function (p) { return krw ? Math.round(p).toLocaleString('ko-KR')
                                                                : p.toFixed(2); } }
    });

    var candle = chart.addCandlestickSeries(Object.assign(candleColors(colors), {
      priceFormat: krw ? { type: 'price', precision: 0, minMove: 1 }
                       : { type: 'price', precision: 2, minMove: 0.01 }
    }));
    var volume = chart.addHistogramSeries({ priceFormat: { type: 'volume' }, priceScaleId: '',
                                            lastValueVisible: false, priceLineVisible: false });
    volume.priceScale().applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
    var ma20 = chart.addLineSeries({ color: '#f59e0b', lineWidth: 1, priceLineVisible: false,
                                     lastValueVisible: false, crosshairMarkerVisible: false });
    var ma60 = chart.addLineSeries({ color: '#3b82f6', lineWidth: 1, priceLineVisible: false,
                                     lastValueVisible: false, crosshairMarkerVisible: false });

    var bars = data.bars.slice();
    candle.setData(bars);
    // 거래량이 없는 날은 막대를 그리지 않는다 — 0 으로 그리면 '거래 없음' 이 된다.
    volume.setData(bars.filter(function (b) { return b.volume !== undefined; })
      .map(function (b) { return { time: b.time, value: b.volume, color: volumeColor(b, up, down) }; }));
    ma20.setData(data.ma20 || []);
    ma60.setData(data.ma60 || []);

    var n = bars.length;
    chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, n - SHOW_BARS), to: n + 3 });

    var byTime = {};
    bars.forEach(function (b, i) { byTime[b.time] = i; });

    function describe(bar, index) {
      if (!bar) { return ''; }
      var prev = index > 0 ? bars[index - 1].close : null;
      var change = prev ? (bar.close - prev) / prev * 100 : null;
      var cls = change === null ? 'flat' : (change > 0 ? 'up' : (change < 0 ? 'down' : 'flat'));
      var live = index === bars.length - 1 && box.dataset.live === '1';
      var m20 = average(bars.map(function (b) { return b.close; }), 20, index);
      var m60 = average(bars.map(function (b) { return b.close; }), 60, index);
      return '<b>' + bar.time + (live ? ' · 장중' : '') + '</b>' +
        ' 시 ' + money(bar.open, data.currency) +
        ' 고 ' + money(bar.high, data.currency) +
        ' 저 ' + money(bar.low, data.currency) +
        ' 종 <b>' + money(bar.close, data.currency) + '</b>' +
        (change === null ? '' : ' <span class="' + cls + '">' + (change > 0 ? '+' : '') +
                                change.toFixed(2) + '%</span>') +
        ' · 거래량 ' + volumeText(bar.volume) +
        ' <span class="tv-ma20">MA20 ' + (m20 === null ? '-' : money(m20, data.currency)) + '</span>' +
        ' <span class="tv-ma60">MA60 ' + (m60 === null ? '-' : money(m60, data.currency)) + '</span>';
    }

    legend.innerHTML = describe(bars[n - 1], n - 1);
    var hovering = false;       // 사람이 봉을 짚어 보는 중이면 갱신이 범례를 가로채지 않는다
    chart.subscribeCrosshairMove(function (param) {
      if (!param || !param.time) {
        hovering = false;
        legend.innerHTML = describe(bars[bars.length - 1], bars.length - 1);
        return;
      }
      hovering = true;
      var i = byTime[param.time];
      legend.innerHTML = describe(bars[i], i);
    });

    box.classList.add('tv-ready');     // 서버가 그린 그림(SVG)을 치운다

    // 밝게/어둡게를 바꾸면 차트 색도 따라간다. 안 그러면 어두운 화면에
    // 밝은 격자가 남는다.
    new MutationObserver(function () {
      var c = palette();
      up = c.up; down = c.down;
      chart.applyOptions({
        layout: { textColor: c.muted },
        grid: { vertLines: { color: c.line }, horzLines: { color: c.line } },
        rightPriceScale: { borderColor: c.line }, timeScale: { borderColor: c.line }
      });
      candle.applyOptions(candleColors(c));
      volume.setData(bars.filter(function (b) { return b.volume !== undefined; })
        .map(function (b) { return { time: b.time, value: b.volume, color: volumeColor(b, up, down) }; }));
    }).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });

    charts[data.ticker] = {
      colors: function () { return candle.options(); },
      // 오늘 봉이 바뀌면 그 봉만 고친다. 날이 바뀌었으면 새 봉을 붙인다.
      push: function (bar) {
        var last = bars[bars.length - 1];
        if (last && bar.time < last.time) { return; }          // 더 옛 값이 늦게 온 것
        if (last && bar.time === last.time) { bars[bars.length - 1] = bar; }
        else { bars.push(bar); byTime[bar.time] = bars.length - 1; }
        candle.update(bar);
        if (bar.volume !== undefined) {
          volume.update({ time: bar.time, value: bar.volume, color: volumeColor(bar, up, down) });
        }
        var closes = bars.map(function (b) { return b.close; });
        var i = bars.length - 1;
        var a20 = average(closes, 20, i), a60 = average(closes, 60, i);
        if (a20 !== null) { ma20.update({ time: bar.time, value: a20 }); }
        if (a60 !== null) { ma60.update({ time: bar.time, value: a60 }); }
        if (!hovering) { legend.innerHTML = describe(bars[i], i); }
      }
    };
  }

  function load(box) {
    if (box.dataset.loading) { return; }
    box.dataset.loading = '1';
    fetch('/bars?t=' + encodeURIComponent(box.dataset.ticker), { cache: 'no-store' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) { if (data) { build(box, data); } })
      .catch(function () { /* 그림(SVG)이 그대로 남는다 */ });
  }

  // 접힌 카드 안에서는 차트 크기를 잴 수 없다. 펼쳐질 때 만든다.
  function wire() {
    var boxes = document.querySelectorAll('.tv-chart[data-ticker]');
    for (var i = 0; i < boxes.length; i++) {
      (function (box) {
        // 겹겹이 접힌 칸 안에 있을 수 있다. 가장 가까운 칸만 보면 바깥이
        // 접혀 있어도 폭 0 짜리 차트를 만든다. 실제로 보이는지로 가린다.
        if (box.offsetParent !== null) { load(box); }
        var parent = box.closest('details');
        while (parent) {
          parent.addEventListener('toggle', function () {
            if (box.offsetParent !== null) { load(box); }
          });
          parent = parent.parentElement ? parent.parentElement.closest('details') : null;
        }
      })(boxes[i]);
    }
  }

  window.__charts = charts;         // 확인용(브라우저 시험에서 색을 잰다)

  document.addEventListener('DOMContentLoaded', function () {
    wire();
    setInterval(poll, POLL_MS);
  });
})();
