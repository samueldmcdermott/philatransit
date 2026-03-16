'use strict';

// ── Statistics — CDF-based chart ─────────────────────────────────────────────

// Which CDF series are active (toggled by buttons)
let cdfActive = { scheduled: true, today: true, dow: false, ndays: false };

// Cached CDF data from the server
let cdfData = {};       // route -> {date: [sorted mins], ...}
let cdfSchedMins = [];  // schedule minutes for current route/day-type

// Chart zoom/pan state
let chartState = {
  minX: 5 * 60, maxX: 24 * 60,
  fullMinX: 5 * 60, fullMaxX: 24 * 60,
  dragging: false, dragStartX: null, dragStartMin: null,
  selecting: false, selectStartX: null, selectEndX: null,
};

// Series colors
const CDF_COLORS = {
  scheduled: '#78818c',
  today:     '#2f69f3',
  dow:       '#22c55e',
  ndays:     '#f59e0b',
};

// ── Load stats ───────────────────────────────────────────────────────────────

async function loadStats() {
  if (!selectedRoute) return;
  document.getElementById('emptyStats').style.display   = 'none';
  document.getElementById('statsContent').style.display = '';
  try {
    const data = await apiFetch('/api/stats/cdfs');
    cdfData = data;
    renderStats();
  } catch (e) { setStatus('Stats error: ' + e.message); }
}

// ── Merge helpers for multi-route (T-ALL) ────────────────────────────────────

function mergeRouteCdfs(...routeIds) {
  const merged = {};
  for (const rid of routeIds) {
    const routeData = cdfData[rid];
    if (!routeData) continue;
    for (const [day, mins] of Object.entries(routeData)) {
      if (!merged[day]) merged[day] = [];
      merged[day].push(...mins);
    }
  }
  for (const day of Object.keys(merged)) merged[day].sort((a, b) => a - b);
  return merged;
}

function mergeScheduleMins(...gtfsIds) {
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
  for (const dt of Object.keys(merged)) merged[dt].sort((a, b) => a - b);
  return merged;
}

// ── Render stats ─────────────────────────────────────────────────────────────

function renderStats() {
  const isMulti = selectedRoute.multi && selectedRoute.apiIds;
  const routeIds = isMulti ? selectedRoute.apiIds : [selectedRoute.id];
  const routeCdfs = isMulti ? mergeRouteCdfs(...routeIds) : (cdfData[selectedRoute.id] || {});

  const effectiveGtfs = isMulti ? '_T-ALL' : selectedRoute.gtfs;
  if (isMulti && !scheduleData['_T-ALL']) {
    scheduleData['_T-ALL'] = mergeScheduleMins(...selectedRoute.apiIds);
  }

  const today = new Date();
  const todayStr = fmtDate(today);
  const dt = dayType(today);

  // Schedule minutes for today's day-type
  cdfSchedMins = (scheduleData[effectiveGtfs]?.[dt] || []).slice().sort((a, b) => a - b);

  // Update DOW label
  const dayNames = ['Sundays', 'Mondays', 'Tuesdays', 'Wednesdays', 'Thursdays', 'Fridays', 'Saturdays'];
  document.getElementById('dowLabel').textContent = 'Previous ' + dayNames[today.getDay()];

  // Today trip count
  const todayMins = routeCdfs[todayStr] || [];
  document.getElementById('sToday').textContent = todayMins.length;

  // Days tracked
  const dayCount = Object.keys(routeCdfs).length;
  document.getElementById('sDays').textContent = dayCount;

  // Build chart series
  chartState.minX = chartState.fullMinX;
  chartState.maxX = chartState.fullMaxX;

  // Cache route CDFs for chart drawing
  chartState._routeCdfs = routeCdfs;
  chartState._todayStr = todayStr;
  chartState._today = today;

  redrawChart();
  updateLegend();
  initChartInteraction();
}

// ── Build CDF series ─────────────────────────────────────────────────────────

function buildCdfSeries() {
  const series = [];
  const routeCdfs = chartState._routeCdfs || {};
  const todayStr = chartState._todayStr || fmtDate(new Date());
  const today = chartState._today || new Date();

  if (cdfActive.scheduled && cdfSchedMins.length) {
    series.push({
      key: 'scheduled',
      label: 'Scheduled',
      mins: cdfSchedMins,
      divisor: 1,
      color: CDF_COLORS.scheduled,
      alpha: 0.55,
      stopAtCurrent: false,
    });
  }

  if (cdfActive.today) {
    const todayMins = routeCdfs[todayStr] || [];
    if (todayMins.length) {
      series.push({
        key: 'today',
        label: 'Today',
        mins: todayMins,
        divisor: 1,
        color: CDF_COLORS.today,
        alpha: 1.0,
        stopAtCurrent: true,
      });
    }
  }

  if (cdfActive.dow) {
    const targetDow = today.getDay();
    const allMins = [];
    let dayCount = 0;
    for (const [day, mins] of Object.entries(routeCdfs)) {
      if (day === todayStr) continue;
      const d = new Date(day + 'T12:00:00');
      if (d.getDay() === targetDow) {
        allMins.push(...mins);
        dayCount++;
      }
    }
    if (allMins.length && dayCount > 0) {
      allMins.sort((a, b) => a - b);
      series.push({
        key: 'dow',
        label: 'Prev. ' + ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'][targetDow] + 's',
        mins: allMins,
        divisor: dayCount,
        color: CDF_COLORS.dow,
        alpha: 0.8,
        stopAtCurrent: false,
      });
    }
  }

  if (cdfActive.ndays) {
    const n = getNdaysValue();
    const allMins = [];
    let dayCount = 0;
    for (let i = 1; i <= n; i++) {
      const d = new Date(today);
      d.setDate(today.getDate() - i);
      const ds = fmtDate(d);
      const mins = routeCdfs[ds];
      if (mins && mins.length) {
        allMins.push(...mins);
        dayCount++;
      }
    }
    if (allMins.length && dayCount > 0) {
      allMins.sort((a, b) => a - b);
      series.push({
        key: 'ndays',
        label: 'Prev. ' + n + 'd',
        mins: allMins,
        divisor: dayCount,
        color: CDF_COLORS.ndays,
        alpha: 0.8,
        stopAtCurrent: false,
      });
    }
  }

  return series;
}

