'use strict';

// ── Map initialization ─────────────────────────────────────────────────────

function initLeaflet() {
  if (mapInitialized) return;
  leafletMap = L.map('mapContainer', { zoomControl: true, preferCanvas: true });
  L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
    attribution: '&copy; OpenStreetMap contributors, &copy; CARTO',
    maxZoom: 19,
  }).addTo(leafletMap);
  routeLayerGroup   = L.layerGroup().addTo(leafletMap);
  stopLayerGroup    = L.layerGroup().addTo(leafletMap);
  vehicleLayerGroup = L.layerGroup().addTo(leafletMap);
  leafletMap.setView([39.9526, -75.1652], 13);
  mapInitialized = true;
}

// ── Draw map ────────────────────────────────────────────────────────────────

async function drawMap() {
  if (!selectedRoute) return;
  initLeaflet();

  const routeKey = selectedRoute.id;
  const gtfsId   = selectedRoute.gtfs || routeKey;
  const color    = selectedRoute.color || '#2f69f3';

  document.getElementById('emptyMap').style.display = 'none';
  document.getElementById('mapContainer').style.display = '';

  routeLayerGroup.clearLayers();
  stopLayerGroup.clearLayers();
  vehicleLayerGroup.clearLayers();
  if (detourLayerGroup) { detourLayerGroup.clearLayers(); }
  vehicleMarkers = {};
  ghostBandLayers = {};
  stopMarkerInfos = [];
  routePathIndices = {};
  stopDirFilters = {};
  lastStopVehicles = [];
  stopPredictions = {};

  // Route path — for multi-route (T-ALL), draw each sub-route
  const subRoutes = selectedRoute.multi
    ? ['T1','T2','T3','T4','T5'].map(id => ({ id, gtfs: id, color: getRouteColor(id) || color }))
    : [{ id: routeKey, gtfs: gtfsId, color }];

  let hasGtfsShapes = false;
  const bounds = [];
  const allStops = new Map();

  for (const sub of subRoutes) {
    const shapeCoords = shapesData[sub.gtfs] || shapesData[sub.id];
    if (shapeCoords && shapeCoords.length > 1) {
      hasGtfsShapes = true;
      const spur = ROUTE_SPURS[sub.id];
      let mainCoords = shapeCoords;
      if (spur) {
        // Draw spur as a thin line, main route as normal
        const ci = spur.cutoffIndex;
        if (spur.end === 'start') {
          drawThinPath(shapeCoords.slice(0, ci + 1), sub.color);
          mainCoords = shapeCoords.slice(ci);
          drawSegmentedPath(mainCoords, sub.id, sub.color);
        } else {
          mainCoords = shapeCoords.slice(0, ci + 1);
          drawSegmentedPath(mainCoords, sub.id, sub.color);
          drawThinPath(shapeCoords.slice(ci), sub.color);
        }
      } else {
        drawSegmentedPath(shapeCoords, sub.id, sub.color);
      }
      // Include shape endpoints in bounds so map shows full route
      bounds.push(mainCoords[0]);
      bounds.push(mainCoords[mainCoords.length - 1]);
    }
  }

  // Draw surface detour path when tunnel is closed (for T2-T5 / T-ALL)
  const isTunnelRoute = TUNNEL_ROUTES.has(routeKey);
  if (isTunnelRoute) {
    drawDetourPaths(routeKey, color, bounds);
  }

  // For single route, fall back to station list if no GTFS shapes
  if (!selectedRoute.multi) {
    const shapeCoords = shapesData[gtfsId] || shapesData[routeKey];
    const stationList = routeStops.length > 0 ? routeStops : (HARDCODED_STATIONS[routeKey] || []);
    if (!hasGtfsShapes && stationList.length > 1) {
      const inOrder = (stationList === routeStops && routeStopsOrdered) || HARDCODED_STATIONS[routeKey];
      const coords  = (inOrder ? stationList : orderStops(stationList)).map(s => [s.lat, s.lng]);
      drawSegmentedPath(coords, routeKey, color);
    }
    for (const s of stationList) {
      allStops.set(`${s.lat},${s.lng}`, { ...s, routeKey, routes: [routeKey] });
    }
  } else {
    // For multi-route, gather stops from HARDCODED_STATIONS for each sub-route
    for (const sub of subRoutes) {
      const stations = HARDCODED_STATIONS[sub.id] || [];
      for (const s of stations) {
        const key = `${s.lat},${s.lng}`;
        const existing = allStops.get(key);
        if (existing) {
          if (!existing.routes.includes(sub.id)) existing.routes.push(sub.id);
        } else {
          allStops.set(key, { ...s, routeKey: sub.id, color: sub.color, routes: [sub.id] });
        }
      }
    }
  }

  // Stop markers — assign nearest stop IDs from stopsData for prediction lookups
  for (const s of allStops.values()) {
    const rk = s.routeKey || routeKey;
    const sc = s.color || color;
    const underground = isPointUnderground(rk, s.lat, s.lng);
    if (!s.stopId) s.stopId = findNearestStopId(s.lat, s.lng);
    const marker = L.circleMarker([s.lat, s.lng], {
      radius: 7, color: '#0c0e12', weight: 1.5,
      fillColor: underground ? '#4a7fff' : sc, fillOpacity: 0.9,
    }).bindPopup(`<b>${s.name}</b>${underground ? '<br><i>Underground</i>' : ''}`);
    stopLayerGroup.addLayer(marker);
    bounds.push([s.lat, s.lng]);
    stopMarkerInfos.push({ marker, stop: s });
  }

  // Defer fitBounds until browser has laid out the (previously hidden) container.
  // On first open, the map container goes from display:none → visible, but dimensions
  // aren't available until after reflow. setTimeout ensures layout is complete.
  if (bounds.length) {
    const fitBoundsNow = () => {
      leafletMap.invalidateSize();
      leafletMap.fitBounds(bounds, { padding: [40, 40], maxZoom: 14 });
    };
    setTimeout(fitBoundsNow, 80);
  }

  // Note if no GTFS shapes
  const noteEl = document.getElementById('noGtfsNote');
  if (!hasGtfsShapes) {
    if (!noteEl) {
      const note = L.control({ position: 'topleft' });
      note.onAdd = () => {
        const d = L.DomUtil.create('div', 'map-no-gtfs');
        d.id = 'noGtfsNote';
        d.innerHTML = 'Run <code>python3 scripts/build_gtfs.py</code> for exact route shapes';
        return d;
      };
      note.addTo(leafletMap);
    }
  }

  await refreshMapVehicles();
}

