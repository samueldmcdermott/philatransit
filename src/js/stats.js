'use strict';

// ── Statistics ─────────────────────────────────────────────────────────────

// Chart zoom/pan state
let chartState = {
  minX: 5 * 60, maxX: 24 * 60,  // visible range in minutes
  fullMinX: 5 * 60, fullMaxX: 24 * 60,
  dragging: false, dragStartX: null, dragStartMin: null,
  selecting: false, selectStartX: null, selectEndX: null,
  cachedActual: [], cachedSched: [], cachedDate: null,
};

async function loadStats() {
  if (!selectedRoute) return;
  document.getElementById('emptyStats').style.display   = 'none';
  document.getElementById('statsContent').style.display = '';
  try {
    const data = await apiFetch('/api/stats');
    renderStats(data);
  } catch (e) { setStatus('Stats error: ' + e.message); }
}

function mergeRouteDays(...routeObjs) {
  const merged = {};
  for (const routes of routeObjs) {
    for (const [day, trips] of Object.entries(routes)) {
      if (!merged[day]) merged[day] = [];
      merged[day].push(...trips);
    }
  }
  return merged;
}

function mergeScheduleMins(...gtfsIds) {
  // Merge schedule minute arrays, returning a combined object keyed by day type
  const merged = {};
  for (const id of gtfsIds) {
    const sched = scheduleData[id];
    if (!sched) continue;
    for (const dt of ['weekday', 'saturday', 'sunday']) {
      if (!sched[dt]) continue;
      if (!merged[dt]) merged[dt] = [];
      merged[dt].push(...sched[dt]);
    }
  }
  // Sort each
  for (const dt of Object.keys(merged)) merged[dt].sort((a, b) => a - b);
  return merged;
}

function renderStats(data) {
  const isMulti = selectedRoute.multi && selectedRoute.apiIds;
  const routes = isMulti
    ? mergeRouteDays(...selectedRoute.apiIds.map(id => data[id] || {}))
    : (data[selectedRoute.id] || {});
  const effectiveGtfs = isMulti ? '_T-ALL' : selectedRoute.gtfs;
  // Temporarily inject merged schedule data for T-ALL
  if (isMulti && !scheduleData['_T-ALL']) {
    scheduleData['_T-ALL'] = mergeScheduleMins(...selectedRoute.apiIds);
  }
  const today   = new Date(), todayStr = fmtDate(today);

  // ── count helpers ──
  const weekStart = new Date(today); weekStart.setDate(today.getDate() - today.getDay());
  const weekStr = fmtDate(weekStart);

  const todayTrips = routes[todayStr] || [];
  let weekCount = 0, allCount = 0;
  for (const [day, trips] of Object.entries(routes)) {
    allCount += trips.length;
    if (day >= weekStr) weekCount += trips.length;
  }
  document.getElementById('sToday').textContent = todayTrips.length;
  document.getElementById('sWeek').textContent  = weekCount;
  document.getElementById('sAll').textContent   = allCount;

  // ── yesterday ──
  const yesterday = new Date(today);
  yesterday.setDate(today.getDate() - 1);
  const yStr = fmtDate(yesterday);
  const yTrips = (routes[yStr] || []).length;
  const yDt = dayType(yesterday);
  const ySched = getScheduleCount(effectiveGtfs, yDt);
  document.getElementById('sYesterday').textContent = ySched > 0 ? `${yTrips}/${ySched}` : yTrips;

  // ── last N days ──
  renderLastNDays(routes, today, effectiveGtfs);

  // ── percentiles (actual) ──
  const durs = todayTrips.filter(t => typeof t === 'object' && t.dur > 0)
    .map(t => t.dur / 60000).sort((a, b) => a - b);
  if (durs.length >= 3) {
    document.getElementById('p25').textContent = pctFmt(percentile(durs, 25));
    document.getElementById('p50').textContent = pctFmt(percentile(durs, 50));
    document.getElementById('p75').textContent = pctFmt(percentile(durs, 75));
    document.getElementById('pctRow').style.display = '';
  } else {
    document.getElementById('pctRow').style.display = 'none';
  }

  // ── scheduled duration ──
  const schedDur = getScheduledDuration(effectiveGtfs, dayType(today));
  const schedEl = document.getElementById('schedDur');
  if (schedDur && schedDur > 0) {
    schedEl.textContent = pctFmt(schedDur);
    document.getElementById('schedDurRow').style.display = '';
  } else {
    document.getElementById('schedDurRow').style.display = 'none';
  }

  // ── chart ──
  chartState.minX = chartState.fullMinX;
  chartState.maxX = chartState.fullMaxX;
  drawChart(todayTrips, effectiveGtfs, today);
  initChartInteraction();
}

