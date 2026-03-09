'use strict';

// ── Statistics ─────────────────────────────────────────────────────────────

async function loadStats() {
  if (!selectedRoute) return;
  document.getElementById('emptyStats').style.display   = 'none';
  document.getElementById('statsContent').style.display = '';
  try {
    const data = await apiFetch('/api/stats');
    renderStats(data);
  } catch (e) { setStatus('Stats error: ' + e.message); }
}

function renderStats(data) {
  const routes = data[selectedRoute.id] || {};
  const today   = new Date(), todayStr = fmtDate(today);
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
  drawChart(todayTrips, selectedRoute.gtfs, today);
}

function percentile(sorted, p) {
  const idx = (p / 100) * (sorted.length - 1);
  const lo  = Math.floor(idx), hi = Math.ceil(idx);
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (idx - lo);
}

function drawChart(trips, gtfsId, date) {
  const canvas = document.getElementById('tripChart');
  const W = canvas.offsetWidth || 680;
  canvas.width = W; canvas.height = 300;
  const ctx = canvas.getContext('2d');
  const PAD = { top:16, right:20, bottom:38, left:46 };
  const cw = W - PAD.left - PAD.right, ch = canvas.height - PAD.top - PAD.bottom;

  const actualMins = trips.map(t => {
    const ts = typeof t === 'object' ? (t.start ?? t.end) : t;
    if (!ts) return NaN;
    const d = new Date(ts);
    return d.getHours()*60 + d.getMinutes() + d.getSeconds()/60;
  }).filter(m => !isNaN(m)).sort((a,b) => a-b);

  const dt = dayType(date);
  const schedMins = (scheduleData[gtfsId]?.[dt] || []).slice().sort((a,b) => a-b);
  const maxY = Math.max(actualMins.length, schedMins.length, 1);
  const minX = 5*60, maxX = 24*60;

  function toX(m) { return PAD.left + Math.max(0,Math.min(1,(m-minX)/(maxX-minX)))*cw; }
  function toY(n) { return PAD.top + (1-n/maxY)*ch; }

  ctx.fillStyle = '#191c22'; ctx.fillRect(0,0,W,canvas.height);
  ctx.strokeStyle = '#25292f'; ctx.lineWidth = 1;
  for (let h=6;h<=24;h+=3){const x=toX(h*60);ctx.beginPath();ctx.moveTo(x,PAD.top);ctx.lineTo(x,PAD.top+ch);ctx.stroke();}
  const yStep=Math.max(1,Math.round(maxY/5));
  for(let y=0;y<=maxY;y+=yStep){const yy=toY(y);ctx.beginPath();ctx.moveTo(PAD.left,yy);ctx.lineTo(PAD.left+cw,yy);ctx.stroke();}

  function drawSteps(mins,color,alpha){
    if(!mins.length)return; ctx.save(); ctx.strokeStyle=color; ctx.globalAlpha=alpha; ctx.lineWidth=2;
    ctx.beginPath(); ctx.moveTo(toX(minX),toY(0)); let c=0;
    for(const m of mins){ctx.lineTo(toX(m),toY(c));c++;ctx.lineTo(toX(m),toY(c));}
    ctx.lineTo(toX(maxX),toY(c)); ctx.stroke(); ctx.restore();
  }
  drawSteps(schedMins,'#78818c',0.55);
  drawSteps(actualMins,'#2f69f3',1.0);

  ctx.fillStyle='#78818c'; ctx.font='10px Helvetica Neue'; ctx.textAlign='center';
  for(let h=6;h<=24;h+=3){
    const label=h===24?'12a':`${h%12||12}${h<12?'a':'p'}`;
    ctx.fillText(label,toX(h*60),PAD.top+ch+14);
  }
  ctx.textAlign='right';
  for(let y=0;y<=maxY;y+=yStep) ctx.fillText(y,PAD.left-5,toY(y)+4);
}

function dayType(d) {
  const day=d.getDay(); return day===0?'sunday':day===6?'saturday':'weekday';
}

async function clearStats() {
  if (!confirm('Clear all recorded trip data?')) return;
  try { await fetch('/api/stats/clear',{method:'POST'}); if(selectedRoute) loadStats(); }
  catch(e) { alert('Error: '+e.message); }
}