function drawSegmentedPath(coords, routeKey, color) {
  if (coords.length < 2) return;
  let segStart = 0;
  let segUnder = isPointUnderground(routeKey, coords[0][0], coords[0][1]);

  for (let i = 1; i <= coords.length; i++) {
    const atEnd  = i === coords.length;
    const nowUnder = atEnd ? !segUnder : isPointUnderground(routeKey, coords[i][0], coords[i][1]);
    if (nowUnder !== segUnder || atEnd) {
      const segCoords = coords.slice(segStart, i);
      if (segCoords.length >= 2) {
        L.polyline(segCoords, {
          color: color,
          weight: segUnder ? 4 : 5,
          opacity: segUnder ? 0.6 : 0.9,
          dashArray: segUnder ? '8 6' : null,
        }).addTo(routeLayerGroup);
        if (segUnder) {
          L.polyline(segCoords, {
            color: '#ffffff', weight: 1.5, opacity: 0.25,
          }).addTo(routeLayerGroup);
        }
      }
      segStart = i - 1;
      segUnder = nowUnder;
    }
  }
}

function drawThinPath(coords, color) {
  if (coords.length < 2) return;
  L.polyline(coords, {
    color, weight: 2, opacity: 0.35, dashArray: '6 4',
  }).addTo(routeLayerGroup);
}

// Detour layer group — cleared/redrawn on each vehicle refresh
let detourLayerGroup = null;

function drawDetourPaths(routeKey, color, bounds) {
  // Only draw when tunnel closure is detected
  const status = getTunnelClosureStatus();
  if (!status) {
    if (detourLayerGroup) detourLayerGroup.clearLayers();
    return;
  }
  if (!detourLayerGroup) {
    detourLayerGroup = L.layerGroup().addTo(leafletMap);
  }
  detourLayerGroup.clearLayers();

  const detourColor = '#f59e0b';  // amber
  // Draw both directions of the detour
  L.polyline(DETOUR_PATH_WB, {
    color: detourColor, weight: 4, opacity: 0.8, dashArray: '10 6',
  }).addTo(detourLayerGroup);
  L.polyline(DETOUR_PATH_EB, {
    color: detourColor, weight: 4, opacity: 0.8, dashArray: '10 6',
  }).addTo(detourLayerGroup);

  // Add bounds so map fits the detour
  for (const p of DETOUR_PATH_WB) bounds.push(p);
  for (const p of DETOUR_PATH_EB) bounds.push(p);
}

function orderStops(stops) {
  if (stops.length < 2) return stops;
  const lats = stops.map(s => s.lat), lngs = stops.map(s => s.lng);
  const latRange = (Math.max(...lats) - Math.min(...lats)) * 111;
  const lngRange = (Math.max(...lngs) - Math.min(...lngs)) * 83;
  return [...stops].sort((a, b) =>
    latRange > lngRange ? b.lat - a.lat : a.lng - b.lng
  );
}