function getScheduleCount(gtfsId, dt) {
  const mins = scheduleData[gtfsId]?.[dt];
  return mins ? mins.length : 0;
}

function getScheduledDuration(gtfsId, dt) {
  // Estimate scheduled trip duration from schedule intervals
  // Use median gap between consecutive departures as a proxy
  const mins = (scheduleData[gtfsId]?.[dt] || []).slice().sort((a, b) => a - b);
  if (mins.length < 2) return null;
  const gaps = [];
  for (let i = 1; i < mins.length; i++) {
    const gap = mins[i] - mins[i - 1];
    if (gap > 0 && gap < 120) gaps.push(gap); // filter out unreasonable gaps
  }
  if (gaps.length === 0) return null;
  gaps.sort((a, b) => a - b);
  return percentile(gaps, 50);
}

function onLastNChange() {
  const sel = document.getElementById('lastNSelect');
  const customRow = document.getElementById('customRangeRow');
  customRow.style.display = sel.value === 'custom' ? '' : 'none';
  if (selectedRoute) loadStats();
}

function renderLastNDays(routes, today, gtfsId) {
  const sel = document.getElementById('lastNSelect');
  const el  = document.getElementById('sLastN');
  gtfsId = gtfsId || selectedRoute.gtfs;

  if (sel.value === 'custom') {
    renderCustomRange(routes, gtfsId);
    el.textContent = '–';
    return;
  }

  const n = parseInt(sel.value, 10);
  let actual = 0, scheduled = 0;
  for (let i = 1; i <= n; i++) {
    const d = new Date(today);
    d.setDate(today.getDate() - i);
    const ds = fmtDate(d);
    actual += (routes[ds] || []).length;
    scheduled += getScheduleCount(gtfsId, dayType(d));
  }
  el.textContent = scheduled > 0 ? `${actual}/${scheduled}` : actual;
}

function renderCustomRange(routes, gtfsId) {
  const fromEl = document.getElementById('rangeFrom');
  const toEl   = document.getElementById('rangeTo');
  const el     = document.getElementById('sCustomRange');
  if (!fromEl.value || !toEl.value) { el.textContent = '–'; return; }
  gtfsId = gtfsId || selectedRoute.gtfs;

  let actual = 0, scheduled = 0;
  const from = new Date(fromEl.value + 'T00:00:00');
  const to   = new Date(toEl.value + 'T00:00:00');
  const todayStr = fmtDate(new Date());

  for (let d = new Date(from); d <= to; d.setDate(d.getDate() + 1)) {
    const ds = fmtDate(d);
    if (ds === todayStr) continue; // exclude today
    actual += (routes[ds] || []).length;
    scheduled += getScheduleCount(gtfsId, dayType(d));
  }
  el.textContent = scheduled > 0 ? `${actual}/${scheduled}` : actual;
}

function percentile(sorted, p) {
  const idx = (p / 100) * (sorted.length - 1);
  const lo  = Math.floor(idx), hi = Math.ceil(idx);
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (idx - lo);
}

// ── Chart drawing ───────────────────────────────────────────────────────────

function drawChart(trips, gtfsId, date) {
  const canvas = document.getElementById('tripChart');
  const W = canvas.offsetWidth || 680;
  canvas.width = W; canvas.height = 300;
  const ctx = canvas.getContext('2d');
  const PAD = { top: 16, right: 20, bottom: 38, left: 46 };
  const cw = W - PAD.left - PAD.right, ch = canvas.height - PAD.top - PAD.bottom;

  const actualMins = trips.map(t => {
    const ts = typeof t === 'object' ? (t.start ?? t.end) : t;
    if (!ts) return NaN;
    const d = new Date(ts);
    return d.getHours() * 60 + d.getMinutes() + d.getSeconds() / 60;
  }).filter(m => !isNaN(m)).sort((a, b) => a - b);

  const dt = dayType(date);
  const schedMins = (scheduleData[gtfsId]?.[dt] || []).slice().sort((a, b) => a - b);

  // Cache for redraw on zoom/pan
  chartState.cachedActual = actualMins;
  chartState.cachedSched = schedMins;
  chartState.cachedDate = date;

  redrawChart();
}

