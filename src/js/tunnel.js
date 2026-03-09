'use strict';

// ── Tunnel / underground constants ─────────────────────────────────────────
const TUNNEL_ROUTES = new Set(['T1','T2','T3','T4','T5']);

const TUNNEL_STOPS = [
  { name:'40th St Portal', lat:39.9495, lng:-75.2033 },
  { name:'37th-Spruce',    lat:39.9510, lng:-75.1966 },
  { name:'36th-Sansom',    lat:39.9539, lng:-75.1945 },
  { name:'33rd St',        lat:39.9548, lng:-75.1895 },
  { name:'30th St',        lat:39.9548, lng:-75.1835 },
  { name:'22nd St',        lat:39.9540, lng:-75.1767 },
  { name:'19th St',        lat:39.9533, lng:-75.1716 },
  { name:'15th St',        lat:39.9525, lng:-75.1653 },
  { name:'13th St',        lat:39.9525, lng:-75.1626 },
];

const TUNNEL_BOX_40TH = { minLat:39.948, maxLat:39.956, minLng:-75.204, maxLng:-75.160 };
const TUNNEL_BOX_36TH = { minLat:39.950, maxLat:39.957, minLng:-75.196, maxLng:-75.160 };
const UNDERGROUND_ZONES = {
  T1: TUNNEL_BOX_36TH,
  T2: TUNNEL_BOX_40TH, T3: TUNNEL_BOX_40TH, T4: TUNNEL_BOX_40TH, T5: TUNNEL_BOX_40TH,
  MFL: { minLat:39.948, maxLat:39.966, minLng:-75.253, maxLng:-75.143 },
  BSL: { minLat:39.870, maxLat:40.050, minLng:-75.175, maxLng:-75.152 },
};

const PORTALS = {
  T1:  {name:'36th St Portal',  lat:39.9553, lng:-75.1942},
  T2:  {name:'40th St Portal',  lat:39.9495, lng:-75.2033},
  T3:  {name:'40th St Portal',  lat:39.9495, lng:-75.2033},
  T4:  {name:'40th St Portal',  lat:39.9495, lng:-75.2033},
  T5:  {name:'40th St Portal',  lat:39.9495, lng:-75.2033},
};

const TUNNEL_EAST_END = {lat:39.9525, lng:-75.1626};

// Tunnel stop sequences (east → west)
const TUNNEL_40TH = [
  {name:'13th St',           lat:39.9525, lng:-75.1626},
  {name:'15th St/City Hall', lat:39.9525, lng:-75.1653},
  {name:'19th St',           lat:39.9533, lng:-75.1716},
  {name:'22nd St',           lat:39.9540, lng:-75.1767},
  {name:'30th St',           lat:39.9548, lng:-75.1835},
  {name:'36th-Sansom',       lat:39.9539, lng:-75.1945},
  {name:'37th-Spruce',       lat:39.9510, lng:-75.1966},
  {name:'40th St Portal',    lat:39.9495, lng:-75.2033},
];

const TUNNEL_36TH = [
  {name:'13th St',           lat:39.9525, lng:-75.1626},
  {name:'15th St/City Hall', lat:39.9525, lng:-75.1653},
  {name:'19th St',           lat:39.9533, lng:-75.1716},
  {name:'22nd St',           lat:39.9540, lng:-75.1767},
  {name:'30th St',           lat:39.9548, lng:-75.1835},
  {name:'33rd St',           lat:39.9548, lng:-75.1895},
  {name:'36th St Portal',    lat:39.9553, lng:-75.1942},
];

// Tunnel path coordinates for interpolation (west→east, fallback if no GTFS shape)
const TUNNEL_PATHS = {
  T1: TUNNEL_36TH.slice().reverse(),
  T2: TUNNEL_40TH.slice().reverse(),
  T3: TUNNEL_40TH.slice().reverse(),
  T4: TUNNEL_40TH.slice().reverse(),
  T5: TUNNEL_40TH.slice().reverse(),
};

const FALLBACK_HALF_TIME = { T1: 441, T2: 660, T3: 619, T4: 665, T5: 618 };

// ── Tunnel estimation constants ────────────────────────────────────────────
const HISTORY_LEN      = 4;
const PORTAL_RADIUS    = 0.002;
const MIN_GHOST_POLLS  = 3;
const GHOST_MAX_AGE_MS = 25 * 60 * 1000;

// ── Tunnel helper functions ────────────────────────────────────────────────

function getHalfTunnelTime(routeKey) {
  const data = tunnelTimesData[routeKey];
  if (data && data.one_way_seconds) return data.one_way_seconds;
  return FALLBACK_HALF_TIME[routeKey] || 600;
}

