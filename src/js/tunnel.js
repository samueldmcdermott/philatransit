'use strict';

// ── Alert time helpers ────────────────────────────────────────────────────

/** Return true when the alert's active period includes the current time.
 *  Checks both the structured start/end fields and time-of-day windows
 *  embedded in the message text (e.g. "from 10 pm until 5 am"). */
function alertIsActive(a) {
  const now = Date.now();
  if (a.start && now < new Date(a.start).getTime()) return false;
  if (a.end   && now > new Date(a.end).getTime())   return false;

  // Parse time-of-day windows like "from 10 pm until 5 am" from the text
  const text = (a.message || '') + ' ' + (a.subject || '');
  const m = text.match(/from\s+(\d{1,2})\s*(am|pm)\s+(?:until|to)\s+(\d{1,2})\s*(am|pm)/i);
  if (m) {
    let fromH = parseInt(m[1]) % 12 + (m[2].toLowerCase() === 'pm' ? 12 : 0);
    let toH   = parseInt(m[3]) % 12 + (m[4].toLowerCase() === 'pm' ? 12 : 0);
    const curH = new Date().getHours();
    if (fromH > toH) {
      // Overnight window (e.g. 22–5): active if curH >= fromH OR curH < toH
      if (curH < fromH && curH >= toH) return false;
    } else {
      // Daytime window (e.g. 9–14): active if curH >= fromH AND curH < toH
      if (curH < fromH || curH >= toH) return false;
    }
  }
  return true;
}

// ── Tunnel / underground constants ─────────────────────────────────────────
// TUNNEL_ROUTE_IDS is defined in routes.js and loaded from /api/config

const TUNNEL_STOPS = [
  { name:'40th St Portal', lat:39.94939, lng:-75.20333 },
  { name:'37th & Spruce',  lat:39.9510, lng:-75.1969 },
  { name:'36th & Sansom',  lat:39.9539, lng:-75.1947 },
  { name:'36th St Portal', lat:39.9553, lng:-75.1942 },
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
  T1:  {name:'36th St Portal',  lat:39.9553,   lng:-75.1942  },
  T2:  {name:'40th St Portal',  lat:39.94939,  lng:-75.20333  },
  T3:  {name:'40th St Portal',  lat:39.94939,  lng:-75.20333  },
  T4:  {name:'40th St Portal',  lat:39.94939,  lng:-75.20333  },
  T5:  {name:'40th St Portal',  lat:39.94939,  lng:-75.20333  },
};

const TUNNEL_EAST_END = {lat:39.9525, lng:-75.1626};

// Tight bounding box around the 40th St tunnel mouth (T2-T5 portal entrance).
const MOUTH_40TH_BOX = {
  minLat: 39.949499, maxLat: 39.949647,
  minLng: -75.203387, maxLng: -75.202749,
};
const MOUTH_40TH_ROUTES = new Set(['T2','T3','T4','T5']);

