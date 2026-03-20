'use strict';

// ── App state ──────────────────────────────────────────────────────────────
let selectedRoute        = null;
let activePanel          = 'live';
let activeMode           = 'TROLLEY';
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
let refreshIntervalMs    = 7000;
let bandOpacity          = 0.55;

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
let lingeringVids      = {};  // vid → direction ('eastbound'|'westbound')

// ── Init ───────────────────────────────────────────────────────────────────

async function init() {
  buildRouteList();
  await Promise.all([checkTrackerStatus(), loadStaticData(), fetchAlerts()]);
  startAutoRefresh();
  // Refresh alerts every 60s
  setInterval(fetchAlerts, 60000);
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

// ── Alerts ──────────────────────────────────────────────────────────────────

let alertsData = [];         // raw alerts from API
let alertsByRoute = {};      // routeAlertId → [alerts]
let alertsLoaded = false;

async function fetchAlerts() {
  try {
    alertsData = await apiFetch('/api/septa/alerts');
    alertsByRoute = {};
    for (const a of alertsData) {
      for (const rid of (a.routes || [])) {
        (alertsByRoute[rid] = alertsByRoute[rid] || []).push(a);
      }
    }
    alertsLoaded = true;
    buildRouteList();  // refresh badges
    updateAlertsBadge();
  } catch (_) {}
}

function updateAlertsBadge() {
  const badge = document.getElementById('alertsBadge');
  if (!badge) return;
  const alerts = selectedRoute ? getRouteAlerts(selectedRoute) : [];
  const activeAlerts = alerts.filter(a => a.type === 'ALERT');
  if (activeAlerts.length > 0) {
    badge.textContent = activeAlerts.length;
    badge.style.display = '';
  } else {
    badge.style.display = 'none';
  }
}

function getRouteAlerts(route) {
  if (!route || !alertsLoaded) return [];
  const ids = route.alertIds || [route.id];
  const seen = new Set();
  const result = [];
  for (const aid of ids) {
    for (const a of (alertsByRoute[aid] || [])) {
      if (!seen.has(a.alert_id)) {
        seen.add(a.alert_id);
        result.push(a);
      }
    }
  }
  return result;
}

function alertSeverityOrder(sev) {
  if (sev === 'SEVERE') return 0;
  if (sev === 'WARNING') return 1;
  if (sev === 'INFO') return 2;
  return 3;
}

function stripHtml(html) {
  const tmp = document.createElement('div');
  tmp.innerHTML = html;
  return tmp.textContent || tmp.innerText || '';
}

function renderAlertsPanel() {
  const empty = document.getElementById('emptyAlerts');
  const content = document.getElementById('alertsContent');
  if (!selectedRoute) {
    empty.style.display = ''; content.style.display = 'none';
    return;
  }
  // Run tunnel closure detection from alerts for trolley routes
  if (TUNNEL_ROUTES.has(selectedRoute.id)) {
    try {
      detectTunnelClosureFromAlerts();
      updateTunnelClosureBanner();
    } catch (_) {}
  }
  const alerts = getRouteAlerts(selectedRoute);
  if (alerts.length === 0) {
    empty.style.display = '';
    empty.innerHTML = `<div class="empty-icon">&#x2705;</div><div class="empty-title">No active alerts</div><div>No alerts or advisories for ${selectedRoute.label}</div>`;
    content.style.display = 'none';
    return;
  }
  empty.style.display = 'none'; content.style.display = '';
  alerts.sort((a, b) => alertSeverityOrder(a.severity) - alertSeverityOrder(b.severity));
  let html = '';
  for (const a of alerts) {
    const sevClass = a.severity === 'SEVERE' ? 'alert-severe' : a.severity === 'WARNING' ? 'alert-warning' : 'alert-info';
    const typeLabel = a.type || 'ALERT';
    const sevLabel = a.severity && a.severity !== 'UNKNOWN_SEVERITY' ? a.severity : '';
    const badge = sevLabel ? `<span class="alert-severity ${sevClass}">${sevLabel}</span>` : '';
    const subject = a.subject ? `<div class="alert-subject">${a.subject}</div>` : '';
    const msg = a.message ? stripHtml(a.message) : '';
    const routes = (a.routes || []).join(', ');
    const effect = a.effect && a.effect !== 'UNKNOWN_EFFECT' ? a.effect.replace(/_/g, ' ') : '';
    const start = a.start ? new Date(a.start).toLocaleString() : '';
    const end = a.end ? new Date(a.end).toLocaleString() : '';
    const timeStr = end ? `${start} — ${end}` : start ? `Since ${start}` : '';

    html += `<div class="alert-card ${sevClass}">`;
    html += `<div class="alert-header"><span class="alert-type">${typeLabel}</span>${badge}</div>`;
    html += subject;
    if (msg) html += `<div class="alert-message">${msg}</div>`;
    html += `<div class="alert-meta">`;
    if (routes) html += `<span class="alert-routes">Routes: ${routes}</span>`;
    if (effect) html += `<span class="alert-effect">${effect}</span>`;
    html += `</div>`;
    if (timeStr) html += `<div class="alert-time">${timeStr}</div>`;
    html += `</div>`;
  }
  content.innerHTML = html;
}

// ── Helpers ─────────────────────────────────────────────────────────────────

function setStatus(msg) { document.getElementById('statusTxt').textContent = msg; }
function fmtTime(ts) { return new Date(ts).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'}); }
function fmtDate(d)  {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${dd}`;
}
function pctFmt(n)   { return isNaN(n)?'–':n.toFixed(1); }

function getRouteColor(routeId) {
  for (const mode of Object.values(MODES)) {
    const r = mode.routes.find(r => r.id === routeId);
    if (r) return r.color;
  }
  return null;
}

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
  const subwayBanner = document.getElementById('subwayBanner');
  if (subwayBanner) subwayBanner.style.display = mode === 'SUBWAY' ? '' : 'none';
  buildRouteList();
}

function buildRouteList() {
  const routes = MODES[activeMode]?.routes || [];
  const el = document.getElementById('routeList');
  el.innerHTML = `<div class="route-section-label">${activeMode}</div>`;
  for (const r of routes) {
    const alerts = getRouteAlerts(r);
    // Only show sidebar dot for ALERT type (active disruptions), not DETOUR/ADVISORY
    const activeAlerts = alerts.filter(a => a.type === 'ALERT');
    const hasSevere = activeAlerts.some(a => a.severity === 'SEVERE');
    const alertDot = hasSevere
      ? '<span class="alert-dot alert-dot-severe"></span>'
      : activeAlerts.length > 0
        ? '<span class="alert-dot alert-dot-warning"></span>'
        : '';
    const btn = document.createElement('button');
    btn.className = 'route-btn' + (selectedRoute?.id === r.id ? ' active' : '');
    btn.innerHTML = `<span class="route-dot" style="background:${r.color}"></span><span class="route-label">${r.label}</span>${alertDot}`;
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
  buildRouteList();
  updateAlertsBadge();
  const isTunnel = TUNNEL_ROUTES.has(route.id);
  const tunnelBtn = document.getElementById('tunnelBtn');
  if (tunnelBtn) tunnelBtn.style.display = isTunnel ? '' : 'none';
  const bandSlider = document.getElementById('bandOpacity');
  if (bandSlider) bandSlider.style.display = isTunnel ? '' : 'none';
  await fetchRouteStops();
  if (activePanel === 'live')   fetchNow();
  else if (activePanel === 'map')    drawMap();
  else if (activePanel === 'alerts') renderAlertsPanel();
  else if (activePanel === 'stats')  loadStats();
}

/** Sample a shape polyline at approximately every `spacingM` meters. */
function sampleShapeStops(coords, spacingM = 350) {
  if (!coords || coords.length < 2) return [];
  const stops = [{ name: 'Start', lat: coords[0][0], lng: coords[0][1] }];
  let accum = 0;
  for (let i = 1; i < coords.length; i++) {
    const dlat = (coords[i][0] - coords[i-1][0]) * 111320;
    const dlng = (coords[i][1] - coords[i-1][1]) * 111320 * Math.cos(coords[i][0] * Math.PI / 180);
    accum += Math.sqrt(dlat * dlat + dlng * dlng);
    if (accum >= spacingM) {
      stops.push({ name: `Stop ${stops.length}`, lat: coords[i][0], lng: coords[i][1] });
      accum = 0;
    }
  }
  const last = coords[coords.length - 1];
  if (accum > spacingM * 0.3) {
    stops.push({ name: `Stop ${stops.length}`, lat: last[0], lng: last[1] });
  }
  return stops;
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

  // If no official stops, sample the shape at ~every 4th intersection
  if (routeStops.length === 0) {
    const shape = shapesData[key] || shapesData[apiIds[0]];
    if (shape) {
      routeStops = sampleShapeStops(shape);
      routeStopsOrdered = true;
    }
  }
}

function toggleModelCard() {
  modelCardOpen = !modelCardOpen;
  // Toggle all visible legend bodies (whichever tab legend is showing)
  for (const el of document.querySelectorAll('.model-card-body')) {
    el.classList.toggle('hidden', !modelCardOpen);
  }
  for (const el of document.querySelectorAll('#mcArrow, .mc-arrow')) {
    el.textContent = modelCardOpen ? '▾' : '▸';
  }
}

function toggleAbout() {
  const overlay = document.getElementById('aboutOverlay');
  overlay.classList.toggle('open');
}

function closeAboutOverlay(e) {
  // Close when clicking the backdrop (not the panel itself)
  if (e.target === e.currentTarget) toggleAbout();
}

function updateLegendForPanel(panel) {
  const live   = document.getElementById('legendLive');
  const map    = document.getElementById('legendMap');
  const alerts = document.getElementById('legendAlerts');
  if (live)   live.style.display   = panel === 'live'   ? '' : 'none';
  if (map)    map.style.display    = panel === 'map'    ? '' : 'none';
  if (alerts) alerts.style.display = panel === 'alerts' ? '' : 'none';
}

// ── Panel switching ─────────────────────────────────────────────────────────

function setPanel(panel) {
  activePanel = panel;
  const tabs = document.querySelectorAll('.panel-tab');
  const panels = ['live','map','alerts','stats'];
  tabs.forEach((t, i) => t.classList.toggle('active', panels[i] === panel));
  document.getElementById('livePanel').style.display   = panel === 'live'   ? '' : 'none';
  document.getElementById('mapPanel').style.display    = panel === 'map'    ? '' : 'none';
  document.getElementById('alertsPanel').style.display = panel === 'alerts' ? '' : 'none';
  document.getElementById('statsPanel').style.display  = panel === 'stats'  ? '' : 'none';
  updateLegendForPanel(panel);
  if (panel === 'stats'  && selectedRoute) loadStats();
  if (panel === 'live'   && selectedRoute) fetchNow();
  if (panel === 'alerts') renderAlertsPanel();
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

function onRefreshChange() {
  const sel = document.getElementById('refreshSelect');
  refreshIntervalMs = parseInt(sel.value, 10);
  startAutoRefresh();
}

function onBandOpacityChange() {
  const slider = document.getElementById('bandOpacity');
  bandOpacity = parseInt(slider.value, 10) / 100;
  // Update existing band polylines
  for (const band of Object.values(ghostBandLayers)) {
    band.setStyle({ opacity: bandOpacity });
  }
}

function startAutoRefresh() {
  clearInterval(refreshTimer);
  clearInterval(ghostTickTimer);
  refreshTimer = setInterval(() => {
    if (!selectedRoute) return;
    if (activePanel === 'live') fetchNow();
    if (activePanel === 'map')  refreshMapVehicles();
  }, refreshIntervalMs);
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
      ghostReplacedVids.delete(vid);
      if (vehicleMarkers[vid]) {
        vehicleLayerGroup?.removeLayer(vehicleMarkers[vid]);
        delete vehicleMarkers[vid];
      }
      if (ghostBandLayers[vid]) {
        vehicleLayerGroup?.removeLayer(ghostBandLayers[vid]);
        delete ghostBandLayers[vid];
      }
      changed = true;
      continue;
    }

    // Recompute aft/fore/mid positions using same logic as detectTunnelEntries
    const fore = ghostPosition(totalElapsed, ghost);
    const aftElapsed = Math.max(0, totalElapsed - ghost.lingerSec);
    const aft  = ghostPosition(aftElapsed, ghost);
    const midElapsed = (totalElapsed + aftElapsed) / 2;
    const mid  = ghostPosition(midElapsed, ghost);

    // Linger when both aft & fore are done, OR when an eastbound ghost's
    // aft has completed the return (second) leg back to the portal.
    const ebReturnAtPortal = ghost.direction === 'eastbound' && aft.leg === 'second' && aft.fraction >= 1.0;
    if ((fore.done && aft.done) || ebReturnAtPortal) {
      // Estimate exhausted but real vehicle hasn't emerged — freeze at exit portal.
      // Both eastbound (round-trip) and westbound ghosts exit at the west portal.
      if (!ghost._lingersAtPortal) {
        const exitPos = PORTALS[ghost.route];
        if (exitPos) {
          ghost._lingersAtPortal = true;
          ghost.lat = exitPos.lat;
          ghost.lng = exitPos.lng;
          if (vehicleMarkers[vid]) vehicleMarkers[vid].setLatLng([exitPos.lat, exitPos.lng]);
          if (ghostBandLayers[vid]) {
            vehicleLayerGroup?.removeLayer(ghostBandLayers[vid]);
            delete ghostBandLayers[vid];
          }
        }
      }
      changed = true;
      continue;
    }

    ghost.lat = mid.pos.lat;
    ghost.lng = mid.pos.lng;
    ghost.fraction = mid.fraction;
    ghost.currentDirection = mid.direction;
    ghost.leg = mid.leg;
    ghost.aftPos  = aft.pos;
    ghost.forePos = fore.pos;
    ghost.midPos  = mid.pos;
    ghost.aftFraction  = aft.fraction;
    ghost.foreFraction = fore.fraction;
    ghost.bandPath = extractBandPath(ghost, aft, fore);

    // Update map marker position
    if (vehicleMarkers[vid]) {
      vehicleMarkers[vid].setLatLng([mid.pos.lat, mid.pos.lng]);
    }
    // Update band polyline on map
    if (ghostBandLayers[vid] && ghost.bandPath && ghost.bandPath.length >= 2) {
      ghostBandLayers[vid].setLatLngs(ghost.bandPath.map(p => [p.lat, p.lng]));
    }
    changed = true;
  }
  if (changed) {
    if (activePanel === 'live') {
      document.querySelectorAll('.ghost-card').forEach(card => {
        const label = card.querySelector('.ghost-label');
        if (label) {
          const vid = card.dataset?.vid;
          const ghost = vid ? ghostVehicles[vid] : null;
          if (ghost) {
            const aftPct = Math.round((ghost.aftFraction || 0) * 100);
            const forePct = Math.round((ghost.foreFraction || 0) * 100);
            const dir = ghost.currentDirection || ghost.direction;
            label.textContent = `Tunnel estimate · ${aftPct}–${forePct}% ${dir}${ghost.leg === 'second' ? ' (return)' : ''}`;
          }
        }
      });
    }
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
      const raw = results.flatMap(r => r?.bus || []);
      vehicles = processTransitData(raw, selectedRoute.id, selectedRoute.multi);
    }
    updateVehicleHistory(vehicles);
    const isTunnelRoute = TUNNEL_ROUTES.has(selectedRoute.id);
    // Sync ghost state from server (server tracks tunnel entries centrally)
    if (isTunnelRoute) {
      try {
        const ghostResp = await apiFetch('/api/ghosts');
        syncServerGhosts(ghostResp.ghosts || ghostResp);
        lingeringVids = ghostResp.lingering || {};
      } catch (_) {}
    }
    const ghosts = isTunnelRoute ? getGhostVehicles() : [];
    const visible = isTunnelRoute ? vehicles.filter(v => !ghostReplacedVids.has(v._id)) : vehicles;
    const allVehicles = [...visible, ...ghosts];

    renderVehicles(allVehicles);

    // Tunnel closure detection (for trolley routes) — after render so errors don't block it
    if (isTunnelRoute) {
      try {
        // For individual trolley routes, fetch all trolley vehicles for GPS detection
        // so a closure detected on any line is visible from every trolley view.
        let gpsVehicles = vehicles;
        if (!selectedRoute.multi) {
          const allIds = ['T1','T2','T3','T4','T5'];
          const otherIds = allIds.filter(id => !selectedRoute.apiIds.includes(id));
          if (otherIds.length) {
            const otherResults = await Promise.all(
              otherIds.map(id => apiFetch(`/api/septa/transitview?route=${encodeURIComponent(id)}`))
            );
            const otherRaw = otherResults.flatMap(r => r?.bus || []);
            const otherVehicles = processTransitData(otherRaw, 'detect', true);
            gpsVehicles = [...vehicles, ...otherVehicles];
          }
        }
        detectTunnelClosureFromGPS(gpsVehicles);
        detectTunnelClosureFromAlerts();
        updateTunnelClosureBanner();
      } catch (e) { console.warn('tunnel closure detection error:', e); }
    }
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
      heading: t.heading, lat: t.lat, lng: t.lon, trip: t.trainno,
    }));
}

function processTransitData(rawVehicles, routeId, isMulti) {
  return rawVehicles
    .filter(v => v.late < 998 && v.label != null && v.label !== 'None' && String(v.label) !== '0'
              && v.VehicleID != null && v.VehicleID !== 'None' && String(v.VehicleID) !== '0'
              && !String(v.VehicleID).includes('schedBased'))
    .map(v => {
      const trip = v.trip && String(v.trip) !== '0' ? String(v.trip) : '';
      const vid  = v.VehicleID && String(v.VehicleID) !== '0' ? String(v.VehicleID) : '';
      const id   = trip || vid || `${v.label}_${v.lat}_${v.lng}`;
      const actualRoute = isMulti ? (v.route_id || routeId) : routeId;
      return {
        _id: id,
        _rkey: actualRoute,
        label: v.label || vid || '?',
        dest:  v.destination || v.dest || '',
        late:  v.late != null ? +v.late : 0,
        heading: v.heading, lat: v.lat, lng: v.lng, trip: v.trip,
        computed_heading: v.computed_heading,
        computed_direction: v.computed_direction,
        toward_terminus: v.toward_terminus,
        dist_along: v.dist_along,
        first_seen_ts: v.first_seen_ts,
        first_dist_along: v.first_dist_along,
        speed_mps: v.speed_mps,
        shape_total_len: v.shape_total_len,
        // Trip model fields
        trip_id: v.trip_id,
        trip_bearing: v.trip_bearing,
        origin_terminus: v.origin_terminus,
        destination_terminus: v.destination_terminus,
        current_stop: v.current_stop,
        next_stop: v.next_stop,
        trip_start_time: v.trip_start_time,
        trip_elapsed: v.trip_elapsed,
        stops_passed: v.stops_passed,
        stops_remaining: v.stops_remaining,
        _routeLabel: isMulti ? actualRoute : null,
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
    empty.innerHTML = `<div class="empty-icon">🚌</div><div class="empty-title">No live vehicles</div><div>No active vehicles for this route right now.</div>`;
    setStatus(`No vehicles · ${fmtTime(now)}`);
    detectCompletions(new Set(), now);
    return;
  }

  const curIds = new Set(vehicles.map(v => v._id));
  detectCompletions(curIds, now);
  const newReg = {};
  for (const v of vehicles) {
    const ex = liveRegistry[v._id];
    newReg[v._id] = {
      route: v._rkey, firstSeen: ex?.firstSeen ?? now, lastSeen: now,
      firstLat: ex?.firstLat ?? parseFloat(v.lat), firstLng: ex?.firstLng ?? parseFloat(v.lng),
    };
  }
  liveRegistry = newReg;

  // Sort by soonest to arrive (least late first); ghosts after real vehicles
  vehicles.sort((a, b) => {
    if (a._ghost && !b._ghost) return 1;
    if (!a._ghost && b._ghost) return -1;
    const lateDiff = (a.late || 0) - (b.late || 0);
    if (lateDiff !== 0) return lateDiff;
    if (selectedRoute?.multi) {
      const rDiff = (a._rkey || '').localeCompare(b._rkey || '');
      if (rDiff !== 0) return rDiff;
    }
    return a.label.localeCompare(b.label, undefined, { numeric: true });
  });
  grid.style.display = ''; empty.style.display = 'none';
  grid.innerHTML = '';

  for (const v of vehicles) {
    const isGhost = v._ghost === true;
    const late = v.late;
    const lateColor = late <= 0 ? 'var(--green)' : late <= 5 ? 'var(--yellow)' : 'var(--red)';
    const lateText  = late <= 0 ? 'On time' : `${late} min`;
    const tunneled  = !isGhost && inTunnel(v);
    const lat = parseFloat(v.lat), lng = parseFloat(v.lng);
    const vColor = getRouteColor(v._rkey) || color;

    let nextStop = '';
    let isTunneled = isGhost || tunneled;
    if (v.next_stop && !isGhost) {
      nextStop = v.next_stop;
    } else if (isGhost || tunneled) {
      nextStop = nearestTunnelStop(v);
    } else if (!isNaN(lat) && !isNaN(lng)) {
      nextStop = nearestStop(lat, lng);
    }

    const dir = v.destination_terminus || v.toward_terminus || headingLabel(v.computed_heading != null ? v.computed_heading : v.heading);
    const tags = [];
    if (v._routeLabel) tags.push(`<span class="tag" style="background:${vColor};color:#000;font-weight:600">${v._routeLabel}</span>`);
    if (isGhost)         tags.push(`<span class="tag tag-tunnel">Estimated · ${v._direction || 'tunnel'}${v._leg === 'second' ? ' (return)' : ''}</span>`);
    else if (isTunneled) tags.push(`<span class="tag tag-tunnel">Underground</span>`);
    if (dir)             tags.push(`<span class="tag">→ ${dir}</span>`);
    if (v.trip)          tags.push(`<span class="tag">Trip #${v.trip}</span>`);

    const card = document.createElement('div');
    card.className = 'vcard' + (isGhost ? ' ghost-card' : '');
    if (isGhost) card.dataset.vid = v._id;
    const aftPct = isGhost ? Math.round((v._aftFraction || 0) * 100) : 0;
    const forePct = isGhost ? Math.round((v._foreFraction || 0) * 100) : 0;
    const ghostDir = v._direction || '';
    const ghostBanner = isGhost ? `<div class="ghost-label">Tunnel estimate · ${aftPct}–${forePct}% ${ghostDir}${v._leg === 'second' ? ' (return)' : ''}</div>` : '';
    const progressInfo = (!isGhost && v.stops_passed != null && v.stops_remaining != null)
      ? `<div class="vcard-progress">${v.stops_passed} passed · ${v.stops_remaining} remaining</div>` : '';
    const dest = v.destination_terminus || v.dest || '—';
    card.innerHTML = `
      ${ghostBanner}
      <div class="vcard-hdr">
        <span class="vcard-id">${v.label}</span>
        <span class="vcard-dest">${dest}</span>
        <span class="late-pill" style="background:${isGhost ? '#1e3a6e' : lateColor}">${isGhost ? 'In tunnel' : lateText}</span>
      </div>
      <div class="next-stop-block" style="border-left:3px solid ${isGhost ? '#93c5fd' : vColor}">
        <div class="next-stop-label">Next Stop</div>
        <div class="next-stop-name tunnel-stop">${nextStop || '—'}</div>
        ${progressInfo}
      </div>
      <div class="vcard-tags">${tags.join('')}</div>`;
    grid.appendChild(card);
  }
  const nGhosts = vehicles.filter(v => v._ghost).length;
  const nReal = vehicles.length - nGhosts;
  const ghostSuffix = nGhosts > 0 ? ` + ${nGhosts} estimated` : '';
  const vSingular = activeMode === 'TROLLEY' ? 'trolley'
    : activeMode === 'BUS'    ? 'bus'
    : activeMode === 'RAIL'   ? 'train'
    : 'vehicle';
  const vPlural = activeMode === 'BUS' ? 'buses' : vSingular + 's';
  setStatus(`${nReal} ${nReal !== 1 ? vPlural : vSingular}${ghostSuffix} · ${fmtTime(now)}`);
}

// ── Tunnel closure banner ────────────────────────────────────────────────────

function updateTunnelClosureBanner() {
  const status = getTunnelClosureStatus();

  let label, cls, mapCls;
  if (status) {
    if (status.gps) {
      // Trolleys observed in the diversion loop — confirmed closure
      label = 'Trolley tunnel closed';
      cls = 'tunnel-closure-banner official';
      mapCls = 'tunnel-closure-map-banner official';
    } else {
      // Alert/detour only — yellow advisory
      const allTrolley = ['T1','T2','T3','T4','T5'];
      const routes = status.alertRoutes || [];
      const routeStr = routes.length >= allTrolley.length ? 'all trolleys' : routes.join(', ');
      label = `Detour active (${routeStr}) \u2014 see Alerts for details`;
      cls = 'tunnel-closure-banner likely';
      mapCls = 'tunnel-closure-map-banner likely';
    }
  }

  const content = status ? `<span class="tunnel-closure-icon">&#x26A0;</span> ${label}` : '';

  // Live panel: insert as first child
  let liveBanner = document.getElementById('tunnelClosureBanner_live');
  if (!status) {
    if (liveBanner) liveBanner.style.display = 'none';
  } else {
    if (!liveBanner) {
      liveBanner = document.createElement('div');
      liveBanner.id = 'tunnelClosureBanner_live';
      const livePanel = document.getElementById('livePanel');
      livePanel.insertBefore(liveBanner, livePanel.firstChild);
    }
    liveBanner.className = cls;
    liveBanner.innerHTML = content;
    liveBanner.style.display = '';
  }

  // Map panel: use the overlay note area (absolute positioned)
  let mapBanner = document.getElementById('tunnelClosureBanner_map');
  if (!status) {
    if (mapBanner) mapBanner.style.display = 'none';
  } else {
    if (!mapBanner) {
      mapBanner = document.createElement('div');
      mapBanner.id = 'tunnelClosureBanner_map';
      mapBanner.className = 'tunnel-closure-map-banner';
      document.getElementById('mapPanel').appendChild(mapBanner);
    }
    mapBanner.className = mapCls;
    mapBanner.innerHTML = content;
    mapBanner.style.display = '';
  }

  // Alerts panel: insert as first child
  let alertsBanner = document.getElementById('tunnelClosureBanner_alerts');
  if (!status) {
    if (alertsBanner) alertsBanner.style.display = 'none';
  } else {
    if (!alertsBanner) {
      alertsBanner = document.createElement('div');
      alertsBanner.id = 'tunnelClosureBanner_alerts';
      const alertsPanel = document.getElementById('alertsPanel');
      alertsPanel.insertBefore(alertsBanner, alertsPanel.firstChild);
    }
    alertsBanner.className = cls;
    alertsBanner.innerHTML = content;
    alertsBanner.style.display = '';
  }
}

// ── Completion detection ────────────────────────────────────────────────────

function detectCompletions(curIds, now) {
  if (serverTrackerRunning) return;
  const MIN_DWELL = 60000; // 60s minimum time on route before counting as trip
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