// ── Vehicle markers ─────────────────────────────────────────────────────────

async function refreshMapVehicles() {
  if (!selectedRoute || !mapInitialized) return;
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
      vehicles = processTransitData(results.flatMap(r => r?.bus || []), selectedRoute.id, selectedRoute.multi);
    }
    updateVehicleHistory(vehicles);
    // Update liveRegistry so stop arrival predictions work from map refresh
    const now = Date.now();
    const newReg = {};
    for (const v of vehicles) {
      const ex = liveRegistry[v._id];
      newReg[v._id] = {
        route: v._rkey, firstSeen: ex?.firstSeen ?? now, lastSeen: now,
        firstLat: ex?.firstLat ?? parseFloat(v.lat), firstLng: ex?.firstLng ?? parseFloat(v.lng),
      };
    }
    Object.assign(liveRegistry, newReg);

    const isTunnelRoute = TUNNEL_ROUTES.has(selectedRoute.id);
    if (isTunnelRoute) {
      try {
        const ghostResp = await apiFetch('/api/ghosts');
        syncServerGhosts(ghostResp.ghosts || ghostResp);
        lingeringVids = ghostResp.lingering || {};
      } catch (_) {}
    }
    const ghosts = isTunnelRoute ? getGhostVehicles() : [];
    const visible = isTunnelRoute ? vehicles.filter(v => !ghostReplacedVids.has(v._id)) : vehicles;
    updateVehiclesOnMap([...visible, ...ghosts]);

    // Tunnel closure detection — after render so errors don't block it
    if (isTunnelRoute) {
      try {
        detectTunnelClosureFromGPS(vehicles);
        detectTunnelClosureFromAlerts();
        updateTunnelClosureBanner();
        drawDetourPaths(selectedRoute.id, selectedRoute.color || '#2f69f3', []);
      } catch (e) { console.warn('tunnel closure detection error:', e); }
    }
  } catch (_) {}
}

function realIcon(color, heading) {
  return L.divIcon({
    className: '',
    html: `<svg width="30" height="30" viewBox="0 0 30 30" style="transform:rotate(${heading}deg)">
      <polygon points="15,2.1 10,21.5 20,21.5" fill="${color}" stroke="white" stroke-width="1.8" stroke-linejoin="round"/>
    </svg>`,
    iconSize: [30, 30], iconAnchor: [15, 15],
  });
}

function ghostIcon(color, heading) {
  return L.divIcon({
    className: 'ghost-marker-svg',
    html: `<svg width="30" height="30" viewBox="0 0 30 30" style="transform:rotate(${heading}deg)">
      <polygon points="15,2.1 10,21.5 20,21.5" fill="none" stroke="${color}" stroke-width="1.8" stroke-dasharray="4 3" stroke-linejoin="round"/>
      <circle cx="15" cy="15" r="3.75" fill="#93c5fd" opacity="0.8"/>
    </svg>`,
    iconSize: [30, 30], iconAnchor: [15, 15],
  });
}

function lingerSolidIcon(color, heading) {
  return L.divIcon({
    className: 'linger-marker',
    html: `<svg width="30" height="30" viewBox="0 0 30 30" style="transform:rotate(${heading}deg)">
      <polygon points="15,2.1 10,21.5 20,21.5" fill="${color}" stroke="white" stroke-width="1.8" stroke-linejoin="round" opacity="0.85"/>
    </svg>`,
    iconSize: [30, 30], iconAnchor: [15, 15],
  });
}

function lingerDashedIcon(color, heading) {
  return L.divIcon({
    className: 'linger-marker',
    html: `<svg width="30" height="30" viewBox="0 0 30 30" style="transform:rotate(${heading}deg)">
      <polygon points="15,2.1 10,21.5 20,21.5" fill="none" stroke="${color}" stroke-width="1.8" stroke-dasharray="4 3" stroke-linejoin="round" opacity="0.85"/>
      <circle cx="15" cy="15" r="3.75" fill="${color}" opacity="0.7"/>
    </svg>`,
    iconSize: [30, 30], iconAnchor: [15, 15],
  });
}

// Ghost band polylines: vid → L.polyline
let ghostBandLayers = {};

// Stop arrival estimation state
let stopMarkerInfos = [];   // [{marker, stop}]
let routePathIndices = {};  // routeKey → {path, cumDist, totalLen}
let stopPredictions = {};   // stopId → [{trip, vehicle, route, minutes}]