// Tunnel stop sequences (east → west)
const TUNNEL_40TH = [
  {name:'13th St',           lat:39.9525, lng:-75.1626},
  {name:'15th St/City Hall', lat:39.9525, lng:-75.1653},
  {name:'19th St',           lat:39.9533, lng:-75.1716},
  {name:'22nd St',           lat:39.9540, lng:-75.1767},
  {name:'30th St',           lat:39.9548, lng:-75.1835},
  {name:'33rd St',           lat:39.9548, lng:-75.1895},
  {name:'36th & Sansom',     lat:39.9539, lng:-75.1947},
  {name:'37th & Spruce',     lat:39.9510, lng:-75.1969},
  {name:'40th St Portal',    lat:39.94939,  lng:-75.20333  },
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

// ── Surface detour route geometry ────────────────────────────────────────
// When the tunnel is closed, T2-T5 detour via surface streets between
// the 40th St Portal area and 40th & Market.  Two one-way paths (from GTFS).
// Westbound (portal → Market): up 42nd, along Spruce, north on 38th, to Market, west to 40th
const DETOUR_PATH_WB = [
  [39.949749,-75.207962],[39.949756,-75.207844],[39.949784,-75.207141],
  [39.950479,-75.207185],[39.950950,-75.207167],[39.951162,-75.207128],
  [39.951644,-75.207039],[39.952244,-75.206921],[39.952082,-75.205584],
  [39.951918,-75.204388],[39.951755,-75.203075],[39.951457,-75.200750],
  [39.951268,-75.199280],[39.951260,-75.199214],[39.951252,-75.199153],
  [39.951958,-75.199002],[39.952481,-75.198889],[39.953188,-75.198741],
  [39.953613,-75.198651],[39.953862,-75.198599],[39.954377,-75.198490],
  [39.954736,-75.198415],[39.955152,-75.198328],[39.955891,-75.198178],
  [39.956233,-75.198105],[39.956303,-75.198089],[39.956669,-75.198006],
  [39.956821,-75.197969],[39.957400,-75.197783],[39.957408,-75.197842],
  [39.957415,-75.197904],[39.957485,-75.198428],[39.957543,-75.199015],
  [39.957580,-75.199495],[39.957687,-75.200392],[39.957758,-75.200981],
  [39.957865,-75.201833],[39.957820,-75.201831],[39.957242,-75.201951],
];
// Eastbound (Market → 42nd): south on 40th, along Spruce, south on 42nd
const DETOUR_PATH_EB = [
  [39.957159,-75.201929],[39.956399,-75.202080],[39.955629,-75.202269],
  [39.954860,-75.202440],[39.954099,-75.202599],[39.953670,-75.202700],
  [39.952989,-75.202850],[39.952369,-75.202970],[39.952089,-75.203020],
  [39.951749,-75.203079],[39.951919,-75.204249],[39.952060,-75.205569],
  [39.952230,-75.206909],[39.951710,-75.207030],[39.951170,-75.207139],
  [39.950820,-75.207189],[39.950479,-75.207179],[39.949790,-75.207150],
];

// T1 detour spur: 41st & Lancaster ↔ 40th & Filbert via 41st & Filbert.
// When T1 is diverted, it stays on Lancaster Ave and connects to the T2-T5
// loop at Filbert & 40th instead of entering at the 36th St portal.
// Single segment (bidirectional): 40th & Filbert → 41st & Filbert → 41st & Lancaster
const DETOUR_T1_SPUR = [
  [39.9579,-75.2019],  // 40th & Filbert (connects to T2-T5 loop)
  [39.9582,-75.2050],  // 41st & Filbert
  [39.9650,-75.2055],  // 41st & Lancaster
];

// Clickable stops on the detour route (vertices of the T2-T5 CCW loop)
const DETOUR_STOPS = [
  { name: '42nd & Spruce',   lat: 39.9522, lng: -75.2069, routes: ['T2','T3','T4','T5'] },
  { name: 'Spruce & 38th',   lat: 39.9513, lng: -75.1994, routes: ['T2','T3','T4','T5'] },
  { name: 'Filbert & 38th',  lat: 39.9574, lng: -75.1981, routes: ['T1','T2','T3','T4','T5'] },
  { name: 'Filbert & 40th',  lat: 39.9578, lng: -75.2018, routes: ['T1','T2','T3','T4','T5'] },
  { name: '40th & Market',   lat: 39.9572, lng: -75.2019, routes: ['T1','T2','T3','T4','T5'] },
];

// ── CCW detour loop for T2-T5 next-to-arrive calculations ────────────────
// Single counterclockwise path: entry from Baltimore Ave west of 42nd →
// N on 42nd → E on Spruce → N on 38th → W on Filbert → S on 40th →
// W on Spruce → back to 42nd → exit to Baltimore Ave.
// Built by concatenating DETOUR_PATH_WB (42nd→38th→Filbert→40th) +
// DETOUR_PATH_EB (40th→Spruce→42nd), deduplicating the junction point.
const DETOUR_LOOP_PATH = [
  ...DETOUR_PATH_WB,
  ...DETOUR_PATH_EB.slice(1),  // skip first point (same as last of WB)
];

// Detour loop stops in CCW order (matching DETOUR_LOOP_PATH traversal)
const DETOUR_LOOP_STOPS = [
  { name: '42nd & Spruce',   lat: 39.9522, lng: -75.2069 },
  { name: 'Spruce & 38th',   lat: 39.9513, lng: -75.1994 },
  { name: 'Filbert & 38th',  lat: 39.9574, lng: -75.1981 },
  { name: 'Filbert & 40th',  lat: 39.9578, lng: -75.2018 },
];

// ── Terminus coordinates (must match server's shapes.py TERMINI) ──────────
// Used to orient client-side shapes so index 0 = start (outer) terminus,
// ensuring movingForward matches the server's computed_direction.
const SHAPE_TERMINI = {
  T1: { startLat: 39.9838, startLng: -75.2460, endLat: 39.9525, endLng: -75.1626 },
  T2: { startLat: 39.9440, startLng: -75.2463, endLat: 39.9525, endLng: -75.1626 },
  T3: { startLat: 39.9191, startLng: -75.2624, endLat: 39.9525, endLng: -75.1626 },
  T4: { startLat: 39.9171, startLng: -75.2464, endLat: 39.9525, endLng: -75.1626 },
  T5: { startLat: 39.9140, startLng: -75.2426, endLat: 39.9525, endLng: -75.1626 },
  G1: { startLat: 39.9702, startLng: -75.2446, endLat: 39.9843, endLng: -75.0996 },
  MFL: { startLat: 39.9623, startLng: -75.2586, endLat: 40.0229, endLng: -75.0779 },
  BSL: { startLat: 39.9054, startLng: -75.1739, endLat: 40.0419, endLng: -75.1368 },
};

// ── Route spur definitions ──────────────────────────────────────────────
// Non-revenue shape prefixes.  Drawn as thin dashed lines; stripped from
// the projection path used for next-to-arrive calculations.
// cutoffIndex must stay in sync with _SHAPE_TRIM in shapes.py.
const ROUTE_SPURS = {
  // T2: Elmwood Loop spur (0-103) + backtrack to 61st terminus (103-174).
  T2: { end: 'start', cutoffIndex: 174 },
};

// ── Tunnel closure detection ──────────────────────────────────────────────
// When the tunnel is closed, trolleys divert to the surface via 42nd St,
// Spruce, 38th, Lancaster/Filbert, 40th & Market.  Vehicles in this zone
// are NORTH of Baltimore Ave, an area they never reach during normal ops.
const DETOUR_ZONE = { minLat: 39.952, maxLat: 39.970, minLng: -75.210, maxLng: -75.195 };

let tunnelClosureState = { gps: false, alert: false, reopenTime: null, alertRoutes: [] };

function isInDetourZone(lat, lng) {
  return lat >= DETOUR_ZONE.minLat && lat <= DETOUR_ZONE.maxLat
      && lng >= DETOUR_ZONE.minLng && lng <= DETOUR_ZONE.maxLng;
}

/** Detect tunnel closure from vehicle positions.  Returns true if any
 *  T2-T5 trolley is currently in the surface-detour zone.
 *  T1 is excluded — it uses the 36th St portal and its normal surface
 *  route on Lancaster Ave runs through the detour zone. */
function detectTunnelClosureFromGPS(vehicles) {
  const detourRoutes = new Set(['T2','T3','T4','T5']);
  for (const v of vehicles) {
    if (!detourRoutes.has(v._rkey)) continue;
    if (v._ghost) continue;
    // Prefer server-provided on_detour flag (has hysteresis), fall back to zone check
    if (v.on_detour) {
      tunnelClosureState.gps = true;
      return true;
    }
    const lat = tripLat(v), lng = tripLng(v);
    if (isNaN(lat) || isNaN(lng)) continue;
    if (isInDetourZone(lat, lng)) {
      tunnelClosureState.gps = true;
      return true;
    }
  }
  tunnelClosureState.gps = false;
  return false;
}

/** Detect tunnel closure from alerts.  Looks for ALERT/DETOUR items
 *  on trolley routes whose message mentions tunnel/station closure keywords.
 *  Collects affected route names for display in the banner. */
function detectTunnelClosureFromAlerts() {
  tunnelClosureState.alert = false;
  tunnelClosureState.reopenTime = null;
  tunnelClosureState.alertRoutes = [];
  if (typeof alertsData === 'undefined' || !alertsData.length) return false;

  const trolleyAlertIds = new Set(['T1','T2','T3','T4','T5']);
  const closureKw = /tunnel|15th\s*st|13th\s*st|subway.?surface|shuttle|divert|diversion|bypass|not\s+serv/i;
  const reopenKw  = /resum|restor|reopen|back\s+in\s+service|normal\s+service/i;

  const affectedRoutes = new Set();

  for (const a of alertsData) {
    if (a.type !== 'ALERT' && a.type !== 'DETOUR' && a.type !== 'ADVISORY') continue;
    if (!alertIsActive(a)) continue;
    if (!a.routes) continue;
    const matchedRoutes = a.routes.filter(r => trolleyAlertIds.has(r));
    if (!matchedRoutes.length) continue;
    const text = (a.message || '') + ' ' + (a.subject || '');

    const isDetour = a.type === 'DETOUR';
    const hasClosureKeyword = closureKw.test(text);
    const hasReopenKeyword = reopenKw.test(text);

    if (isDetour || (hasClosureKeyword && !hasReopenKeyword)) {
      for (const r of matchedRoutes) affectedRoutes.add(r);
    }
  }

  if (affectedRoutes.size > 0) {
    tunnelClosureState.alert = true;
    tunnelClosureState.alertRoutes = [...affectedRoutes].sort();
    return true;
  }
  return false;
}

function getTunnelClosureStatus() {
  const gps = tunnelClosureState.gps;
  const alert = tunnelClosureState.alert;
  if (!gps && !alert) return null;
  return {
    gps,
    alert,
    alertRoutes: tunnelClosureState.alertRoutes,
  };
}

// ── Tunnel estimation constants ────────────────────────────────────────────
const HISTORY_LEN         = 6;       // keep more history for linger detection
const GHOST_MAX_AGE_MS    = 25 * 60 * 1000;
const LINGER_RADIUS       = 0.002;   // distance to portal to be "near" it (T1 and east end)
const LINGER_TIME_MS      = 60000;   // 60s of frozen GPS to trigger ghost
const STATIONARY_THRESH   = 0.0005;  // max position change to count as "frozen"

// Destinations that indicate eastbound (into tunnel from west portal)
const EASTBOUND_DESTS = ['13th', 'market'];

/** Return true if (lat, lng) is inside the triangle defined by tri (array of 3 {lat,lng}). */
function pointInTriangle(lat, lng, tri) {
  const [a, b, c] = tri;
  const d1 = (lat - b.lat) * (a.lng - b.lng) - (a.lat - b.lat) * (lng - b.lng);
  const d2 = (lat - c.lat) * (b.lng - c.lng) - (b.lat - c.lat) * (lng - c.lng);
  const d3 = (lat - a.lat) * (c.lng - a.lng) - (c.lat - a.lat) * (lng - a.lng);
  const hasNeg = (d1 < 0) || (d2 < 0) || (d3 < 0);
  const hasPos = (d1 > 0) || (d2 > 0) || (d3 > 0);
  return !(hasNeg && hasPos);
}

// ── Portal linger tracking ───────────────────────────────────────────────
// vid → { firstTs, route, direction, lat, lng }
let portalLingerMap   = {};
// Vehicles whose real API entries should be hidden (replaced by ghost)
let ghostReplacedVids = new Set();

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
  if (!TUNNEL_ROUTE_IDS.has(v._rkey)) return false;
  const zone = UNDERGROUND_ZONES[v._rkey];
  if (!zone) return false;
  const lat = tripLat(v), lng = tripLng(v);
  if (isNaN(lat) || isNaN(lng)) return false;
  return lat >= zone.minLat && lat <= zone.maxLat && lng >= zone.minLng && lng <= zone.maxLng;
}

