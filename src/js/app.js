'use strict';

// ── App state ──────────────────────────────────────────────────────────────
let selectedRoute        = null;
let activePanel          = 'live';
let activeMode           = 'SUBWAY';
let liveRegistry         = {};
let routeStops           = [];
let routeStopsOrdered    = false;
let stopsData            = {};
let stopsArr             = [];
let scheduleData         = {};
let shapesData           = {};
let tunnelTimesData      = {};
let serverTrackerRunning = false;
let modelCardOpen        = true;
let refreshTimer         = null;

// Map state
let leafletMap       = null;
let mapInitialized   = false;
let vehicleMarkers   = {};
let routeLayerGroup  = null;
let stopLayerGroup   = null;
let vehicleLayerGroup= null;

// Tunnel estimation state
let tunnelEstimationOn = true;
let vehicleHistory     = {};
let ghostVehicles      = {};
let ghostedVids        = {};
let tunnelShapePaths   = {};

// ── Init ───────────────────────────────────────────────────────────────────

async function init() {
  buildRouteList();
  await Promise.all([checkTrackerStatus(), loadStaticData()]);
  startAutoRefresh();
}

async function loadStaticData() {
  await Promise.all([
    fetch('/static/stops.json').then(r => r.ok ? r.json() : {}).then(d => {
      stopsData = d;
      stopsArr  = Object.values(d);
    }).catch(() => {}),
    fetch('/static/schedule.json').then(r => r.ok ? r.json() : {}).then(d => { scheduleData = d; }).catch(() => {}),
    fetch('/static/shapes.json').then(r => r.ok ? r.json() : {}).then(d => { shapesData = d; }).catch(() => {}),
    fetch('/static/tunnel_times.json').then(r => r.ok ? r.json() : {}).then(d => { tunnelTimesData = d; }).catch(() => {}),
  ]);
}

// ── Helpers ─────────────────────────────────────────────────────────────────

