// 종목 화면의 일봉 차트. TradingView Lightweight Charts 로 그린다.
//
// 그림만 그리는 무료 공개 라이브러리(Apache-2.0)이고, 숫자는 이 프로그램이 받아둔
// 것을 쓴다(/bars). 라이브러리를 못 불러오면 서버가 그려둔 캔들 그림(SVG)이 남는다.
(function () {
  'use strict';

  var charts = {};
  var MA_COLORS = { ma20: '#f59f00', ma60: '#3b82f6', ma120: '#a855f7' };

  function css(name, fallback) {
    var v = getComputedStyle(document.documentElement).getPropertyValue(name);
    return (v && v.trim()) || fallback;
  }
  function palette() {
    return { up: css('--up', '#0e9f55'), down: css('--down', '#e5484d'),
             line: css('--line', '#e8eaef'), muted: css('--muted', '#8b95a1') };
  }
  function candleColors(c) {
    return { upColor: c.up, downColor: c.down, borderUpColor: c.up, borderDownColor: c.down,
             wickUpColor: c.up, wickDownColor: c.down };
  }
  function average(closes, window, index) {
    if (index < window - 1) { return null; }       // 창이 다 차기 전에는 내지 않는다
    var sum = 0;
    for (var i = index - window + 1; i <= index; i++) { sum += closes[i]; }
    return sum / window;
  }
  function money(value, currency) {
    if (value === null || value === undefined) { return '-'; }
    if (currency === 'KRW') { return Math.round(value).toLocaleString('ko-KR') + '원'; }
    return '$' + value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  function volumeText(v) {
    if (v === null || v === undefined) { return '-'; }
    if (v >= 1e8) { return (v / 1e8).toFixed(2) + '억'; }
    if (v >= 1e4) { return (v / 1e4).toFixed(1) + '만'; }
    return Math.round(v).toLocaleString('ko-KR');
  }

  function build(box, data) {
    if (!window.LightweightCharts || !data.bars || data.bars.length < 5) { return; }
    var colors = palette();
    var krw = data.currency === 'KRW';
    var canvas = box.querySelector('.tv-canvas');
    var legend = box.querySelector('.tv-legend');
    var card = box.closest('.chart-card') || document;

    var chart = LightweightCharts.createChart(canvas, {
      autoSize: true,
      layout: { background: { color: 'transparent' }, textColor: colors.muted, fontSize: 11,
                fontFamily: getComputedStyle(document.body).fontFamily },
      grid: { vertLines: { color: colors.line }, horzLines: { color: colors.line } },
      rightPriceScale: { borderColor: colors.line },
      timeScale: { borderColor: colors.line, rightOffset: 3 },
      crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
      localization: { locale: 'ko-KR', dateFormat: 'yyyy-MM-dd',
                      priceFormatter: function (p) { return krw ? Math.round(p).toLocaleString('ko-KR') : p.toFixed(2); } }
    });
    var candle = chart.addCandlestickSeries(Object.assign(candleColors(colors), {
      priceFormat: krw ? { type: 'price', precision: 0, minMove: 1 } : { type: 'price', precision: 2, minMove: 0.01 }
    }));
    var volume = chart.addHistogramSeries({ priceFormat: { type: 'volume' }, priceScaleId: '',
                                            lastValueVisible: false, priceLineVisible: false });
    volume.priceScale().applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
    var lines = {};
    Object.keys(MA_COLORS).forEach(function (key) {
      lines[key] = chart.addLineSeries({ color: MA_COLORS[key], lineWidth: 1, priceLineVisible: false,
                                         lastValueVisible: false, crosshairMarkerVisible: false });
      lines[key].setData(data[key] || []);
    });

    var bars = data.bars.slice();
    var up = colors.up, down = colors.down;
    function volumeBars() {
      // 거래량이 없는 날은 막대를 그리지 않는다 — 0 으로 그리면 '거래 없음' 이 된다.
      return bars.filter(function (b) { return b.volume !== undefined; }).map(function (b) {
        return { time: b.time, value: b.volume, color: (b.close >= b.open ? up : down) + '66' };
      });
    }
    candle.setData(bars);
    volume.setData(volumeBars());

    var byTime = {};
    bars.forEach(function (b, i) { byTime[b.time] = i; });

    function describe(index) {
      var bar = bars[index];
      if (!bar) { return ''; }
      var prev = index > 0 ? bars[index - 1].close : null;
      var change = prev ? (bar.close - prev) / prev * 100 : null;
      var cls = change === null ? 'flat' : (change > 0 ? 'up' : (change < 0 ? 'down' : 'flat'));
      var live = index === bars.length - 1 && box.getAttribute('data-live') === '1';
      var closes = bars.map(function (b) { return b.close; });
      var mas = Object.keys(MA_COLORS).filter(function (k) { return shown[k]; }).map(function (k) {
        var n = parseInt(k.slice(2), 10);
        var v = average(closes, n, index);
        return ' <span style="color:' + MA_COLORS[k] + '">MA' + n + ' ' + (v === null ? '-' : money(v, data.currency)) + '</span>';
      }).join('');
      return '<b>' + bar.time + (live ? ' · 장중' : '') + '</b>' +
        ' 시 ' + money(bar.open, data.currency) + ' 고 ' + money(bar.high, data.currency) +
        ' 저 ' + money(bar.low, data.currency) + ' 종 <b>' + money(bar.close, data.currency) + '</b>' +
        (change === null ? '' : ' <span class="' + cls + '">' + (change > 0 ? '+' : '') + change.toFixed(2) + '%</span>') +
        ' · 거래량 ' + volumeText(bar.volume) + mas;
    }

    // 이동평균 켜고 끄기
    var shown = { ma20: true, ma60: true, ma120: false };
    Array.prototype.forEach.call(card.querySelectorAll('[data-ma-key]'), function (b) {
      var key = b.getAttribute('data-ma-key');
      var saved = null;
      try { saved = localStorage.getItem('ma:' + key); } catch (e) {}
      if (saved !== null) { shown[key] = saved === '1'; }
      b.classList.toggle('on', shown[key]);
      lines[key].applyOptions({ visible: shown[key] });
      b.addEventListener('click', function () {
        shown[key] = !shown[key];
        b.classList.toggle('on', shown[key]);
        lines[key].applyOptions({ visible: shown[key] });
        try { localStorage.setItem('ma:' + key, shown[key] ? '1' : '0'); } catch (e) {}
        legend.innerHTML = describe(bars.length - 1);
      });
    });

    // 기간 단추. 봉 수로 자른다(거래일 기준). '전체' 는 받아둔 봉 전부.
    function showDays(days) {
      var n = bars.length;
      var from = days > 0 ? Math.max(0, n - days) : 0;
      chart.timeScale().setVisibleLogicalRange({ from: from, to: n + 3 });
    }
    var rangeButtons = card.querySelectorAll('[data-range] button');
    var savedRange = null;
    try { savedRange = localStorage.getItem('range'); } catch (e) {}
    Array.prototype.forEach.call(rangeButtons, function (b) {
      if (savedRange !== null) { b.classList.toggle('on', b.getAttribute('data-days') === savedRange); }
      b.addEventListener('click', function () {
        Array.prototype.forEach.call(rangeButtons, function (x) { x.classList.toggle('on', x === b); });
        showDays(parseInt(b.getAttribute('data-days'), 10));
        try { localStorage.setItem('range', b.getAttribute('data-days')); } catch (e) {}
      });
    });
    var current = card.querySelector('[data-range] button.on');
    showDays(current ? parseInt(current.getAttribute('data-days'), 10) : 252);

    legend.innerHTML = describe(bars.length - 1);
    var hovering = false;         // 봉을 짚어 보는 중이면 갱신이 범례를 가로채지 않는다
    chart.subscribeCrosshairMove(function (param) {
      if (!param || !param.time) { hovering = false; legend.innerHTML = describe(bars.length - 1); return; }
      hovering = true;
      legend.innerHTML = describe(byTime[param.time]);
    });

    box.classList.add('tv-ready');     // 서버가 그린 그림(SVG)을 치운다

    // 밝게/어둡게를 바꾸면 차트 색도 따라간다.
    new MutationObserver(function () {
      var c = palette();
      up = c.up; down = c.down;
      chart.applyOptions({ layout: { textColor: c.muted },
                           grid: { vertLines: { color: c.line }, horzLines: { color: c.line } },
                           rightPriceScale: { borderColor: c.line }, timeScale: { borderColor: c.line } });
      candle.applyOptions(candleColors(c));
      volume.setData(volumeBars());
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
          volume.update({ time: bar.time, value: bar.volume, color: (bar.close >= bar.open ? up : down) + '66' });
        }
        var closes = bars.map(function (b) { return b.close; });
        var i = bars.length - 1;
        Object.keys(MA_COLORS).forEach(function (k) {
          var v = average(closes, parseInt(k.slice(2), 10), i);
          if (v !== null) { lines[k].update({ time: bar.time, value: v }); }
        });
        if (!hovering) { legend.innerHTML = describe(i); }
      }
    };
  }

  function load(box) {
    if (box.getAttribute('data-loading')) { return; }
    box.setAttribute('data-loading', '1');
    fetch('/bars?t=' + encodeURIComponent(box.getAttribute('data-ticker')), { cache: 'no-store' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) { if (data) { build(box, data); } })
      .catch(function () { /* 그림(SVG)이 그대로 남는다 */ });
  }

  window.__charts = charts;
  document.addEventListener('DOMContentLoaded', function () {
    Array.prototype.forEach.call(document.querySelectorAll('.tv-chart[data-ticker]'), load);
  });
})();