function nearestTunnelStop(v) {
  const lat = tripLat(v), lng = tripLng(v);
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
  // For T2-T5, use the tight mouth box at the tunnel entrance.
  // Points west of the box's east edge are underground only if inside the box;
  // points east of it use the rectangular UNDERGROUND_ZONES box as before.
  if (MOUTH_40TH_ROUTES.has(routeKey)) {
    if (lng < MOUTH_40TH_BOX.maxLng) {
      return lat >= MOUTH_40TH_BOX.minLat && lat <= MOUTH_40TH_BOX.maxLat
          && lng >= MOUTH_40TH_BOX.minLng && lng <= MOUTH_40TH_BOX.maxLng;
    }
  }
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
    const recent = hist.filter(h => (now - h.ts) < 180000);
    if (recent.length > 0) {
      newHistory[vid] = recent;
      newHistory[vid]._label = hist._label;
      newHistory[vid]._dest  = hist._dest;
      newHistory[vid]._late  = hist._late;
      newHistory[vid]._trip  = hist._trip;
      newHistory[vid]._rkey  = hist._rkey;
      newHistory[vid]._toward_destination = hist._toward_destination;
    }
  }
  for (const v of vehicles) {
    const lat = tripLat(v), lng = tripLng(v);
    if (isNaN(lat) || isNaN(lng)) continue;
    const prev = newHistory[v._id] || [];
    const entry = { lat, lng, ts: now };
    const updated = [...prev.slice(-(HISTORY_LEN - 1)), entry];
    updated._label = v.label;
    updated._dest  = v.destination || v.meta?.headsign || '';
    updated._late  = tripDelay(v);
    updated._trip  = v.trip_id || '';
    updated._rkey  = v._rkey;
    updated._toward_destination = v.toward_destination;
    newHistory[v._id] = updated;
  }
  vehicleHistory = newHistory;
}

