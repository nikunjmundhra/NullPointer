/* AirLens dashboard data adapter — all observation data is loaded from ../data/. */
const DATA_FILES = {
  observations: '../data/satellite_hcho_filtered.csv',
  summary: '../data/hcho_hotspot_regional_summary.csv',
  hotspots: '../data/hcho_hotspots_ranked_live.csv'
};

const state = { observations: [], summary: [], hotspots: [], regions: {}, currentRegion: null, liveDate: null };
let map, heatLayer, regionBounds = [];
let trendChart, anomalyChart;

function parseCsv(text) {
  const rows = []; let row = [], cell = '', quoted = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i], next = text[i + 1];
    if (ch === '"' && quoted && next === '"') { cell += '"'; i++; }
    else if (ch === '"') quoted = !quoted;
    else if (ch === ',' && !quoted) { row.push(cell); cell = ''; }
    else if ((ch === '\n' || ch === '\r') && !quoted) {
      if (ch === '\r' && next === '\n') i++;
      row.push(cell); if (row.some(value => value !== '')) rows.push(row); row = []; cell = '';
    } else cell += ch;
  }
  if (cell || row.length) { row.push(cell); rows.push(row); }
  const [headers, ...records] = rows;
  if (!headers) return [];
  return records.map(values => Object.fromEntries(headers.map((header, i) => [header.trim(), (values[i] || '').trim()])));
}

async function loadCsv(name, path, requiredColumns) {
  const response = await fetch(path, { cache: 'no-store' });
  if (!response.ok) throw new Error(`${name} could not be loaded (${response.status}).`);
  const rows = parseCsv(await response.text());
  if (!rows.length) throw new Error(`${name} is empty.`);
  const missing = requiredColumns.filter(column => !(column in rows[0]));
  if (missing.length) throw new Error(`${name} is missing: ${missing.join(', ')}.`);
  return rows;
}

function asNumber(value) { return Number.parseFloat(value); }
function formatRegion(id) { return id.split('_').map(word => word[0].toUpperCase() + word.slice(1)).join(' '); }
function recordsFor(region) { return state.observations.filter(row => row.region_id === region && row.date === state.liveDate); }
function hotspotsFor(region) { return state.hotspots.filter(row => row.region_id === region && row.date === state.liveDate).sort((a, b) => asNumber(b.hcho_value_calibrated) - asNumber(a.hcho_value_calibrated)); }
function summaryFor(region) { return state.summary.find(row => row.region_id === region); }

function showDataError(error) {
  const shell = document.querySelector('.monitor-shell');
  shell.innerHTML = `<div style="padding:36px;max-width:700px"><div class="eyebrow">Data unavailable</div><h3 style="font-family:var(--display);font-size:30px;margin:14px 0">The dashboard could not load its CSV files.</h3><p style="color:var(--ink-muted)">${error.message}</p><p style="color:var(--ink-muted);margin-top:12px">Run <code>python scripts/serve_dashboard.py</code> from the project root, then open the local address it prints. Opening the HTML directly will block data loading.</p></div>`;
}

function initialiseRegions() {
  const ids = [...new Set(state.observations.filter(row => row.date === state.liveDate).map(row => row.region_id))];
  if (!ids.length) throw new Error(`No live observations were found for ${state.liveDate}.`);
  ids.forEach(id => {
    const points = recordsFor(id);
    const latitudes = points.map(row => asNumber(row.lat));
    const longitudes = points.map(row => asNumber(row.lon));
    const bounds = [[Math.min(...latitudes), Math.min(...longitudes)], [Math.max(...latitudes), Math.max(...longitudes)]];
    state.regions[id] = { label: formatRegion(id), bounds, center: [(bounds[0][0] + bounds[1][0]) / 2, (bounds[0][1] + bounds[1][1]) / 2] };
  });
  state.currentRegion = ids[0];
}

function initialiseMap() {
  map = L.map('map', { zoomControl: true, attributionControl: false }).setView([23, 80], 5);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19 }).addTo(map);
  regionBounds = Object.entries(state.regions).map(([id, region]) => ({
    id,
    layer: L.rectangle(region.bounds, { color: '#8a968d', weight: 1, fill: false }).addTo(map)
  }));
}

function renderMap() {
  if (heatLayer) map.removeLayer(heatLayer);
  const points = recordsFor(state.currentRegion);
  const values = points.map(row => asNumber(row.hcho_value));
  const min = Math.min(...values), max = Math.max(...values);
  const heatPoints = points.map(row => [asNumber(row.lat), asNumber(row.lon), Math.max(.08, (asNumber(row.hcho_value) - min) / (max - min || 1))]);
  heatLayer = L.heatLayer(heatPoints, {
    radius: 42, blur: 34, maxZoom: 10, minOpacity: .22,
    gradient: { .18: '#39745e', .55: '#d8ad58', .82: '#d76637', 1: '#a72d22' }
  }).addTo(map);
  regionBounds.forEach(item => item.layer.setStyle({ color: item.id === state.currentRegion ? '#c9552e' : '#8a968d', weight: item.id === state.currentRegion ? 2 : 1 }));
  map.fitBounds(state.regions[state.currentRegion].bounds, { padding: [28, 28], maxZoom: 10, animate: true });
}