function updateVehiclesOnMap(vehicles) {
  if (!mapInitialized) return;
  const defaultColor = selectedRoute?.color || '#2f69f3';
  const seenIds = new Set();

  for (const v of vehicles) {
    const lat = parseFloat(v.lat), lng = parseFloat(v.lng);
    if (isNaN(lat) || isNaN(lng)) continue;
    seenIds.add(v._id);
    const color = getRouteColor(v._rkey) || defaultColor;

    const isGhost = v._ghost === true;
    const tunneled = isGhost || inTunnel(v);
    let nextStop = '';
    if (v.next_stop && !isGhost) nextStop = v.next_stop;
    else if (tunneled) nextStop = nearestTunnelStop(v);
    else nextStop = nearestStop(lat, lng);

    const lateText = isGhost ? 'In tunnel' : (v.late <= 0 ? 'On time' : `${v.late} min late`);
    const dir = v.destination_terminus || v.toward_terminus || headingLabel(v.computed_heading != null ? v.computed_heading : v.heading);
    const aftPct = isGhost ? Math.round((v._aftFraction || 0) * 100) : 0;
    const forePct = isGhost ? Math.round((v._foreFraction || 0) * 100) : 0;
    const ghostInfo = isGhost ? `<div style="font-size:10px;color:#93c5fd;margin-bottom:3px;">Estimated · ${aftPct}–${forePct}% ${v._direction||''}${v._leg==='second'?' (return)':''}</div>` : '';
    const stopProgress = (!isGhost && v.stops_passed != null && v.stops_remaining != null)
      ? `<div style="font-size:10px;color:#555;margin-top:2px;">${v.stops_passed} stops passed · ${v.stops_remaining} remaining</div>` : '';
    const popupHtml = `
      <div style="background:#191c22;padding:8px 10px;border-radius:5px;color:#e0e8f0;min-width:140px;">
        <div style="font-weight:700;font-size:14px;margin-bottom:4px;">${v.label}</div>
        ${ghostInfo}
        ${nextStop ? `<div style="font-size:12px;color:#93c5fd;margin-bottom:3px;">▶ ${nextStop}${tunneled?' (tunnel)':''}</div>` : ''}
        <div style="font-size:11px;color:#78818c;">${v.destination_terminus || v.dest || '—'}</div>
        <div style="font-size:11px;color:#78818c;margin-top:2px;">${lateText}${dir?' · → '+dir:''}</div>
        ${stopProgress}
      </div>`;

    // Ghost band polyline
    if (isGhost && v._bandPath && v._bandPath.length >= 2) {
      const bandCoords = v._bandPath.map(p => [p.lat, p.lng]);
      if (ghostBandLayers[v._id]) {
        ghostBandLayers[v._id].setLatLngs(bandCoords);
      } else {
        const band = L.polyline(bandCoords, {
          color, weight: 10, opacity: bandOpacity,
          lineCap: 'round', lineJoin: 'round',
        }).addTo(vehicleLayerGroup);
        ghostBandLayers[v._id] = band;
      }
    }

    // Determine marker icon based on vehicle state
    const isLingering = !isGhost && lingeringVids[v._id];
    const isPortalLinger = isGhost && v._lingersAtPortal;
    // Prefer trip_bearing (fixed for the trip's lifetime) for icon orientation;
    // fall back to computed_heading (dynamic) or raw GPS heading.
    let hdg = v.trip_bearing != null ? +v.trip_bearing
            : v.computed_heading != null ? +v.computed_heading
            : v.heading != null ? +v.heading : 0;
    // For ghost/linger vehicles without a heading, derive from direction
    if (hdg === 0 && (isGhost || isPortalLinger) && v._direction) {
      hdg = v._direction === 'eastbound' ? 90 : 270;
    }

    function pickIcon() {
      if (isLingering) return lingerSolidIcon(color, hdg);
      if (isPortalLinger) return lingerDashedIcon(color, hdg);
      if (isGhost) return ghostIcon(color, hdg);
      return realIcon(color, hdg);
    }

    const markerState = isPortalLinger ? 'portal-linger' : isLingering ? 'linger' : isGhost ? 'ghost' : 'real';

    if (vehicleMarkers[v._id]) {
      vehicleMarkers[v._id].setLatLng([lat, lng]).setPopupContent(popupHtml);
      if (vehicleMarkers[v._id]._markerState !== markerState ||
          vehicleMarkers[v._id]._lastHeading !== hdg) {
        vehicleMarkers[v._id].setIcon(pickIcon());
        vehicleMarkers[v._id]._markerState = markerState;
        vehicleMarkers[v._id]._isGhost = isGhost || isPortalLinger;
        vehicleMarkers[v._id]._lastHeading = hdg;
      }
    } else {
      const icon = pickIcon();
      const m = L.marker([lat, lng], { icon })
        .bindPopup(popupHtml, { className: 'map-vehicle-popup' })
        .addTo(vehicleLayerGroup);
      m._isGhost = isGhost || isPortalLinger;
      m._markerState = markerState;
      m._lastHeading = hdg;
      vehicleMarkers[v._id] = m;
    }
  }

  // Remove markers and ghost band layers for vehicles no longer present
  for (const [id, m] of Object.entries(vehicleMarkers)) {
    if (!seenIds.has(id)) {
      vehicleLayerGroup.removeLayer(m);
      delete vehicleMarkers[id];
    }
  }
  for (const [id, band] of Object.entries(ghostBandLayers)) {
    if (!seenIds.has(id)) {
      vehicleLayerGroup.removeLayer(band);
      delete ghostBandLayers[id];
    }
  }

  updateAllStopPopups(vehicles);
}