// ── Direction detection ──────────────────────────────────────────────────

function isHeadingEast(dest, v) {
  // Prefer toward_destination from server (shape-based)
  if (v && v.toward_destination != null) {
    return v.toward_destination;  // toward_destination = toward 13th St = eastbound
  }
  // Fallback to destination keyword detection
  if (!dest) return false;
  const d = dest.toLowerCase();
  return EASTBOUND_DESTS.some(k => d.includes(k));
}

function getPortalAndDirection(lat, lng, routeKey, dest, v) {
  // Check if near a west portal AND heading eastbound into tunnel.
  // T2-T5 use the tight mouth box; T1 uses a radius.
  if (MOUTH_40TH_ROUTES.has(routeKey)) {
    if (lat >= MOUTH_40TH_BOX.minLat && lat <= MOUTH_40TH_BOX.maxLat
        && lng >= MOUTH_40TH_BOX.minLng && lng <= MOUTH_40TH_BOX.maxLng
        && isHeadingEast(dest, v)) {
      return { near: true, direction: 'eastbound', portal: PORTALS[routeKey] };
    }
  } else {
    const portal = PORTALS[routeKey];
    if (portal) {
      const dWest = Math.abs(lat - portal.lat) + Math.abs(lng - portal.lng);
      if (dWest < LINGER_RADIUS && isHeadingEast(dest, v)) {
        return { near: true, direction: 'eastbound', portal };
      }
    }
  }
  // Check if near 13th St (east end) AND heading westbound into tunnel
  const dEast = Math.abs(lat - TUNNEL_EAST_END.lat) + Math.abs(lng - TUNNEL_EAST_END.lng);
  if (dEast < LINGER_RADIUS && !isHeadingEast(dest, v)) {
    return { near: true, direction: 'westbound', portal: TUNNEL_EAST_END };
  }
  return { near: false };
}