function redrawChart() {
  const canvas = document.getElementById('tripChart');
  if (!canvas) return;
  const W = canvas.offsetWidth || 680;
  canvas.width = W; canvas.height = 300;
  const ctx = canvas.getContext('2d');
  const PAD = { top: 16, right: 20, bottom: 38, left: 46 };
  const cw = W - PAD.left - PAD.right, ch = canvas.height - PAD.top - PAD.bottom;

  const { cachedActual: actualMins, cachedSched: schedMins, cachedDate: date } = chartState;
  if (!date) return;

  const minX = chartState.minX, maxX = chartState.maxX;

  // Count visible items for Y scale
  const visActual = actualMins.filter(m => m >= minX && m <= maxX).length;
  const visSched = schedMins.filter(m => m >= minX && m <= maxX).length;
  // But step chart needs full cumulative count
  const maxY = Math.max(actualMins.length, schedMins.length, 1);

  function toX(m) { return PAD.left + Math.max(0, Math.min(1, (m - minX) / (maxX - minX))) * cw; }
  function toY(n) { return PAD.top + (1 - n / maxY) * ch; }

  // Background
  ctx.fillStyle = '#191c22'; ctx.fillRect(0, 0, W, canvas.height);

  // Grid
  ctx.strokeStyle = '#25292f'; ctx.lineWidth = 1;
  const hStep = (maxX - minX) > 6 * 60 ? 3 : (maxX - minX) > 2 * 60 ? 1 : 0.5;
  for (let h = Math.ceil(minX / 60); h * 60 <= maxX; h += hStep) {
    const x = toX(h * 60);
    ctx.beginPath(); ctx.moveTo(x, PAD.top); ctx.lineTo(x, PAD.top + ch); ctx.stroke();
  }
  const yStep = Math.max(1, Math.round(maxY / 5));
  for (let y = 0; y <= maxY; y += yStep) {
    const yy = toY(y);
    ctx.beginPath(); ctx.moveTo(PAD.left, yy); ctx.lineTo(PAD.left + cw, yy); ctx.stroke();
  }

  // Step chart helper
  function drawSteps(mins, color, alpha, stopAtCurrent) {
    if (!mins.length) return;
    ctx.save(); ctx.strokeStyle = color; ctx.globalAlpha = alpha; ctx.lineWidth = 2;
    ctx.beginPath(); ctx.moveTo(toX(minX), toY(0)); let c = 0;
    const now = new Date();
    const nowMin = now.getHours() * 60 + now.getMinutes() + now.getSeconds() / 60;
    for (const m of mins) {
      ctx.lineTo(toX(m), toY(c)); c++; ctx.lineTo(toX(m), toY(c));
    }
    if (stopAtCurrent) {
      // End at current time with a marker
      const endMin = Math.min(nowMin, maxX);
      ctx.lineTo(toX(endMin), toY(c));
      ctx.stroke();
      // Draw marker circle at current time
      const cx = toX(endMin), cy = toY(c);
      ctx.beginPath(); ctx.arc(cx, cy, 4, 0, Math.PI * 2);
      ctx.fillStyle = color; ctx.globalAlpha = 1; ctx.fill();
    } else {
      ctx.lineTo(toX(maxX), toY(c));
      ctx.stroke();
    }
    ctx.restore();
  }

  drawSteps(schedMins, '#78818c', 0.55, false);
  // Only stop at current time if viewing today
  const isToday = date && fmtDate(date) === fmtDate(new Date());
  drawSteps(actualMins, '#2f69f3', 1.0, isToday);

  // Selection overlay
  if (chartState.selecting && chartState.selectStartX != null && chartState.selectEndX != null) {
    const x1 = Math.min(chartState.selectStartX, chartState.selectEndX);
    const x2 = Math.max(chartState.selectStartX, chartState.selectEndX);
    ctx.save();
    ctx.fillStyle = 'rgba(47, 105, 243, 0.15)';
    ctx.fillRect(x1, PAD.top, x2 - x1, ch);
    ctx.strokeStyle = 'rgba(47, 105, 243, 0.5)';
    ctx.lineWidth = 1;
    ctx.strokeRect(x1, PAD.top, x2 - x1, ch);
    ctx.restore();
  }

  // X axis labels
  ctx.fillStyle = '#78818c'; ctx.font = '10px Helvetica Neue'; ctx.textAlign = 'center';
  for (let h = Math.ceil(minX / 60); h * 60 <= maxX; h += hStep) {
    const hh = h % 24;
    const label = `${hh % 12 || 12}${hh < 12 ? 'a' : 'p'}`;
    ctx.fillText(label, toX(h * 60), PAD.top + ch + 14);
  }
  // Y axis labels
  ctx.textAlign = 'right';
  for (let y = 0; y <= maxY; y += yStep) ctx.fillText(y, PAD.left - 5, toY(y) + 4);

  // Zoom hint
  if (chartState.minX === chartState.fullMinX && chartState.maxX === chartState.fullMaxX) {
    ctx.fillStyle = '#555'; ctx.font = '9px Helvetica Neue'; ctx.textAlign = 'right';
    ctx.fillText('Scroll to zoom · Click+drag to select range · Double-click to reset', W - PAD.right, canvas.height - 4);
  }
}