// ── Stop arrival estimation ──────────────────────────────────────────────

// Direction filter per stop: stopKey → null | 'fwd' | 'rev'
let stopDirFilters = {};
// Cache of last vehicles for popup re-render on filter change
let lastStopVehicles = [];

function findNearestStopId(lat, lng) {
  let bestId = null, bestDist = Infinity;
  for (const [sid, s] of Object.entries(stopsData)) {
    const d = Math.abs(s.lat - lat) + Math.abs(s.lng - lng);
    if (d < bestDist) { bestDist = d; bestId = sid; }
  }
  return bestDist < 0.003 ? bestId : null;
}

async function fetchStopPredictions() {
  const stopIds = stopMarkerInfos
    .map(info => info.stop.stopId)
    .filter(Boolean);
  if (stopIds.length === 0) return;
  const uniqueIds = [...new Set(stopIds)];
  // Batch into chunks of 50 to avoid huge URLs
  for (let i = 0; i < uniqueIds.length; i += 50) {
    const batch = uniqueIds.slice(i, i + 50);
    const routeFilter = selectedRoute.multi
      ? (selectedRoute.apiIds || []).join(',')
      : (selectedRoute.id || '');
    try {
      const data = await apiFetch(
        `/api/septa/stop-predictions?stops=${batch.join(',')}&routes=${routeFilter}`
      );
      Object.assign(stopPredictions, data);
    } catch (_) {}
  }
}

function getRoutePathIndex(routeKey) {
  if (routePathIndices[routeKey]) return routePathIndices[routeKey];
  let shapeCoords = shapesData[routeKey];
  if (!shapeCoords || shapeCoords.length < 2) return null;
  // Exclude spur sections — they distort distance-along calculations
  const spur = ROUTE_SPURS[routeKey];
  if (spur) {
    const ci = spur.cutoffIndex;
    shapeCoords = spur.end === 'start' ? shapeCoords.slice(ci) : shapeCoords.slice(0, ci + 1);
  }
  let path = shapeCoords.map(c => ({ lat: c[0], lng: c[1] }));
  // Orient shape so index 0 = start (outer) terminus, matching server orientation.
  // Without this, movingForward can be inverted for routes like T3/T5 whose raw
  // GTFS shapes run in the opposite direction from the server's convention.
  const term = SHAPE_TERMINI[routeKey];
  if (term && path.length >= 2) {
    const d0 = distLatLng(path[0], { lat: term.startLat, lng: term.startLng });
    const dn = distLatLng(path[path.length - 1], { lat: term.startLat, lng: term.startLng });
    if (dn < d0) path = path.slice().reverse();
  }
  const cumDist = [0];
  for (let i = 1; i < path.length; i++) {
    cumDist.push(cumDist[i - 1] + distLatLng(path[i - 1], path[i]));
  }
  routePathIndices[routeKey] = { path, cumDist, totalLen: cumDist[cumDist.length - 1] };
  return routePathIndices[routeKey];
}

// Determine if route runs primarily east-west or north-south from its shape
function getRouteOrientation(routeKey) {
  const shapeCoords = shapesData[routeKey];
  if (!shapeCoords || shapeCoords.length < 2) return 'ew';
  const first = shapeCoords[0], last = shapeCoords[shapeCoords.length - 1];
  const latSpan = Math.abs(last[0] - first[0]) * 111;
  const lngSpan = Math.abs(last[1] - first[1]) * 85;
  return latSpan > lngSpan * 1.5 ? 'ns' : 'ew';
}

function getDirLabels(routeKey) {
  const routes = (selectedRoute?.multi && selectedRoute?.apiIds) || [routeKey];
  // Use first sub-route's shape for orientation
  const orient = getRouteOrientation(routes[0]);
  return orient === 'ns'
    ? { fwd: 'Northbound', rev: 'Southbound' }
    : { fwd: 'Eastbound', rev: 'Westbound' };
}

