'use strict';

// ── Route definitions ──────────────────────────────────────────────────────
const RAIL_LINES = [
  { id:'Airport',            label:'Airport',             color:'#a855f7', gtfs:'Airport',            stopsId:'Airport' },
  { id:'Chestnut Hill East', label:'Chestnut Hill East',  color:'#10b981', gtfs:'Chestnut Hill East',  stopsId:'Chestnut_Hill_East' },
  { id:'Chestnut Hill West', label:'Chestnut Hill West',  color:'#059669', gtfs:'Chestnut Hill West',  stopsId:'Chestnut_Hill_West' },
  { id:'Cynwyd',             label:'Cynwyd',              color:'#6366f1', gtfs:'Cynwyd',              stopsId:'Cynwyd' },
  { id:'Fox Chase',          label:'Fox Chase',           color:'#f97316', gtfs:'Fox Chase',           stopsId:'Fox_Chase' },
  { id:'Lansdale',           label:'Lansdale/Doylestown', color:'#eab308', gtfs:'Lansdale',            stopsId:'Lansdale' },
  { id:'Media',              label:'Media/Wawa',          color:'#ec4899', gtfs:'Media',               stopsId:'Media' },
  { id:'Manayunk',           label:'Manayunk/Norristown', color:'#8b5cf6', gtfs:'Manayunk',            stopsId:'Manayunk' },
  { id:'Paoli',              label:'Paoli/Thorndale',     color:'#0ea5e9', gtfs:'Paoli',               stopsId:'Paoli' },
  { id:'Trenton',            label:'Trenton',             color:'#ef4444', gtfs:'Trenton',             stopsId:'Trenton' },
  { id:'Warminster',         label:'Warminster',          color:'#84cc16', gtfs:'Warminster',          stopsId:'Warminster' },
  { id:'West Trenton',       label:'West Trenton',        color:'#06b6d4', gtfs:'West Trenton',        stopsId:'West_Trenton' },
  { id:'Wilmington',         label:'Wilmington/Newark',   color:'#f43f5e', gtfs:'Wilmington',          stopsId:'Wilmington' },
];

const SUBWAY_LINES = [
  { id:'MFL', apiIds:['L1'],    label:'Market-Frankford Line', color:'#0060a9', gtfs:'L1' },
  { id:'BSL', apiIds:['B1'],    label:'Broad Street Line',     color:'#f97316', gtfs:'B1' },
];

const TROLLEY_LINES = [
  { id:'T1', apiIds:['T1'], label:'T1 – Overbrook',       color:'#22c55e', gtfs:'T1' },
  { id:'T2', apiIds:['T2'], label:'T2 – Angora',          color:'#3b82f6', gtfs:'T2' },
  { id:'T3', apiIds:['T3'], label:'T3 – Yeadon/Darby',    color:'#ec4899', gtfs:'T3' },
  { id:'T4', apiIds:['T4'], label:'T4 – Darby',           color:'#8b5cf6', gtfs:'T4' },
  { id:'T5', apiIds:['T5'], label:'T5 – Eastwick',        color:'#f59e0b', gtfs:'T5' },
  { id:'G1', apiIds:['G1'], label:'G1 – Girard',          color:'#14b8a6', gtfs:'G1' },
];

const BUS_ROUTES = [
  '1','2','3','4','5','6','7','9','10','12','14','16','17','18','19',
  '20','21','22','23','24','25','26','27','28','29','30','31','32','33',
  '35','37','38','39','40','42','43','44','45','46','47','48','49','50',
  '52','53','54','55','56','57','58','59','60','61','62','63','64','65',
  '66','67','68','70','73','75','77','78','79','80','84','88','89','90',
  '91','92','93','94','95','96','97','98','99','103','104','105','106',
  '107','108','109','110','111','112','113','114','115','116','117','118',
  '119','120','123','124','125','126','127','128','129','130','131','132',
  '133','150','201','204','206','310',
].map(n => ({ id: n, label: `Route ${n}`, color: '#78818c', gtfs: n }));

const MODES = {
  SUBWAY:  { routes: SUBWAY_LINES,  type: 'transit' },
  TROLLEY: { routes: TROLLEY_LINES, type: 'transit' },
  BUS:     { routes: BUS_ROUTES,    type: 'transit' },
  RAIL:    { routes: RAIL_LINES,    type: 'rail' },
};

// ── Rail line key (maps TrainView fields to stable route key) ──────────────
const RAIL_ALIASES = {
  'Airport':            ['airport','phl'],
  'Chestnut Hill East': ['chestnut hill east','che'],
  'Chestnut Hill West': ['chestnut hill west','chw'],
  'Cynwyd':             ['cynwyd'],
  'Fox Chase':          ['fox chase'],
  'Lansdale':           ['lansdale','doylestown'],
  'Media':              ['media','wawa'],
  'Manayunk':           ['manayunk','norristown'],
  'Paoli':              ['paoli','thorndale','malvern'],
  'Trenton':            ['trenton'],
  'Warminster':         ['warminster'],
  'West Trenton':       ['west trenton'],
  'Wilmington':         ['wilmington','newark'],
};

function railLineKey(line, dest, src) {
  const s = `${line} ${dest} ${src}`.toLowerCase();
  for (const [id, keys] of Object.entries(RAIL_ALIASES))
    if (keys.some(k => s.includes(k))) return id;
  return line || 'unknown';
}