function initialiseTabs() {
  const tabs = document.getElementById('regionTabs'); tabs.replaceChildren();
  Object.entries(state.regions).forEach(([id, region]) => {
    const tab = document.createElement('button');
    tab.type = 'button'; tab.className = `monitor-tab${id === state.currentRegion ? ' active' : ''}`;
    tab.innerHTML = `<span class="dotstat"></span>${region.label}`;
    tab.addEventListener('click', () => { state.currentRegion = id; tabs.querySelectorAll('.monitor-tab').forEach(item => item.classList.toggle('active', item === tab)); updateDashboard(); });
    tabs.appendChild(tab);
  });
}

function initialiseCharts() {
  Chart.defaults.color = '#65716a'; Chart.defaults.font.family = "'SFMono-Regular', Consolas, monospace"; Chart.defaults.font.size = 10;
  const grid = { color: 'rgba(25,37,34,.09)' };
  trendChart = new Chart(document.getElementById('trendChart'), { type: 'bar', data: { labels: [], datasets: [{ data: [], backgroundColor: '#39745e', borderRadius: 0 }] }, options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { x: { grid }, y: { grid, ticks: { callback: value => Number(value).toExponential(1) } } } } });
  anomalyChart = new Chart(document.getElementById('anomalyChart'), { type: 'bar', data: { labels: [], datasets: [{ data: [], backgroundColor: '#8a968d', borderRadius: 0 }] }, options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { x: { grid }, y: { grid, max: 100 } } } });
}

function updateDashboard() {
  const summary = summaryFor(state.currentRegion), hotspots = hotspotsFor(state.currentRegion), region = state.regions[state.currentRegion];
  if (!summary) throw new Error(`No summary row was found for ${region.label}.`);
  document.getElementById('kpiHcho').textContent = asNumber(summary.mean_calibrated_hcho).toExponential(2);
  document.getElementById('kpiAnomaly').textContent = asNumber(summary.max_calibrated_hcho).toExponential(2);
  document.getElementById('kpiHotspots').textContent = `${summary.hotspot_count} / ${summary.total_grid_points}`;
  document.getElementById('kpiBaseline').textContent = `${asNumber(summary.hotspot_pct).toFixed(1)}%`;
  document.getElementById('dateLabel').textContent = state.liveDate;
  document.getElementById('advisoryText').innerHTML = hotspots.length ? `<b>${summary.hotspot_count} of ${summary.total_grid_points} grid cells (${asNumber(summary.hotspot_pct).toFixed(1)}%)</b> flagged in ${region.label}. The most severe cell is rank #${hotspots[0].hotspot_rank}.` : `No hotspot cells flagged in ${region.label} for this pass — all ${summary.total_grid_points} monitored grid points fall within the current threshold.`;
  renderMap();
  trendChart.data.labels = hotspots.slice(0, 10).map(row => `#${row.hotspot_rank}`); trendChart.data.datasets[0].data = hotspots.slice(0, 10).map(row => asNumber(row.hcho_value_calibrated)); trendChart.update('none');
  const ids = Object.keys(state.regions); anomalyChart.data.labels = ids.map(id => state.regions[id].label); anomalyChart.data.datasets[0].data = ids.map(id => asNumber(summaryFor(id)?.hotspot_pct)); anomalyChart.data.datasets[0].backgroundColor = ids.map(id => id === state.currentRegion ? '#c9552e' : '#8a968d'); anomalyChart.update('none');
}

function initialiseMotion() {
  const targets = [document.querySelector('.hero-content'), document.querySelector('#gap .gap-layout'), document.querySelector('#pipeline .pipeline'), document.querySelector('#monitor .monitor-shell')].filter(Boolean);
  targets.forEach(el => el.classList.add(el.classList.contains('pipeline') ? 'reveal-stagger' : 'reveal'));
  const gap = document.querySelector('.gap-visual');
  if (gap) { gap.classList.add('animate-bars'); gap.querySelectorAll('.coverage-fill').forEach(bar => bar.style.setProperty('--target-width', bar.style.width)); }
  if (matchMedia('(prefers-reduced-motion: reduce)').matches) { targets.forEach(el => el.classList.add('is-visible')); if (gap) gap.classList.add('is-visible'); return; }
  const observer = new IntersectionObserver((entries, io) => entries.forEach(entry => { if (entry.isIntersecting) { entry.target.classList.add('is-visible'); io.unobserve(entry.target); } }), { threshold: .16 });
  targets.forEach(el => observer.observe(el)); if (gap) observer.observe(gap);
}

async function start() {
  try {
    const [observations, summary, hotspots] = await Promise.all([
      loadCsv('satellite_hcho_filtered.csv', DATA_FILES.observations, ['region_id', 'date', 'lat', 'lon', 'hcho_value', 'period']),
      loadCsv('hcho_hotspot_regional_summary.csv', DATA_FILES.summary, ['region_id', 'total_grid_points', 'hotspot_count', 'mean_calibrated_hcho', 'max_calibrated_hcho', 'hotspot_pct']),
      loadCsv('hcho_hotspots_ranked_live.csv', DATA_FILES.hotspots, ['hotspot_rank', 'region_id', 'date', 'lat', 'lon', 'hcho_value_calibrated', 'cloud_fraction'])
    ]);
    state.observations = observations.filter(row => row.period === 'live'); state.summary = summary; state.hotspots = hotspots;
    state.liveDate = [...new Set(state.observations.map(row => row.date))].sort().at(-1);
    initialiseRegions(); initialiseMap(); initialiseTabs(); initialiseCharts(); updateDashboard(); initialiseMotion();
  } catch (error) { console.error(error); showDataError(error); }
}
start();