function projectOntoPathIdx(idx, lat, lng) {
  if (!idx || idx.path.length < 2) return null;
  let bestDistAlong = 0, bestPerpDist = Infinity;
  const cosLat = Math.cos(lat * Math.PI / 180);
  for (let i = 1; i < idx.path.length; i++) {
    const p0 = idx.path[i - 1], p1 = idx.path[i];
    const dx = (p1.lat - p0.lat) * 111320;
    const dy = (p1.lng - p0.lng) * 111320 * cosLat;
    const px = (lat - p0.lat) * 111320;
    const py = (lng - p0.lng) * 111320 * cosLat;
    const segLenSq = dx * dx + dy * dy;
    const t = segLenSq === 0 ? 0 : Math.max(0, Math.min(1, (px * dx + py * dy) / segLenSq));
    const projLat = p0.lat + t * (p1.lat - p0.lat);
    const projLng = p0.lng + t * (p1.lng - p0.lng);
    const perpDist = distLatLng({ lat, lng }, { lat: projLat, lng: projLng });
    if (perpDist < bestPerpDist) {
      bestPerpDist = perpDist;
      bestDistAlong = idx.cumDist[i - 1] + t * (idx.cumDist[i] - idx.cumDist[i - 1]);
    }
  }
  return { distAlong: bestDistAlong, perpDist: bestPerpDist };
}

function computeStopArrivals(stop, vehicles, now) {
  const arrivals = [];
  const routes = stop.routes || [stop.routeKey];

  for (const rk of routes) {
    const pathIdx = getRoutePathIndex(rk);
    if (!pathIdx) continue;
    const stopProj = projectOntoPathIdx(pathIdx, stop.lat, stop.lng);
    if (!stopProj) continue;

    for (const v of vehicles) {
      // Match vehicle to route
      if (selectedRoute.multi && v._rkey !== rk) continue;
      if (!selectedRoute.multi && v._rkey !== (selectedRoute?.id || rk)) continue;

      const lat = parseFloat(v.lat), lng = parseFloat(v.lng);
      if (isNaN(lat) || isNaN(lng)) continue;

      const isGhost = v._ghost === true;
      let speedMpm, movingForward;  // speed in meters-per-minute
      let hasSpeed = false;

      const vehProj = projectOntoPathIdx(pathIdx, lat, lng);
      if (!vehProj) continue;

      // Always use server-provided direction when available (shape-oriented,
      // consistent with server's terminus conventions)
      const hasServerDir = !isGhost && v.computed_direction != null;

      // Prefer server-provided speed (cumulative travel, survives turnarounds)
      if (!isGhost && v.speed_mps != null && v.speed_mps > 0) {
        speedMpm = v.speed_mps * 60;
        movingForward = v.computed_direction !== 'reverse';
        hasSpeed = true;
      }

      if (!hasSpeed) {
        let startLat, startLng, T_s_ms;
        if (isGhost) {
          startLat = v._enterLat;
          startLng = v._enterLng;
          T_s_ms = now - v._enterTs;
        } else {
          const reg = liveRegistry[v._id];
          if (!reg || reg.firstLat == null) continue;
          startLat = reg.firstLat;
          startLng = reg.firstLng;
          T_s_ms = now - reg.firstSeen;
        }

        if (T_s_ms < 30000) continue;

        const startProj = projectOntoPathIdx(pathIdx, startLat, startLng);
        if (!startProj) continue;

        const D_cs = Math.abs(vehProj.distAlong - startProj.distAlong);
        if (D_cs < 50) continue;

        speedMpm = D_cs / (T_s_ms / 60000);

        // Use server direction when available; fall back to client projection
        movingForward = hasServerDir
          ? v.computed_direction !== 'reverse'
          : vehProj.distAlong >= startProj.distAlong;
        hasSpeed = true;
      }
      const D_ch = movingForward
        ? stopProj.distAlong - vehProj.distAlong
        : vehProj.distAlong - stopProj.distAlong;

      // Check direct arrival (stop is ahead in current direction)
      if (D_ch >= 0 && D_ch <= pathIdx.totalLen * 0.9) {
        const T_star = D_ch / speedMpm;
        if (T_star <= 120) {
          let T_official = null;
          let officialStatus = null;
          if (!isGhost && stop.stopId) {
            const preds = stopPredictions[stop.stopId] || [];
            const tripId = String(v.trip || '');
            const match = preds.find(p => String(p.trip) === tripId);
            if (match) {
              T_official = match.minutes;
              officialStatus = match.status || null;
            }
          }

          let T_low = null, T_high = null;
          if (isGhost && v._forePos && v._aftPos) {
            const foreProj = projectOntoPathIdx(pathIdx, v._forePos.lat, v._forePos.lng);
            const aftProj = projectOntoPathIdx(pathIdx, v._aftPos.lat, v._aftPos.lng);
            if (foreProj && aftProj) {
              const D_ch_f = movingForward
                ? stopProj.distAlong - foreProj.distAlong
                : foreProj.distAlong - stopProj.distAlong;
              const D_ch_a = movingForward
                ? stopProj.distAlong - aftProj.distAlong
                : aftProj.distAlong - stopProj.distAlong;
              if (D_ch_f >= 0) T_low = D_ch_f / speedMpm;
              if (D_ch_a >= 0) T_high = D_ch_a / speedMpm;
            }
          }

          arrivals.push({
            label: v.label,
            dest: v.destination_terminus || v.dest || '—',
            route: rk,
            T_star,
            T_official,
            officialStatus,
            T_low,
            T_high,
            isGhost,
            late: v.late,
            dirFwd: movingForward,
          });
        }
      }

      // Turnaround (reflection) arrival: an eastbound trolley heading
      // toward 13th & Market will reverse there and come back westbound
      // through ALL tunnel stops.  This applies to stops both behind AND
      // ahead of the vehicle (stops ahead get both a direct eastbound
      // arrival and a later westbound turnaround arrival).
      // With oriented shapes, 13th St (inner terminus) is always at the
      // END of the shape (high distAlong) for all trolley routes.
      if (TUNNEL_ROUTES.has(rk) && rk !== 'T-ALL' && !isGhost) {
        // movingForward = heading toward end of shape = toward 13th St
        const headingToTerminus = movingForward;
        if (headingToTerminus) {
          const toEnd = pathIdx.totalLen - vehProj.distAlong;
          const endToStop = pathIdx.totalLen - stopProj.distAlong;
          const D_turn = toEnd + endToStop;
          // Skip if stop is at/near the terminus itself (no westbound re-arrival)
          if (D_turn > 100) {
            const T_turn = D_turn / speedMpm;
            if (T_turn > 0 && T_turn <= 120) {
              arrivals.push({
                label: v.label,
                dest: v.origin_terminus || v.dest || '—',
                route: rk,
                T_star: T_turn,
                T_official: null,
                officialStatus: null,
                T_low: null,
                T_high: null,
                isGhost: false,
                late: v.late,
                dirFwd: !movingForward,  // after turnaround, direction reverses
                turnaround: true,
              });
            }
          }
        }
      }
    }
  }

  // Sort by best available time: official (server) when present, else calc
  return arrivals.sort((a, b) => {
    const ta = a.T_official != null ? a.T_official : a.T_star;
    const tb = b.T_official != null ? b.T_official : b.T_star;
    return ta - tb;
  });
}