// ── Ghost vehicle management ───────────────────────────────────────────────

/** Sync local ghost state from server-side ghost tracker (/api/ghosts). */
function syncServerGhosts(serverGhosts) {
  if (!tunnelEstimationOn) {
    ghostVehicles = {};
    ghostReplacedVids.clear();
    return;
  }

  const now = Date.now();
  const serverVids = new Set();

  for (const sg of serverGhosts) {
    const vid = sg.vid;
    serverVids.add(vid);

    if (ghostVehicles[vid]) {
      // Already tracking — update metadata but don't reset interpolation
      ghostVehicles[vid].label = sg.label;
      ghostVehicles[vid].dest = sg.dest;
      ghostVehicles[vid].late = sg.late;
      continue;
    }

    // New ghost from server — build local object for interpolation
    const shapePath = getTunnelShapePath(sg.route);
    if (!shapePath || shapePath.length < 2) continue;
    const halfTime = getHalfTunnelTime(sg.route);
    const path = sg.direction === 'eastbound' ? shapePath : [...shapePath].reverse();

    ghostReplacedVids.add(vid);
    ghostVehicles[vid] = {
      route: sg.route, label: sg.label, dest: sg.dest, late: sg.late,
      trip: sg.trip, _routeLabel: sg.route,
      _entryLat: sg.entryLat, _entryLng: sg.entryLng,
      enterTs: sg.enterTs, lingerSec: sg.lingerSec,
      leg: 'first', direction: sg.direction, halfTime,
      pathWE: shapePath, pathEW: [...shapePath].reverse(),
      path, pathLen: pathLength(path),
    };

    // Compute current position
    const totalElapsed = (now - sg.enterTs) / 1000;
    const fore = ghostPosition(totalElapsed, ghostVehicles[vid]);
    const aftElapsed = Math.max(0, totalElapsed - sg.lingerSec);
    const aft = ghostPosition(aftElapsed, ghostVehicles[vid]);
    const midElapsed = (totalElapsed + aftElapsed) / 2;
    const mid = ghostPosition(midElapsed, ghostVehicles[vid]);

    const g = ghostVehicles[vid];
    // Westbound: if aft has reached the portal (fraction 1.0), linger at portal.
    // Eastbound return: if aft has reached the portal on the second (westbound)
    // leg, linger at the portal — the ghost did a round trip and exits west.
    const wbAftAtPortal = sg.direction === 'westbound' && aft.fraction >= 1.0;
    const ebReturnAtPortal = sg.direction === 'eastbound' && aft.leg === 'second' && aft.fraction >= 1.0;
    if ((fore.done && aft.done) || wbAftAtPortal || ebReturnAtPortal) {
      // Both eastbound (round-trip) and westbound ghosts exit at the west portal.
      const exitPos = PORTALS[sg.route];
      if (exitPos) { g._lingersAtPortal = true; g.lat = exitPos.lat; g.lng = exitPos.lng; }
    } else {
      g._lingersAtPortal = false;
      g.lat = mid.pos.lat; g.lng = mid.pos.lng;
      g.fraction = mid.fraction; g.currentDirection = mid.direction; g.leg = mid.leg;
      g.aftPos = aft.pos; g.forePos = fore.pos; g.midPos = mid.pos;
      g.aftFraction = aft.fraction; g.foreFraction = fore.fraction;
      g.bandPath = extractBandPath(g, aft, fore);
    }
  }

  // Remove ghosts no longer reported by server
  for (const vid of Object.keys(ghostVehicles)) {
    if (!serverVids.has(vid)) {
      delete ghostVehicles[vid];
      ghostReplacedVids.delete(vid);
    }
  }
}