// ── Chart interaction (zoom/pan/select) ─────────────────────────────────────

function initChartInteraction() {
  const canvas = document.getElementById('tripChart');
  if (!canvas || canvas._chartListenersAdded) return;
  canvas._chartListenersAdded = true;
  const PAD = { top: 16, right: 20, bottom: 38, left: 46 };

  function xToMin(px) {
    const W = canvas.width;
    const cw = W - PAD.left - PAD.right;
    const frac = (px - PAD.left) / cw;
    return chartState.minX + frac * (chartState.maxX - chartState.minX);
  }

  canvas.addEventListener('wheel', e => {
    e.preventDefault();
    const rect = canvas.getBoundingClientRect();
    const px = (e.clientX - rect.left) * (canvas.width / rect.width);
    const centerMin = xToMin(px);
    const range = chartState.maxX - chartState.minX;
    const factor = e.deltaY > 0 ? 1.3 : 0.7;
    let newRange = range * factor;
    newRange = Math.max(30, Math.min(chartState.fullMaxX - chartState.fullMinX, newRange));
    const frac = (centerMin - chartState.minX) / range;
    chartState.minX = Math.max(chartState.fullMinX, centerMin - frac * newRange);
    chartState.maxX = Math.min(chartState.fullMaxX, chartState.minX + newRange);
    if (chartState.maxX - chartState.minX < 30) chartState.maxX = chartState.minX + 30;
    redrawChart();
  }, { passive: false });

  canvas.addEventListener('mousedown', e => {
    if (e.button !== 0) return;
    const rect = canvas.getBoundingClientRect();
    const px = (e.clientX - rect.left) * (canvas.width / rect.width);
    chartState.selecting = true;
    chartState.selectStartX = px;
    chartState.selectEndX = px;
  });

  canvas.addEventListener('mousemove', e => {
    if (!chartState.selecting) return;
    const rect = canvas.getBoundingClientRect();
    const px = (e.clientX - rect.left) * (canvas.width / rect.width);
    chartState.selectEndX = px;
    redrawChart();
  });

  canvas.addEventListener('mouseup', e => {
    if (!chartState.selecting) return;
    chartState.selecting = false;
    const rect = canvas.getBoundingClientRect();
    const px = (e.clientX - rect.left) * (canvas.width / rect.width);
    chartState.selectEndX = px;
    const x1 = Math.min(chartState.selectStartX, chartState.selectEndX);
    const x2 = Math.max(chartState.selectStartX, chartState.selectEndX);
    // Only zoom if drag was significant (>10px)
    if (x2 - x1 > 10) {
      const W = canvas.width;
      const cw = W - PAD.left - PAD.right;
      const range = chartState.maxX - chartState.minX;
      const newMin = chartState.minX + ((x1 - PAD.left) / cw) * range;
      const newMax = chartState.minX + ((x2 - PAD.left) / cw) * range;
      chartState.minX = Math.max(chartState.fullMinX, newMin);
      chartState.maxX = Math.min(chartState.fullMaxX, newMax);
      if (chartState.maxX - chartState.minX < 30) chartState.maxX = chartState.minX + 30;
    }
    chartState.selectStartX = null;
    chartState.selectEndX = null;
    redrawChart();
  });

  canvas.addEventListener('dblclick', () => {
    chartState.minX = chartState.fullMinX;
    chartState.maxX = chartState.fullMaxX;
    redrawChart();
  });
}

function dayType(d) {
  const day = d.getDay(); return day === 0 ? 'sunday' : day === 6 ? 'saturday' : 'weekday';
}

async function clearStats() {
  if (!confirm('Clear all recorded trip data?')) return;
  try { await fetch('/api/stats/clear', { method: 'POST' }); if (selectedRoute) loadStats(); }
  catch (e) { alert('Error: ' + e.message); }
}