function stopKey(stop) {
  return `${stop.lat},${stop.lng}`;
}

// Called from inline onclick in popup HTML
function setStopDirFilter(key, dir) {
  stopDirFilters[key] = dir;
  // Re-render just this stop's popup synchronously from cached data (no fetch)
  const now = Date.now();
  for (const info of stopMarkerInfos) {
    if (stopKey(info.stop) === key) {
      const arrivals = computeStopArrivals(info.stop, lastStopVehicles, now);
      const html = formatArrivalPopup(info.stop, arrivals);
      const popup = info.marker.getPopup();
      if (popup) {
        popup.setContent(html);
        popup.update();
      }
      break;
    }
  }
}

function formatArrivalPopup(stop, arrivals) {
  const rk = stop.routeKey || (stop.routes && stop.routes[0]) || '';
  const underground = isPointUnderground(rk, stop.lat, stop.lng);
  const sk = stopKey(stop);
  const dirFilter = stopDirFilters[sk] || null;
  const dirLabels = getDirLabels(rk);

  let html = `<div style="background:#191c22;padding:8px 10px;border-radius:5px;color:#e0e8f0;min-width:210px;max-width:320px;">`;
  html += `<div style="font-weight:700;font-size:14px;">${stop.name}</div>`;
  if (underground) html += `<div style="font-size:10px;color:#93c5fd;"><i>Underground</i></div>`;

  // Direction filter tabs
  const tabStyle = (active) => `cursor:pointer;padding:2px 7px;border-radius:3px;font-size:9px;font-weight:600;margin-right:2px;${active ? 'background:#2f69f3;color:#fff;' : 'background:#25292f;color:#78818c;'}`;
  html += `<div style="margin-top:6px;margin-bottom:4px;display:flex;align-items:center;gap:2px;">`;
  html += `<span style="${tabStyle(!dirFilter)}" onclick="event.stopPropagation();setStopDirFilter('${sk}',null)">All</span>`;
  html += `<span style="${tabStyle(dirFilter==='fwd')}" onclick="event.stopPropagation();setStopDirFilter('${sk}','fwd')">${dirLabels.fwd}</span>`;
  html += `<span style="${tabStyle(dirFilter==='rev')}" onclick="event.stopPropagation();setStopDirFilter('${sk}','rev')">${dirLabels.rev}</span>`;
  html += `</div>`;

  // Filter arrivals by direction
  let filtered = arrivals;
  if (dirFilter === 'fwd') filtered = arrivals.filter(a => a.dirFwd);
  else if (dirFilter === 'rev') filtered = arrivals.filter(a => !a.dirFwd);

  if (filtered.length === 0) {
    html += `<div style="font-size:11px;color:#78818c;margin-top:4px;">No approaching vehicles</div>`;
    html += `</div>`;
    return html;
  }

  html += `<div style="font-size:10px;color:#93c5fd;margin-bottom:3px;text-transform:uppercase;letter-spacing:0.05em;">Next arrival in...</div>`;

  const routes = stop.routes || [stop.routeKey];
  const isMultiRoute = routes.length > 1;

  if (isMultiRoute && !dirFilter) {
    // Group by (route, direction), show next for each
    const groups = {};
    for (const a of filtered) {
      const dk = a.dirFwd ? 'fwd' : 'rev';
      const key = `${a.route}_${dk}`;
      if (!groups[key]) groups[key] = a;
    }
    const groupList = Object.values(groups).sort((a, b) => {
      if (a.route !== b.route) return a.route.localeCompare(b.route);
      return a.T_star - b.T_star;
    });
    for (const a of groupList) {
      html += formatOneArrival(a, true, dirLabels);
    }
  } else {
    const shown = filtered.slice(0, 5);
    for (const a of shown) {
      html += formatOneArrival(a, isMultiRoute, dirLabels);
    }
  }

  html += `</div>`;
  return html;
}