// Compute ghost position for a given elapsed time (handles round-trip)
function ghostPosition(elapsedSec, ghost) {
  let fraction, direction, leg, path;
  if (elapsedSec <= ghost.halfTime) {
    fraction = elapsedSec / ghost.halfTime;
    direction = ghost.direction;
    leg = 'first';
    path = ghost.direction === 'eastbound' ? ghost.pathWE : ghost.pathEW;
  } else {
    const secondElapsed = elapsedSec - ghost.halfTime;
    fraction = secondElapsed / ghost.halfTime;
    direction = ghost.direction === 'eastbound' ? 'westbound' : 'eastbound';
    leg = 'second';
    path = ghost.direction === 'eastbound' ? ghost.pathEW : ghost.pathWE;
  }
  fraction = Math.min(fraction, 1.0);

  // Westbound ghosts: clamp position at portal (never pass west of it)
  // The westbound first-leg path goes east→west, so fraction 1.0 = portal.
  // On second leg the ghost would go back east — but the trolley emerges instead.
  if (ghost.direction === 'westbound' && leg === 'second') {
    fraction = 1.0;
    path = ghost.pathEW;  // use first-leg path
    leg = 'first';
    direction = 'westbound';
  }

  const done = fraction >= 1.0 && elapsedSec > ghost.halfTime * 2;
  const pos = pointAlongPath(path, fraction);
  return { pos, fraction, direction, leg, path, done };
}