function getTunnelShapePath(routeKey) {
  if (tunnelShapePaths[routeKey]) return tunnelShapePaths[routeKey];

  const shapeCoords = shapesData[routeKey];
  if (!shapeCoords || shapeCoords.length < 2) {
    tunnelShapePaths[routeKey] = TUNNEL_PATHS[routeKey] || [];
    return tunnelShapePaths[routeKey];
  }

  const zone = UNDERGROUND_ZONES[routeKey];
  if (!zone) {
    tunnelShapePaths[routeKey] = TUNNEL_PATHS[routeKey] || [];
    return tunnelShapePaths[routeKey];
  }

  // Find the longest contiguous underground run, preserving path order
  const portal = PORTALS[routeKey];
  let bestRun = [], curRun = [];
  for (const coord of shapeCoords) {
    const [lat, lng] = coord;
    if (lat >= zone.minLat && lat <= zone.maxLat && lng >= zone.minLng && lng <= zone.maxLng) {
      curRun.push({ lat, lng });
    } else {
      if (curRun.length > bestRun.length) bestRun = curRun;
      curRun = [];
    }
  }
  if (curRun.length > bestRun.length) bestRun = curRun;

  if (bestRun.length < 2) {
    tunnelShapePaths[routeKey] = TUNNEL_PATHS[routeKey] || [];
    return tunnelShapePaths[routeKey];
  }

  // Ensure path goes west (portal) → east (13th St)
  if (portal) {
    const d0 = Math.abs(bestRun[0].lng - portal.lng) + Math.abs(bestRun[0].lat - portal.lat);
    const dN = Math.abs(bestRun[bestRun.length-1].lng - portal.lng) + Math.abs(bestRun[bestRun.length-1].lat - portal.lat);
    if (d0 > dN) bestRun.reverse();
  }

  tunnelShapePaths[routeKey] = bestRun;
  return bestRun;
}

// ── Underground detection ──────────────────────────────────────────────────

function inTunnel(v) {
  if (!TUNNEL_ROUTES.has(v._rkey)) return false;
  const zone = UNDERGROUND_ZONES[v._rkey];
  if (!zone) return false;
  const lat = parseFloat(v.lat), lng = parseFloat(v.lng);
  if (isNaN(lat) || isNaN(lng)) return false;
  return lat >= zone.minLat && lat <= zone.maxLat && lng >= zone.minLng && lng <= zone.maxLng;
}

function nearestTunnelStop(v) {
  const lat = parseFloat(v.lat), lng = parseFloat(v.lng);
  let best = TUNNEL_STOPS[0], bestD = Infinity;
  for (const s of TUNNEL_STOPS) {
    const d = (lat - s.lat) ** 2 + (lng - s.lng) ** 2;
    if (d < bestD) { bestD = d; best = s; }
  }
  return best.name;
}

function isPointUnderground(routeKey, lat, lng) {
  const zone = UNDERGROUND_ZONES[routeKey];
  if (!zone) return false;
  return lat >= zone.minLat && lat <= zone.maxLat
      && lng >= zone.minLng && lng <= zone.maxLng;
}

// ── Geometry helpers ───────────────────────────────────────────────────────

function distLatLng(a, b) {
  const dLat = (a.lat - b.lat) * 111320;
  const dLng = (a.lng - b.lng) * 111320 * Math.cos(a.lat * Math.PI / 180);
  return Math.sqrt(dLat * dLat + dLng * dLng);
}

function pathLength(path) {
  let d = 0;
  for (let i = 1; i < path.length; i++) d += distLatLng(path[i - 1], path[i]);
  return d;
}

function pointAlongPath(path, fraction) {
  const total = pathLength(path);
  let target = fraction * total, acc = 0;
  for (let i = 1; i < path.length; i++) {
    const seg = distLatLng(path[i - 1], path[i]);
    if (acc + seg >= target) {
      const t = (target - acc) / seg;
      return {
        lat: path[i - 1].lat + t * (path[i].lat - path[i - 1].lat),
        lng: path[i - 1].lng + t * (path[i].lng - path[i - 1].lng),
      };
    }
    acc += seg;
  }
  return path[path.length - 1];
}

// ── Vehicle history tracking ───────────────────────────────────────────────

function updateVehicleHistory(vehicles) {
  const now = Date.now();
  const newHistory = {};
  for (const [vid, hist] of Object.entries(vehicleHistory)) {
    const recent = hist.filter(h => (now - h.ts) < 120000);
    if (recent.length > 0) {
      newHistory[vid] = recent;
      newHistory[vid]._label = hist._label;
      newHistory[vid]._dest  = hist._dest;
      newHistory[vid]._late  = hist._late;
      newHistory[vid]._trip  = hist._trip;
    }
  }
  for (const v of vehicles) {
    const lat = parseFloat(v.lat), lng = parseFloat(v.lng);
    if (isNaN(lat) || isNaN(lng)) continue;
    const prev = newHistory[v._id] || [];
    const entry = { lat, lng, ts: now };
    const updated = [...prev.slice(-(HISTORY_LEN - 1)), entry];
    updated._label = v.label;
    updated._dest  = v.dest;
    updated._late  = v.late;
    updated._trip  = v.trip;
    newHistory[v._id] = updated;
  }
  vehicleHistory = newHistory;
}