function formatOneArrival(a, showRoute, dirLabels) {
  const color = getRouteColor(a.route) || selectedRoute?.color || '#2f69f3';
  const routeBadge = `<span style="background:${color};color:#000;padding:1px 4px;border-radius:3px;font-size:9px;font-weight:600;margin-right:4px;">${a.route}</span>`;

  // Primary: use official (server-cached) prediction when available
  const hasOfficial = !a.isGhost && a.T_official != null;

  let primaryText;
  if (hasOfficial) {
    const statusColor = (a.officialStatus === 'ON-TIME' || a.officialStatus === 'EARLY') ? '#22c55e' : a.officialStatus === 'LATE' ? '#f59e0b' : '#e0e8f0';
    primaryText = `<span style="color:${statusColor}">${Math.round(a.T_official)} min</span>`;
  } else if (a.isGhost && a.T_low != null && a.T_high != null) {
    const lo = Math.round(a.T_low);
    const hi = Math.round(a.T_high);
    primaryText = lo === hi ? `${lo} min` : `${lo}–${hi} min`;
  } else {
    primaryText = `${Math.round(a.T_star)} min`;
  }

  // Secondary: show calc estimate for comparison when official is available
  let secondaryText = '';
  if (hasOfficial) {
    secondaryText = ` <span style="color:#555;font-size:9px;">(calc. ${Math.round(a.T_star)})</span>`;
  } else if (!a.isGhost) {
    secondaryText = ' <span style="color:#555;font-size:9px;">(est.)</span>';
  }

  const dirLabel = a.dirFwd ? dirLabels.fwd : dirLabels.rev;
  const turnTag = a.turnaround ? ' <span style="color:#93c5fd;font-size:9px;">via turnaround</span>' : '';

  let html = `<div style="margin-bottom:4px;border-bottom:1px solid #25292f;padding-bottom:3px;">`;
  html += `<div style="font-size:11px;">${routeBadge}<b>${a.dest}</b> <span style="color:#555;font-size:9px;">${dirLabel}</span>${turnTag}</div>`;
  html += `<div style="font-size:10px;color:#78818c;">${primaryText}${secondaryText}</div>`;
  html += `</div>`;
  return html;
}

async function updateAllStopPopups(vehicles) {
  if (!mapInitialized || stopMarkerInfos.length === 0) return;
  lastStopVehicles = vehicles;
  // Fetch GTFS-RT official predictions (only include real tracked vehicles)
  await fetchStopPredictions();
  const now = Date.now();
  for (const info of stopMarkerInfos) {
    const arrivals = computeStopArrivals(info.stop, vehicles, now);
    const html = formatArrivalPopup(info.stop, arrivals);
    info.marker.setPopupContent(html);
  }
}