// Extract the path coordinates between aft and fore for drawing the band
function extractBandPath(ghost, aft, fore) {
  // Both on same leg and same path
  if (aft.leg === fore.leg) {
    return extractSubPath(aft.path, aft.fraction, fore.fraction);
  }
  // Aft on first leg, fore on second leg — band wraps around the turnaround
  const firstPath = aft.path;
  const secondPath = fore.path;
  const tail = extractSubPath(firstPath, aft.fraction, 1.0);
  const head = extractSubPath(secondPath, 0, fore.fraction);
  return [...tail, ...head];
}

// Extract a subsection of a path between two fractions
function extractSubPath(path, fracStart, fracEnd) {
  if (!path || path.length < 2) return [];
  const total = pathLength(path);
  const startDist = fracStart * total;
  const endDist = fracEnd * total;
  const pts = [];
  let acc = 0;

  // Add interpolated start point
  for (let i = 1; i < path.length; i++) {
    const seg = distLatLng(path[i - 1], path[i]);
    if (acc + seg >= startDist && pts.length === 0) {
      const t = (startDist - acc) / seg;
      pts.push({
        lat: path[i - 1].lat + t * (path[i].lat - path[i - 1].lat),
        lng: path[i - 1].lng + t * (path[i].lng - path[i - 1].lng),
      });
    }
    if (pts.length > 0 && acc + seg < endDist) {
      pts.push(path[i]);
    }
    if (acc + seg >= endDist) {
      const t = (endDist - acc) / seg;
      pts.push({
        lat: path[i - 1].lat + t * (path[i].lat - path[i - 1].lat),
        lng: path[i - 1].lng + t * (path[i].lng - path[i - 1].lng),
      });
      break;
    }
    acc += seg;
  }
  return pts;
}

function getGhostVehicles() {
  if (!tunnelEstimationOn) return [];
  const routeKey = selectedRoute?.id;
  return Object.entries(ghostVehicles)
    .filter(([_, g]) => routeKey === 'T-ALL' || g.route === routeKey)
    .map(([vid, g]) => ({
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
    _routeLabel: g._routeLabel || null,
    _bandPath:  g.bandPath || [],
    _aftPos:    g.aftPos,
    _forePos:   g.forePos,
    _midPos:    g.midPos,
    _aftFraction:  g.aftFraction,
    _foreFraction: g.foreFraction,
    _enterLat:  g._entryLat,
    _enterLng:  g._entryLng,
    _enterTs:   g.enterTs,
    _lingersAtPortal: g._lingersAtPortal || false,
  }));
}

function toggleTunnelEstimation() {
  tunnelEstimationOn = !tunnelEstimationOn;
  const btn = document.getElementById('tunnelBtn');
  if (btn) {
    btn.textContent = tunnelEstimationOn ? 'Tunnel: On' : 'Tunnel: Off';
    btn.className = 'btn' + (tunnelEstimationOn ? ' btn-on' : '');
  }
  if (!tunnelEstimationOn) {
    ghostVehicles = {};
    ghostReplacedVids.clear();
  }
  if (selectedRoute && activePanel === 'live') fetchNow();
  if (selectedRoute && activePanel === 'map') refreshMapVehicles();
}