function getNdaysValue() {
  const sel = document.getElementById('ndaysSelect');
  if (sel.value === 'custom') {
    return parseInt(document.getElementById('ndaysCustom').value, 10) || 3;
  }
  return parseInt(sel.value, 10) || 3;
}

// ── Toggle CDF series ────────────────────────────────────────────────────────

function toggleCdf(key) {
  cdfActive[key] = !cdfActive[key];
  const btn = document.querySelector(`.cdf-btn[data-cdf="${key}"]`);
  if (btn) btn.classList.toggle('active', cdfActive[key]);
  redrawChart();
  updateLegend();
}

function onNdaysChange() {
  const sel = document.getElementById('ndaysSelect');
  document.getElementById('ndaysCustom').style.display = sel.value === 'custom' ? '' : 'none';
  if (cdfActive.ndays) {
    redrawChart();
    updateLegend();
  }
}

function updateLegend() {
  const el = document.getElementById('chartLegend');
  const series = buildCdfSeries();
  el.innerHTML = series.map(s => {
    const extra = s.key === 'today'
      ? ' <span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:' + s.color + ';vertical-align:middle;margin-left:2px"></span> Now'
      : '';
    const label = s.divisor > 1 ? s.label + ' (avg of ' + s.divisor + ')' : s.label;
    return '<span><span class="ld" style="background:' + s.color + ';opacity:' + s.alpha + '"></span>' + label + extra + '</span>';
  }).join('');
}

// ── Chart drawing ────────────────────────────────────────────────────────────

function redrawChart() {
  const canvas = document.getElementById('tripChart');
  if (!canvas) return;
  const W = canvas.offsetWidth || 680;
  canvas.width = W; canvas.height = 300;
  const ctx = canvas.getContext('2d');
  const PAD = { top: 16, right: 20, bottom: 38, left: 46 };
  const cw = W - PAD.left - PAD.right, ch = canvas.height - PAD.top - PAD.bottom;

  const minX = chartState.minX, maxX = chartState.maxX;
  const series = buildCdfSeries();

  // Compute maxY across all visible series (after divisor)
  let maxY = 1;
  for (const s of series) {
    const effectiveMax = s.mins.length / s.divisor;
    if (effectiveMax > maxY) maxY = effectiveMax;
  }
  // Round maxY up to a nice number
  maxY = Math.ceil(maxY);

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

  // Draw each CDF series
  for (const s of series) {
    drawCdfSteps(ctx, s, minX, maxX, maxY, toX, toY, PAD, cw);
  }

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
    ctx.fillText('Scroll to zoom \u00b7 Click+drag to select range \u00b7 Double-click to reset', W - PAD.right, canvas.height - 4);
  }
}

function drawCdfSteps(ctx, s, minX, maxX, maxY, toX, toY, PAD, cw) {
  if (!s.mins.length) return;
  const div = s.divisor;

  ctx.save();
  ctx.strokeStyle = s.color;
  ctx.globalAlpha = s.alpha;
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(toX(minX), toY(0));

  let count = 0;
  for (const m of s.mins) {
    const yBefore = count / div;
    count++;
    const yAfter = count / div;
    ctx.lineTo(toX(m), toY(yBefore));
    ctx.lineTo(toX(m), toY(yAfter));
  }

  if (s.stopAtCurrent) {
    const now = new Date();
    const nowMin = now.getHours() * 60 + now.getMinutes() + now.getSeconds() / 60;
    const endMin = Math.min(nowMin, maxX);
    const yEnd = count / div;
    ctx.lineTo(toX(endMin), toY(yEnd));
    ctx.stroke();
    // Draw marker circle at current time
    const cx = toX(endMin), cy = toY(yEnd);
    ctx.beginPath(); ctx.arc(cx, cy, 4, 0, Math.PI * 2);
    ctx.fillStyle = s.color; ctx.globalAlpha = 1; ctx.fill();
  } else {
    ctx.lineTo(toX(maxX), toY(count / div));
    ctx.stroke();
  }

  ctx.restore();
}

// ── Chart interaction (zoom/pan/select) ──────────────────────────────────────

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

// ── Helpers ──────────────────────────────────────────────────────────────────

function dayType(d) {
  const day = d.getDay(); return day === 0 ? 'sunday' : day === 6 ? 'saturday' : 'weekday';
}

function percentile(sorted, p) {
  const idx = (p / 100) * (sorted.length - 1);
  const lo  = Math.floor(idx), hi = Math.ceil(idx);
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (idx - lo);
}

async function clearStats() {
  if (!confirm('Clear all recorded trip data?')) return;
  try { await fetch('/api/stats/clear', { method: 'POST' }); if (selectedRoute) loadStats(); }
  catch (e) { alert('Error: ' + e.message); }
}
