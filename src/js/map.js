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
  setTimeout(() => leafletMap.invalidateSize(), 50);

  routeLayerGroup.clearLayers();
  stopLayerGroup.clearLayers();
  vehicleLayerGroup.clearLayers();
  vehicleMarkers = {};

  // Route path
  const shapeCoords = shapesData[gtfsId] || shapesData[routeKey];
  const stationList = routeStops.length > 0 ? routeStops : (HARDCODED_STATIONS[routeKey] || []);
  let hasGtfsShapes = false;

  if (shapeCoords && shapeCoords.length > 1) {
    hasGtfsShapes = true;
    drawSegmentedPath(shapeCoords, routeKey, color);
  } else if (stationList.length > 1) {
    const inOrder = (stationList === routeStops && routeStopsOrdered) || HARDCODED_STATIONS[routeKey];
    const coords  = (inOrder ? stationList : orderStops(stationList)).map(s => [s.lat, s.lng]);
    drawSegmentedPath(coords, routeKey, color);
  }

  // Stop markers
  const bounds = [];
  for (const s of stationList) {
    const underground = isPointUnderground(routeKey, s.lat, s.lng);
    const marker = L.circleMarker([s.lat, s.lng], {
      radius: 4, color: '#0c0e12', weight: 1.5,
      fillColor: underground ? '#4a7fff' : color, fillOpacity: 0.9,
    }).bindPopup(`<b>${s.name}</b>${underground ? '<br><i>Underground</i>' : ''}`);
    stopLayerGroup.addLayer(marker);
    bounds.push([s.lat, s.lng]);
  }

  if (bounds.length) leafletMap.fitBounds(bounds, { padding: [30, 30] });

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
      vehicles = processTransitData(results.flatMap(r => r?.bus || []), selectedRoute.id);
    }
    updateVehicleHistory(vehicles);
    detectTunnelEntries(vehicles);
    const ghosts = getGhostVehicles();
    updateVehiclesOnMap([...vehicles, ...ghosts]);
  } catch (_) {}
}

function realIcon(color, heading) {
  return L.divIcon({
    className: '',
    html: `<svg width="24" height="24" viewBox="0 0 24 24" style="transform:rotate(${heading}deg)">
      <circle cx="12" cy="12" r="8" fill="${color}" stroke="#0c0e12" stroke-width="2"/>
      <polygon points="12,2 15,9 12,7 9,9" fill="white" opacity="0.9"/>
    </svg>`,
    iconSize: [24, 24], iconAnchor: [12, 12],
  });
}

function ghostIcon(color) {
  return L.divIcon({
    className: 'ghost-marker-svg',
    html: `<svg width="24" height="24" viewBox="0 0 24 24">
      <circle cx="12" cy="12" r="7" fill="none" stroke="${color}" stroke-width="2" stroke-dasharray="4 3"/>
      <circle cx="12" cy="12" r="3" fill="#93c5fd" opacity="0.8"/>
    </svg>`,
    iconSize: [24, 24], iconAnchor: [12, 12],
  });
}

function updateVehiclesOnMap(vehicles) {
  if (!mapInitialized) return;
  const color = selectedRoute?.color || '#2f69f3';
  const seenIds = new Set();

  for (const v of vehicles) {
    const lat = parseFloat(v.lat), lng = parseFloat(v.lng);
    if (isNaN(lat) || isNaN(lng)) continue;
    seenIds.add(v._id);

    const isGhost = v._ghost === true;
    const tunneled = isGhost || inTunnel(v);
    let nextStop = '';
    if (tunneled) nextStop = nearestTunnelStop(v);
    else nextStop = nearestStop(lat, lng);

    const lateText = isGhost ? 'In tunnel' : (v.late <= 0 ? 'On time' : `${v.late} min late`);
    const dir = headingLabel(v.heading);
    const ghostInfo = isGhost ? `<div style="font-size:10px;color:#93c5fd;margin-bottom:3px;">Estimated · ${Math.round((v._fraction||0)*100)}% ${v._direction||''}${v._leg==='second'?' (return)':''}</div>` : '';
    const popupHtml = `
      <div style="background:#191c22;padding:8px 10px;border-radius:5px;color:#e0e8f0;min-width:140px;">
        <div style="font-weight:700;font-size:14px;margin-bottom:4px;">${v.label}</div>
        ${ghostInfo}
        ${nextStop ? `<div style="font-size:12px;color:#93c5fd;margin-bottom:3px;">▶ ${nextStop}${tunneled?' (tunnel)':''}</div>` : ''}
        <div style="font-size:11px;color:#78818c;">${v.dest || '—'}</div>
        <div style="font-size:11px;color:#78818c;margin-top:2px;">${lateText}${dir?' · ▷'+dir:''}</div>
      </div>`;

    if (vehicleMarkers[v._id]) {
      vehicleMarkers[v._id].setLatLng([lat, lng]).setPopupContent(popupHtml);
      if (isGhost && !vehicleMarkers[v._id]._isGhost) {
        vehicleMarkers[v._id].setIcon(ghostIcon(color));
        vehicleMarkers[v._id]._isGhost = true;
      } else if (!isGhost && vehicleMarkers[v._id]._isGhost) {
        const heading = v.heading != null ? +v.heading : 0;
        vehicleMarkers[v._id].setIcon(realIcon(color, heading));
        vehicleMarkers[v._id]._isGhost = false;
      }
    } else {
      const icon = isGhost ? ghostIcon(color) : realIcon(color, v.heading != null ? +v.heading : 0);
      const m = L.marker([lat, lng], { icon })
        .bindPopup(popupHtml, { className: 'map-vehicle-popup' })
        .addTo(vehicleLayerGroup);
      m._isGhost = isGhost;
      vehicleMarkers[v._id] = m;
    }
  }

  // Remove markers for vehicles no longer present
  for (const [id, m] of Object.entries(vehicleMarkers)) {
    if (!seenIds.has(id)) {
      vehicleLayerGroup.removeLayer(m);
      delete vehicleMarkers[id];
    }
  }
}