function setStatus(msg) { document.getElementById('statusTxt').textContent = msg; }
function fmtTime(ts) { return new Date(ts).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'}); }
function fmtDate(d)  { return d.toISOString().slice(0,10); }
function pctFmt(n)   { return isNaN(n)?'–':n.toFixed(1); }

async function apiFetch(url, opts) {
  const r = await fetch(url, opts);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

function headingLabel(deg) {
  if (deg == null || deg === '') return null;
  const dirs = ['N','NE','E','SE','S','SW','W','NW'];
  return dirs[Math.round(((+deg % 360) + 360) % 360 / 45) % 8];
}

function nearestStop(lat, lng) {
  const pool = routeStops.length > 0 ? routeStops : stopsArr;
  if (pool.length === 0) return '';
  let bestName = '', bestDist = Infinity;
  for (const s of pool) {
    const d = (lat - s.lat) ** 2 + (lng - s.lng) ** 2;
    if (d < bestDist) { bestDist = d; bestName = s.name; }
  }
  return bestName;
}

// ── Sidebar ─────────────────────────────────────────────────────────────────

function setMode(mode) {
  activeMode = mode;
  document.querySelectorAll('.mode-tab').forEach(t =>
    t.classList.toggle('active', t.dataset.mode === mode));
  buildRouteList();
}

function buildRouteList() {
  const routes = MODES[activeMode]?.routes || [];
  const el = document.getElementById('routeList');
  el.innerHTML = `<div class="route-section-label">${activeMode}</div>`;
  for (const r of routes) {
    const btn = document.createElement('button');
    btn.className = 'route-btn' + (selectedRoute?.id === r.id ? ' active' : '');
    btn.innerHTML = `<span class="route-dot" style="background:${r.color}"></span><span class="route-label">${r.label}</span>`;
    btn.onclick = () => selectRoute(r, MODES[activeMode].type);
    el.appendChild(btn);
  }
}

async function selectRoute(route, type) {
  selectedRoute = { ...route, type };
  liveRegistry  = {};
  routeStops        = [];
  routeStopsOrdered = false;
  vehicleHistory    = {};
  ghostVehicles     = {};
  ghostedVids       = {};
  tunnelShapePaths  = {};
  buildRouteList();
  const tunnelBtn = document.getElementById('tunnelBtn');
  if (tunnelBtn) tunnelBtn.style.display = TUNNEL_ROUTES.has(route.id) ? '' : 'none';
  await fetchRouteStops();
  if (activePanel === 'live')  fetchNow();
  else if (activePanel === 'map')   drawMap();
  else if (activePanel === 'stats') loadStats();
}

async function fetchRouteStops() {
  if (!selectedRoute) return;
  const key = selectedRoute.id;

  if (HARDCODED_STATIONS[key]) {
    routeStops        = HARDCODED_STATIONS[key];
    routeStopsOrdered = true;
    return;
  }

  const apiIds = selectedRoute.apiIds || [selectedRoute.id];
  const results = await Promise.all(
    apiIds.map(id =>
      fetch(`/api/septa/stops?route=${encodeURIComponent(id)}`)
        .then(r => r.json()).then(d => Array.isArray(d) ? d : []).catch(() => [])
    )
  );
  const seen = new Set();
  routeStops = results.flat().filter(s => {
    const k = s.stopid || s.stopname;
    if (seen.has(k)) return false;
    seen.add(k);
    return s.lat && s.lng;
  }).map(s => ({ name: s.stopname, lat: +s.lat, lng: +s.lng }));
}

function toggleModelCard() {
  modelCardOpen = !modelCardOpen;
  document.getElementById('mcBody').classList.toggle('hidden', !modelCardOpen);
  document.getElementById('mcArrow').textContent = modelCardOpen ? '▾' : '▸';
}

// ── Panel switching ─────────────────────────────────────────────────────────

function setPanel(panel) {
  activePanel = panel;
  const tabs = document.querySelectorAll('.panel-tab');
  const panels = ['live','map','stats'];
  tabs.forEach((t, i) => t.classList.toggle('active', panels[i] === panel));
  document.getElementById('livePanel').style.display  = panel === 'live'  ? '' : 'none';
  document.getElementById('mapPanel').style.display   = panel === 'map'   ? '' : 'none';
  document.getElementById('statsPanel').style.display = panel === 'stats' ? '' : 'none';
  if (panel === 'stats' && selectedRoute) loadStats();
  if (panel === 'live'  && selectedRoute) fetchNow();
  if (panel === 'map') {
    if (selectedRoute) drawMap();
    setTimeout(() => { if (leafletMap) leafletMap.invalidateSize(); }, 50);
  }
}

// ── Tracker ─────────────────────────────────────────────────────────────────

async function checkTrackerStatus() {
  try {
    const d = await apiFetch('/api/tracker/status');
    setTrackerUI(d.running, d.tracked);
  } catch (_) {}
}

function setTrackerUI(running, tracked) {
  serverTrackerRunning = running;
  const btn = document.getElementById('trackerBtn');
  btn.textContent = running ? `Tracker: On (${tracked})` : 'Tracker: Off';
  btn.className   = 'btn' + (running ? ' btn-on' : '');
}

async function toggleTracker() {
  const action = serverTrackerRunning ? 'stop' : 'start';
  try {
    await apiFetch(`/api/tracker/${action}`, { method: 'POST' });
    setTrackerUI(action === 'start', 0);
  } catch (e) { setStatus('Tracker error: ' + e.message); }
}

// ── Auto-refresh ────────────────────────────────────────────────────────────

let ghostTickTimer = null;

function startAutoRefresh() {
  clearInterval(refreshTimer);
  clearInterval(ghostTickTimer);
  refreshTimer = setInterval(() => {
    if (!selectedRoute) return;
    if (activePanel === 'live') fetchNow();
    if (activePanel === 'map')  refreshMapVehicles();
  }, 30000);
  ghostTickTimer = setInterval(() => {
    if (!tunnelEstimationOn || Object.keys(ghostVehicles).length === 0) return;
    if (!selectedRoute || !TUNNEL_ROUTES.has(selectedRoute.id)) return;
    tickGhosts();
  }, 5000);
}

function tickGhosts() {
  const now = Date.now();
  let changed = false;
  for (const [vid, ghost] of Object.entries(ghostVehicles)) {
    const totalElapsed = (now - ghost.enterTs) / 1000;

    if ((now - ghost.enterTs) > GHOST_MAX_AGE_MS) {
      delete ghostVehicles[vid];
      if (vehicleMarkers[vid]) {
        vehicleLayerGroup?.removeLayer(vehicleMarkers[vid]);
        delete vehicleMarkers[vid];
      }
      changed = true;
      continue;
    }

    let fraction, currentDirection;
    if (totalElapsed <= ghost.halfTime) {
      fraction = totalElapsed / ghost.halfTime;
      currentDirection = ghost.direction;
      ghost.path = ghost.direction === 'eastbound' ? ghost.pathWE : ghost.pathEW;
    } else {
      const secondElapsed = totalElapsed - ghost.halfTime;
      fraction = secondElapsed / ghost.halfTime;
      currentDirection = ghost.direction === 'eastbound' ? 'westbound' : 'eastbound';
      ghost.path = ghost.direction === 'eastbound' ? ghost.pathEW : ghost.pathWE;
    }

    fraction = Math.min(fraction, 1.0);
    if (fraction >= 1.0 && totalElapsed > ghost.halfTime * 2) {
      delete ghostVehicles[vid];
      if (vehicleMarkers[vid]) {
        vehicleLayerGroup?.removeLayer(vehicleMarkers[vid]);
        delete vehicleMarkers[vid];
      }
      changed = true;
      continue;
    }

    const pos = pointAlongPath(ghost.path, fraction);
    ghost.lat = pos.lat;
    ghost.lng = pos.lng;
    ghost.fraction = fraction;
    ghost.currentDirection = currentDirection;
    ghost.leg = totalElapsed <= ghost.halfTime ? 'first' : 'second';

    if (vehicleMarkers[vid]) {
      vehicleMarkers[vid].setLatLng([pos.lat, pos.lng]);
    }
    changed = true;
  }
  if (changed && activePanel === 'live') {
    document.querySelectorAll('.ghost-card').forEach(card => {
      const label = card.querySelector('.ghost-label');
      if (label) {
        const vid = card.dataset?.vid;
        const ghost = vid ? ghostVehicles[vid] : null;
        if (ghost) {
          const pct = Math.round((ghost.fraction || 0) * 100);
          const dir = ghost.currentDirection || ghost.direction;
          label.textContent = `Tunnel estimate · ${pct}% ${dir}`;
        }
      }
    });
  }
}

// ── Fetch & render vehicles ─────────────────────────────────────────────────

async function fetchNow() {
  if (!selectedRoute) return;
  const btn = document.getElementById('refreshBtn');
  btn.disabled = true;
  setStatus('Fetching…');
  try {
    let vehicles;
    if (selectedRoute.type === 'rail') {
      const data = await apiFetch('/api/septa/trainview');
      vehicles = processRailData(data);
    } else {
      const apiIds = selectedRoute.apiIds || [selectedRoute.id];
      const results = await Promise.all(
        apiIds.map(id => apiFetch(`/api/septa/transitview?route=${encodeURIComponent(id)}`))
      );
      vehicles = processTransitData(results.flatMap(r => r?.bus || []), selectedRoute.id);
    }
    updateVehicleHistory(vehicles);
    detectTunnelEntries(vehicles);
    const ghosts = getGhostVehicles();
    const allVehicles = [...vehicles, ...ghosts];

    renderVehicles(allVehicles);
    if (activePanel === 'map') updateVehiclesOnMap(allVehicles);
    return allVehicles;
  } catch (e) {
    setStatus(`Error: ${e.message}`);
    return [];
  } finally {
    btn.disabled = false;
  }
}

function processRailData(trains) {
  return trains
    .filter(t => railLineKey(t.line || '', t.dest || '', t.SOURCE || '') === selectedRoute.id)
    .map(t => ({
      _id: String(t.trainno || Math.random()),
      _rkey: selectedRoute.id,
      label: String(t.trainno || '?'),
      dest:  t.dest || '',
      late:  t.late != null ? +t.late : 0,
      heading: t.heading, lat: t.lat, lng: t.lng, trip: t.trainno,
    }));
}

function processTransitData(rawVehicles, routeId) {
  return rawVehicles
    .filter(v => v.late < 998 && v.label != null && v.label !== 'None' && String(v.label) !== '0'
              && v.VehicleID != null && v.VehicleID !== 'None' && String(v.VehicleID) !== '0')
    .map(v => {
      const trip = v.trip && String(v.trip) !== '0' ? String(v.trip) : '';
      const vid  = v.VehicleID && String(v.VehicleID) !== '0' ? String(v.VehicleID) : '';
      const id   = trip || vid || `${v.label}_${v.lat}_${v.lng}`;
      return {
        _id: id,
        _rkey: routeId,
        label: v.label || vid || '?',
        dest:  v.destination || v.dest || '',
        late:  v.late != null ? +v.late : 0,
        heading: v.heading, lat: v.lat, lng: v.lng, trip: v.trip,
      };
    });
}

// ── Render vehicle cards ────────────────────────────────────────────────────

function renderVehicles(vehicles) {
  const grid  = document.getElementById('vehicleGrid');
  const empty = document.getElementById('emptyLive');
  const now   = Date.now();
  const color = selectedRoute?.color || '#2f69f3';

  if (vehicles.length === 0) {
    grid.style.display = 'none'; empty.style.display = '';
    const isSubway = selectedRoute && ['MFL','BSL'].includes(selectedRoute.id);
    const subwayNote = isSubway ? '<div style="margin-top:8px;color:var(--muted);font-size:12px">SEPTA does not provide real-time tracking for subway lines.</div>' : '';
    empty.innerHTML = `<div class="empty-icon">🚌</div><div class="empty-title">No live vehicles</div><div>No active vehicles for this route right now.</div>${subwayNote}`;
    setStatus(`No vehicles · ${fmtTime(now)}`);
    detectCompletions(new Set(), now);
    return;
  }

  const curIds = new Set(vehicles.map(v => v._id));
  detectCompletions(curIds, now);
  const newReg = {};
  for (const v of vehicles) {
    const ex = liveRegistry[v._id];
    newReg[v._id] = { route: v._rkey, firstSeen: ex?.firstSeen ?? now, lastSeen: now };
  }
  liveRegistry = newReg;

  vehicles.sort((a, b) => a.label.localeCompare(b.label, undefined, { numeric: true }));
  grid.style.display = ''; empty.style.display = 'none';
  grid.innerHTML = '';

  for (const v of vehicles) {
    const isGhost = v._ghost === true;
    const late = v.late;
    const lateColor = late <= 0 ? 'var(--green)' : late <= 5 ? 'var(--yellow)' : 'var(--red)';
    const lateText  = late <= 0 ? 'On time' : `${late} min`;
    const tunneled  = !isGhost && inTunnel(v);
    const lat = parseFloat(v.lat), lng = parseFloat(v.lng);

    let nextStop = '';
    let isTunneled = isGhost || tunneled;
    if (isGhost) {
      nextStop = nearestTunnelStop(v);
    } else if (tunneled) {
      nextStop = nearestTunnelStop(v);
    } else if (!isNaN(lat) && !isNaN(lng)) {
      nextStop = nearestStop(lat, lng);
    }

    const dir = headingLabel(v.heading);
    const tags = [];
    if (isGhost)         tags.push(`<span class="tag tag-tunnel">Estimated · ${v._direction || 'tunnel'}${v._leg === 'second' ? ' (return)' : ''}</span>`);
    else if (isTunneled) tags.push(`<span class="tag tag-tunnel">Underground</span>`);
    if (dir)             tags.push(`<span class="tag">▷ ${dir}</span>`);
    if (v.trip)          tags.push(`<span class="tag">Trip #${v.trip}</span>`);

    const card = document.createElement('div');
    card.className = 'vcard' + (isGhost ? ' ghost-card' : '');
    if (isGhost) card.dataset.vid = v._id;
    const ghostPct = Math.round((v._fraction || 0) * 100);
    const ghostDir = v._direction || '';
    const ghostBanner = isGhost ? `<div class="ghost-label">Tunnel estimate · ${ghostPct}% ${ghostDir}${v._leg === 'second' ? ' (return)' : ''}</div>` : '';
    card.innerHTML = `
      ${ghostBanner}
      <div class="vcard-hdr">
        <span class="vcard-id">${v.label}</span>
        <span class="late-pill" style="background:${isGhost ? '#1e3a6e' : lateColor}">${isGhost ? 'In tunnel' : lateText}</span>
      </div>
      <div class="next-stop-block" style="border-left:3px solid ${isGhost ? '#93c5fd' : color}">
        <div class="next-stop-label">Next Stop</div>
        <div class="next-stop-name tunnel-stop">${nextStop || '—'}</div>
      </div>
      <div class="vcard-dest">${v.dest || '—'}</div>
      <div class="vcard-tags">${tags.join('')}</div>`;
    grid.appendChild(card);
  }
  const nGhosts = vehicles.filter(v => v._ghost).length;
  const nReal = vehicles.length - nGhosts;
  const ghostSuffix = nGhosts > 0 ? ` + ${nGhosts} estimated` : '';
  setStatus(`${nReal} vehicle${nReal !== 1 ? 's' : ''}${ghostSuffix} · ${fmtTime(now)}`);
}

// ── Completion detection ────────────────────────────────────────────────────

function detectCompletions(curIds, now) {
  if (serverTrackerRunning) return;
  const MIN_DWELL = 2 * 30 * 1000;
  for (const [vid, entry] of Object.entries(liveRegistry)) {
    if (!curIds.has(vid) && (now - entry.firstSeen) >= MIN_DWELL) {
      recordCompletion(entry.route, entry.firstSeen, now);
    }
  }
}

async function recordCompletion(routeKey, startTs, endTs) {
  try {
    await fetch('/api/stats/record', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ route: routeKey, start: startTs, end: endTs }),
    });
  } catch (_) {}
}

// ── Boot ────────────────────────────────────────────────────────────────────

window.addEventListener('DOMContentLoaded', init);
