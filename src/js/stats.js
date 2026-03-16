'use strict';

// ── Statistics — CDF-based chart ─────────────────────────────────────────────

// Which CDF series are active (toggled by buttons)
let cdfActive = { scheduled: true, today: true, dow: false, ndays: false };

// Cached CDF data from the server
let cdfData = {};       // route -> {date: [sorted mins], ...}
let cdfSchedMins = [];  // schedule minutes for current route/day-type

// Cutoff: discard data before this date/time
const CUTOFF_DATE = '2026-03-15';
// First full day of tracking (exclude partial first day from historical stats)
const FIRST_FULL_DATE = '2026-03-16';

// Chart zoom/pan state
let chartState = {
  minX: 5 * 60, maxX: 24 * 60,
  fullMinX: 5 * 60, fullMaxX: 24 * 60,
  dragging: false, dragStartX: null, dragStartMin: null,
  selecting: false, selectStartX: null, selectEndX: null,
  hoverMin: null, // minute value under cursor (null = no hover)
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

// ── Filter routeCdfs to only valid dates ─────────────────────────────────────

function filterCutoff(routeCdfs) {
  const filtered = {};
  for (const [day, mins] of Object.entries(routeCdfs)) {
    if (day >= CUTOFF_DATE) filtered[day] = mins;
  }
  return filtered;
}

// ── Render stats ─────────────────────────────────────────────────────────────

function renderStats() {
  const isMulti = selectedRoute.multi && selectedRoute.apiIds;
  const routeIds = isMulti ? selectedRoute.apiIds : [selectedRoute.id];
  const rawCdfs = isMulti ? mergeRouteCdfs(...routeIds) : (cdfData[selectedRoute.id] || {});
  const routeCdfs = filterCutoff(rawCdfs);

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

// ── CDF value at a given minute ──────────────────────────────────────────────

function cdfValueAt(mins, minute, divisor, stopAtCurrent) {
  const now = new Date();
  const nowMin = now.getHours() * 60 + now.getMinutes() + now.getSeconds() / 60;
  const cap = stopAtCurrent ? Math.min(minute, nowMin) : minute;
  let count = 0;
  for (const m of mins) {
    if (m > cap) break;
    count++;
  }
  return count / divisor;
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
      extendToEnd: true, // scheduled extends flat to midnight
    });
  }

  if (cdfActive.today) {
    const todayMins = routeCdfs[todayStr] || [];
    series.push({
      key: 'today',
      label: 'Today',
      mins: todayMins,
      divisor: 1,
      color: CDF_COLORS.today,
      alpha: 1.0,
      stopAtCurrent: true,
      extendToEnd: false,
    });
  }

  if (cdfActive.dow) {
    const targetDow = today.getDay();
    const allMins = [];
    let dayCount = 0;
    for (const [day, mins] of Object.entries(routeCdfs)) {
      if (day === todayStr || day < FIRST_FULL_DATE) continue;
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
        extendToEnd: false,
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
      if (ds < FIRST_FULL_DATE) continue;
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
        extendToEnd: false,
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

// ── Format minute as time string ─────────────────────────────────────────────

function fmtMin(m) {
  const h = Math.floor(m / 60) % 24;
  const mm = Math.floor(m % 60);
  const ampm = h < 12 ? 'a' : 'p';
  return (h % 12 || 12) + ':' + String(mm).padStart(2, '0') + ampm;
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

  // Compute maxY from visible range (CDF value at maxX for each series)
  let maxY = 1;
  for (const s of series) {
    const val = cdfValueAt(s.mins, maxX, s.divisor, s.stopAtCurrent);
    if (val > maxY) maxY = val;
  }
  maxY = Math.ceil(maxY * 1.05) || 1; // 5% headroom

  function toX(m) { return PAD.left + ((m - minX) / (maxX - minX)) * cw; }
  function toY(n) { return PAD.top + (1 - n / maxY) * ch; }

  // Background
  ctx.fillStyle = '#191c22'; ctx.fillRect(0, 0, W, canvas.height);

  // Clip drawing to plot area
  ctx.save();
  ctx.beginPath();
  ctx.rect(PAD.left, PAD.top, cw, ch);
  ctx.clip();

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

  // End clip
  ctx.restore();

  // Selection overlay (outside clip so it draws over axes too)
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

  // Hover crosshair + tooltip
  if (chartState.hoverMin != null && !chartState.selecting && series.length) {
    drawHoverTooltip(ctx, series, chartState.hoverMin, minX, maxX, maxY, toX, toY, PAD, cw, ch, W);
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

  // Zoom hint (always visible)
  ctx.fillStyle = '#555'; ctx.font = '9px Helvetica Neue'; ctx.textAlign = 'right';
  const isZoomed = chartState.minX !== chartState.fullMinX || chartState.maxX !== chartState.fullMaxX;
  const hint = isZoomed
    ? 'Scroll to zoom \u00b7 Double-click to reset'
    : 'Scroll to zoom \u00b7 Click+drag to select range \u00b7 Double-click to reset';
  ctx.fillText(hint, W - PAD.right, canvas.height - 4);
}

function drawCdfSteps(ctx, s, minX, maxX, maxY, toX, toY, PAD, cw) {
  const div = s.divisor;
  const now = new Date();
  const nowMin = now.getHours() * 60 + now.getMinutes() + now.getSeconds() / 60;

  // Count steps before visible range to get correct starting y
  let count = 0;
  let startIdx = 0;
  for (let i = 0; i < s.mins.length; i++) {
    if (s.mins[i] >= minX) break;
    if (s.stopAtCurrent && s.mins[i] > nowMin) break;
    count++;
    startIdx = i + 1;
  }

  ctx.save();
  ctx.strokeStyle = s.color;
  ctx.globalAlpha = s.alpha;
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(toX(minX), toY(count / div));

  // Draw steps only within visible range
  for (let i = startIdx; i < s.mins.length; i++) {
    const m = s.mins[i];
    if (m > maxX) break;
    if (s.stopAtCurrent && m > nowMin) break;
    const yBefore = count / div;
    count++;
    const yAfter = count / div;
    ctx.lineTo(toX(m), toY(yBefore));
    ctx.lineTo(toX(m), toY(yAfter));
  }

  if (s.stopAtCurrent) {
    // Today series: extend to current time, draw dot
    const endMin = Math.min(nowMin, maxX);
    const yEnd = count / div;
    ctx.lineTo(toX(endMin), toY(yEnd));
    ctx.stroke();
    // Draw marker circle at current time (only if in visible range)
    if (nowMin >= minX && nowMin <= maxX) {
      const cx = toX(endMin), cy = toY(yEnd);
      ctx.beginPath(); ctx.arc(cx, cy, 4, 0, Math.PI * 2);
      ctx.fillStyle = s.color; ctx.globalAlpha = 1; ctx.fill();
    }
  } else if (s.extendToEnd) {
    // Scheduled: extend flat to end of day (midnight)
    ctx.lineTo(toX(24 * 60), toY(count / div));
    ctx.stroke();
  } else {
    // Historical averages: end at last data point
    ctx.stroke();
  }

  ctx.restore();
}

// ── Hover tooltip ────────────────────────────────────────────────────────────

function drawHoverTooltip(ctx, series, hoverMin, minX, maxX, maxY, toX, toY, PAD, cw, ch, W) {
  const hx = toX(hoverMin);

  // Vertical line
  ctx.save();
  ctx.strokeStyle = 'rgba(224, 232, 240, 0.25)';
  ctx.lineWidth = 1;
  ctx.setLineDash([4, 3]);
  ctx.beginPath();
  ctx.moveTo(hx, PAD.top);
  ctx.lineTo(hx, PAD.top + ch);
  ctx.stroke();
  ctx.setLineDash([]);
  ctx.restore();

  // Compute values for each series at hoverMin
  const lines = [];
  for (const s of series) {
    const val = cdfValueAt(s.mins, hoverMin, s.divisor, s.stopAtCurrent);
    const valStr = s.divisor > 1 ? val.toFixed(1) : String(Math.round(val));
    lines.push({ label: s.label, val: valStr, color: s.color });
  }

  // Draw small dots on each series at hover position
  for (const s of series) {
    const val = cdfValueAt(s.mins, hoverMin, s.divisor, s.stopAtCurrent);
    const dy = toY(val);
    ctx.save();
    ctx.fillStyle = s.color;
    ctx.globalAlpha = s.alpha;
    ctx.beginPath();
    ctx.arc(hx, dy, 3, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
  }

  // Tooltip box
  if (!lines.length) return;

  ctx.save();
  ctx.font = '10px Helvetica Neue';
  const timeStr = fmtMin(hoverMin);
  const header = timeStr;
  const lineHeight = 14;
  const padding = 6;
  const dotSize = 6;
  const dotGap = 4;

  // Measure text widths
  let boxW = ctx.measureText(header).width;
  for (const l of lines) {
    const tw = ctx.measureText(l.label + ': ' + l.val).width + dotSize + dotGap;
    if (tw > boxW) boxW = tw;
  }
  boxW += padding * 2;
  const boxH = lineHeight * (1 + lines.length) + padding * 2 - 4;

  // Position tooltip (flip if near right edge)
  let tx = hx + 10;
  if (tx + boxW > W - PAD.right) tx = hx - boxW - 10;
  let ty = PAD.top + 8;

  // Background
  ctx.fillStyle = 'rgba(18, 21, 26, 0.92)';
  ctx.strokeStyle = '#25292f';
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.roundRect(tx, ty, boxW, boxH, 4);
  ctx.fill();
  ctx.stroke();

  // Header (time)
  ctx.fillStyle = '#e0e8f0';
  ctx.textAlign = 'left';
  ctx.fillText(header, tx + padding, ty + padding + 10);

  // Series values
  let row = 1;
  for (const l of lines) {
    const ry = ty + padding + 10 + lineHeight * row;
    // Color dot
    ctx.fillStyle = l.color;
    ctx.beginPath();
    ctx.arc(tx + padding + dotSize / 2, ry - 3, dotSize / 2, 0, Math.PI * 2);
    ctx.fill();
    // Text
    ctx.fillStyle = '#b0b8c4';
    ctx.fillText(l.label + ': ' + l.val, tx + padding + dotSize + dotGap, ry);
    row++;
  }

  ctx.restore();
}

// ── Chart interaction (zoom/pan/select/hover) ────────────────────────────────

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
    chartState.hoverMin = null;
    chartState.selectStartX = px;
    chartState.selectEndX = px;
  });

  canvas.addEventListener('mousemove', e => {
    const rect = canvas.getBoundingClientRect();
    const px = (e.clientX - rect.left) * (canvas.width / rect.width);
    if (chartState.selecting) {
      chartState.selectEndX = px;
      chartState.hoverMin = null;
    } else {
      const minPx = PAD.left, maxPx = PAD.left + (canvas.width - PAD.left - PAD.right);
      if (px >= minPx && px <= maxPx) {
        chartState.hoverMin = xToMin(px);
      } else {
        chartState.hoverMin = null;
      }
    }
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

  canvas.addEventListener('mouseleave', () => {
    chartState.hoverMin = null;
    if (!chartState.selecting) redrawChart();
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

function csvTimestamp() {
  const d = new Date();
  return d.getFullYear()
    + String(d.getMonth() + 1).padStart(2, '0')
    + String(d.getDate()).padStart(2, '0')
    + '_' + String(d.getHours()).padStart(2, '0')
    + String(d.getMinutes()).padStart(2, '0');
}

function exportCsvPlot() {
  const series = buildCdfSeries();
  if (!series.length) return;

  const rows = ['series,minute,time_of_day,cdf_value'];
  for (const s of series) {
    let count = 0;
    for (const m of s.mins) {
      if (s.stopAtCurrent) {
        const now = new Date();
        const nowMin = now.getHours() * 60 + now.getMinutes() + now.getSeconds() / 60;
        if (m > nowMin) break;
      }
      count++;
      const val = (count / s.divisor).toFixed(2);
      const safe = s.label.replace(/"/g, '""');
      rows.push(`"${safe}",${m.toFixed(2)},${fmtMin(m)},${val}`);
    }
  }

  const blob = new Blob([rows.join('\n')], { type: 'text/csv' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `septa_cdf_${csvTimestamp()}.csv`;
  a.click();
  URL.revokeObjectURL(a.href);
}

function exportCsvAll() {
  const a = document.createElement('a');
  a.href = '/api/stats/export?format=csv';
  a.download = `septa_trips_${csvTimestamp()}.csv`;
  a.click();
}

async function clearStats() {
  if (!confirm('Clear all recorded trip data?')) return;
  try { await fetch('/api/stats/clear', { method: 'POST' }); if (selectedRoute) loadStats(); }
  catch (e) { alert('Error: ' + e.message); }
}