function isNearPortal(lat, lng, routeKey) {
  const portal = PORTALS[routeKey];
  if (!portal) return { near: false };
  const dWest = Math.abs(lat - portal.lat) + Math.abs(lng - portal.lng);
  const dEast = Math.abs(lat - TUNNEL_EAST_END.lat) + Math.abs(lng - TUNNEL_EAST_END.lng);
  if (dWest < PORTAL_RADIUS) return { near: true, direction: 'eastbound' };
  if (dEast < PORTAL_RADIUS) return { near: true, direction: 'westbound' };
  return { near: false };
}

// ── Ghost vehicle management ───────────────────────────────────────────────

function detectTunnelEntries(currentVehicles) {
  if (!tunnelEstimationOn) { ghostVehicles = {}; return; }

  const now = Date.now();
  const currentIds = new Set(currentVehicles.map(v => v._id));
  const routeKey = selectedRoute?.id;
  if (!TUNNEL_ROUTES.has(routeKey)) return;

  // Prune old ghostedVids entries
  for (const [vid, ts] of Object.entries(ghostedVids)) {
    if (now - ts > 5 * 60 * 1000) delete ghostedVids[vid];
  }

  // Check for vehicles that disappeared near a portal
  for (const [vid, history] of Object.entries(vehicleHistory)) {
    if (currentIds.has(vid)) continue;
    if (ghostVehicles[vid]) continue;
    if (ghostedVids[vid]) continue;
    if (history.length < MIN_GHOST_POLLS) continue;

    const last = history[history.length - 1];
    const age = now - last.ts;
    if (age > 90000) continue;

    const { near, direction } = isNearPortal(last.lat, last.lng, routeKey);
    if (!near) continue;

    // Verify vehicle was moving toward the portal
    if (history.length >= 2) {
      const prev = history[history.length - 2];
      const portal = direction === 'eastbound' ? PORTALS[routeKey] : TUNNEL_EAST_END;
      if (portal) {
        const prevDist = Math.abs(prev.lat - portal.lat) + Math.abs(prev.lng - portal.lng);
        const lastDist = Math.abs(last.lat - portal.lat) + Math.abs(last.lng - portal.lng);
        if (lastDist >= prevDist) continue;
      }
    }

    const shapePath = getTunnelShapePath(routeKey);
    if (!shapePath || shapePath.length < 2) continue;

    const halfTime = getHalfTunnelTime(routeKey);
    const path = direction === 'eastbound' ? shapePath : [...shapePath].reverse();

    ghostedVids[vid] = now;
    ghostVehicles[vid] = {
      route:     routeKey,
      label:     history._label || vid.slice(-4),
      dest:      history._dest  || '',
      late:      history._late  ?? 0,
      trip:      history._trip  || '',
      enterTs:   last.ts,
      leg:       'first',
      direction,
      halfTime,
      pathWE:    shapePath,
      pathEW:    [...shapePath].reverse(),
      path,
      pathLen:   pathLength(path),
    };
  }

  // Update ghost positions and handle round-trip
  for (const [vid, ghost] of Object.entries(ghostVehicles)) {
    if (currentIds.has(vid)) {
      delete ghostVehicles[vid];
      delete ghostedVids[vid];
      continue;
    }

    const totalElapsed = (now - ghost.enterTs) / 1000;

    if ((now - ghost.enterTs) > GHOST_MAX_AGE_MS) {
      delete ghostVehicles[vid];
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
      continue;
    }

    const pos = pointAlongPath(ghost.path, fraction);
    ghost.lat = pos.lat;
    ghost.lng = pos.lng;
    ghost.fraction = fraction;
    ghost.currentDirection = currentDirection;
    ghost.leg = totalElapsed <= ghost.halfTime ? 'first' : 'second';
  }
}

function getGhostVehicles() {
  if (!tunnelEstimationOn) return [];
  return Object.entries(ghostVehicles).map(([vid, g]) => ({
    _id:     vid,
    _rkey:   g.route,
    label:   g.label,
    dest:    g.dest,
    late:    g.late,
    trip:    g.trip,
    lat:     g.lat,
    lng:     g.lng,
    heading: null,
    _ghost:  true,
    _direction: g.currentDirection || g.direction,
    _fraction:  g.fraction,
    _leg:       g.leg,
  }));
}

function toggleTunnelEstimation() {
  tunnelEstimationOn = !tunnelEstimationOn;
  const btn = document.getElementById('tunnelBtn');
  if (btn) {
    btn.textContent = tunnelEstimationOn ? 'Tunnel: On' : 'Tunnel: Off';
    btn.className = 'btn' + (tunnelEstimationOn ? ' btn-on' : '');
  }
  if (!tunnelEstimationOn) ghostVehicles = {};
  if (selectedRoute && activePanel === 'live') fetchNow();
  if (selectedRoute && activePanel === 'map') refreshMapVehicles();
}
