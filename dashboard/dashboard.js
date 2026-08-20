/* Team C reads only Team B's dashboard-ready prediction bundle. */
const ROOT = '../data/dashboard_ready/';
const state = {
  manifest: null,
  mode: 'live',
  grid: [],
  hotspots: [],
  summary: {},
  regions: {},
  region: null,
  date: null
};

let map, predictionHeat, observationLayer, hotspotLayer, boundsLayers = [], predictedChart, comparisonChart;
const props = f => f.properties || {};
const number = v => Number.parseFloat(v);
const label = id => id.split('_').map(w => w[0].toUpperCase() + w.slice(1)).join(' ');
const cells = () => state.grid.filter(f => props(f).region_id === state.region && props(f).date === state.date);
const hotspots = () => state.hotspots.filter(f => props(f).region_id === state.region && props(f).date === state.date);

async function json(file) {
  const r = await fetch(ROOT + file, { cache: 'no-store' });
  if (!r.ok) throw new Error(`${file} could not be loaded (${r.status}).`);
  return r.json();
}

function errorView(error) {
  document.querySelector('.monitor-shell').innerHTML = `<div style="padding:36px"><div class="eyebrow">Prediction bundle unavailable</div><h3 style="font-family:var(--display);font-size:30px;margin:14px 0">The model output could not be loaded.</h3><p>${error.message}</p><p style="margin-top:12px">Run <code>python src/pipeline/run_pipeline.py</code>, then refresh.</p></div>`;
}

function getAvailableDates() {
  if (!state.region) return [];
  return [...new Set(state.grid.filter(f => props(f).region_id === state.region).map(f => props(f).date))].sort();
}

function setupDateControls() {
  const select = document.getElementById('dateSelect');
  const prevBtn = document.getElementById('prevDateBtn');
  const nextBtn = document.getElementById('nextDateBtn');
  if (!select) return;

  const dates = getAvailableDates();
  select.innerHTML = '';

  if (dates.length === 0) {
    select.innerHTML = '<option value="">No dates available</option>';
    if (prevBtn) prevBtn.disabled = true;
    if (nextBtn) nextBtn.disabled = true;
    return;
  }

  if (!state.date || !dates.includes(state.date)) {
    state.date = dates[dates.length - 1]; // Default to latest date
  }

  dates.forEach(d => {
    const spotCount = state.hotspots.filter(f => props(f).region_id === state.region && props(f).date === d).length;
    const opt = document.createElement('option');
    opt.value = d;
    opt.textContent = spotCount > 0 ? `${d} 🔥 (${spotCount})` : d;
    if (d === state.date) opt.selected = true;
    select.appendChild(opt);
  });

  const currentIndex = dates.indexOf(state.date);
  if (prevBtn) {
    prevBtn.disabled = currentIndex <= 0;
    prevBtn.onclick = () => {
      if (currentIndex > 0) {
        state.date = dates[currentIndex - 1];
        setupDateControls();
        update();
      }
    };
  }
  if (nextBtn) {
    nextBtn.disabled = currentIndex >= dates.length - 1;
    nextBtn.onclick = () => {
      if (currentIndex < dates.length - 1) {
        state.date = dates[currentIndex + 1];
        setupDateControls();
        update();
      }
    };
  }

  select.onchange = (e) => {
    state.date = e.target.value;
    setupDateControls();
    update();
  };
}

function setupRegions() {
  state.regions = {};
  [...new Set(state.grid.map(f => props(f).region_id))].forEach(id => {
    const points = state.grid.filter(f => props(f).region_id === id).map(f => f.geometry.coordinates);
    if (points.length === 0) return;
    const lat = points.map(([, y]) => y), lon = points.map(([x]) => x);
    state.regions[id] = { label: label(id), bounds: [[Math.min(...lat), Math.min(...lon)], [Math.max(...lat), Math.max(...lon)]] };
  });

  const regionKeys = Object.keys(state.regions);
  if (regionKeys.length > 0 && (!state.region || !state.regions[state.region])) {
    state.region = regionKeys[0];
  }
  setupDateControls();
}

function setupMap() {
  if (map) return;
  map = L.map('map', { zoomControl: true, attributionControl: false }).setView([23, 80], 5);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19 }).addTo(map);
}

function updateMapBounds() {
  if (!map) return;
  boundsLayers.forEach(x => map.removeLayer(x.layer));
  boundsLayers = Object.entries(state.regions).map(([id, r]) => ({
    id,
    layer: L.rectangle(r.bounds, { color: id === state.region ? '#c9552e' : '#8a968d', weight: id === state.region ? 2 : 1, fill: false }).addTo(map)
  }));
  if (state.region && state.regions[state.region]) {
    map.fitBounds(state.regions[state.region].bounds, { padding: [28, 28], maxZoom: 10, animate: true });
  }
}

function aqiColour(aqi) {
  return aqi <= 50 ? '#5aa878' : aqi <= 100 ? '#c8b451' : aqi <= 200 ? '#df8544' : aqi <= 300 ? '#d14b35' : '#8f2930';
}

function renderMap() {
  if (!map) return;
  [predictionHeat, observationLayer, hotspotLayer].filter(Boolean).forEach(layer => map.removeLayer(layer));
  const rows = cells();
  if (rows.length === 0) return;

  const values = rows.map(f => number(props(f).estimated_aqi)), peak = Math.max(...values, 1);
  /* Quiet teal dots: measured satellite HCHO observations. */
  observationLayer = L.layerGroup(rows.map(f => {
    const [lon, lat] = f.geometry.coordinates;
    return L.circleMarker([lat, lon], { radius: 2.3, color: '#286d70', fillColor: '#6eaaa4', fillOpacity: .32, opacity: .45, weight: .7 })
      .bindTooltip(`Observed satellite HCHO: ${number(props(f).hcho_value).toExponential(2)} mol/m²`);
  })).addTo(map);

  /* Strong warm field: model-predicted surface AQI. */
  predictionHeat = L.heatLayer(rows.map(f => {
    const [lon, lat] = f.geometry.coordinates;
    return [lat, lon, Math.max(.06, number(props(f).estimated_aqi) / peak)];
  }), { radius: 45, blur: 38, maxZoom: 10, minOpacity: .17, gradient: { .2: '#e6c45b', .48: '#e49643', .72: '#d55735', 1: '#8f2930' } }).addTo(map);

  hotspotLayer = L.layerGroup(hotspots().map(f => {
    const p = props(f), [lon, lat] = f.geometry.coordinates;
    return L.circleMarker([lat, lon], { radius: 11, color: '#6e1e1c', fillColor: aqiColour(number(p.max_estimated_aqi)), fillOpacity: .9, weight: 2.5 })
      .bindPopup(`<b>Modelled AQI hotspot</b><br>${p.severity} · peak AQI ${Math.round(number(p.max_estimated_aqi))}<br><small>Prediction, not a ground measurement</small>`);
  })).addTo(map);

  updateMapBounds();
}

function setupModeTabs() {
  const box = document.getElementById('modeTabs');
  if (!box) return;
  box.querySelectorAll('.monitor-tab').forEach(t => {
    const m = t.getAttribute('data-mode');
    t.classList.toggle('active', m === state.mode);
    t.onclick = () => {
      if (state.mode !== m) {
        box.querySelectorAll('.monitor-tab').forEach(b => b.classList.toggle('active', b === t));
        loadMode(m);
      }
    };
  });
}

function setupRegionTabs() {
  const box = document.getElementById('regionTabs');
  if (!box) return;
  box.replaceChildren();
  Object.entries(state.regions).forEach(([id, r]) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = `monitor-tab${id === state.region ? ' active' : ''}`;
    b.innerHTML = `<span class="dotstat"></span>${r.label}`;
    b.onclick = () => {
      state.region = id;
      setupDateControls();
      box.querySelectorAll('.monitor-tab').forEach(t => t.classList.toggle('active', t === b));
      update();
    };
    box.appendChild(b);
  });
}

function setupCharts() {
  if (predictedChart && comparisonChart) return;
  Chart.defaults.color = '#65716a';
  const grid = { color: 'rgba(25,37,34,.09)' };
  predictedChart = new Chart(document.getElementById('trendChart'), {
    type: 'bar',
    data: { labels: [], datasets: [{ data: [], backgroundColor: [] }] },
    options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { x: { grid }, y: { grid, title: { display: true, text: 'Predicted AQI' } } } }
  });
  comparisonChart = new Chart(document.getElementById('anomalyChart'), {
    type: 'bar',
    data: { labels: [], datasets: [{ data: [], backgroundColor: [] }] },
    options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { x: { grid }, y: { grid, beginAtZero: true, title: { display: true, text: 'Model hotspots' } } } }
  });
}

function update() {
  const rows = cells(), aqi = rows.map(f => number(props(f).estimated_aqi)), hcho = rows.map(f => number(props(f).hcho_value)), spots = hotspots();
  const mean = aqi.length ? aqi.reduce((a, b) => a + b, 0) / aqi.length : 0;
  const meanHcho = hcho.length ? hcho.reduce((a, b) => a + b, 0) / hcho.length : 0;
  const peakAqi = aqi.length ? Math.max(...aqi) : 0;

  document.getElementById('kpiHcho').textContent = Math.round(mean);
  document.getElementById('kpiAnomaly').textContent = Math.round(peakAqi);
  document.getElementById('kpiHotspots').textContent = spots.length;
  document.getElementById('kpiBaseline').textContent = meanHcho.toExponential(2);

  const r2 = Number(state.summary.calibration?.cv_r2 || 0);
  const regionName = state.regions[state.region]?.label || state.region;
  const modeName = state.mode === 'live' ? 'Live Feed' : 'Historical Stubble Season';

  document.getElementById('advisoryText').innerHTML = spots.length
    ? `<b>${spots.length} modelled AQI hotspot${spots.length === 1 ? '' : 's'}</b> in ${regionName} (${modeName}, ${state.date}). These are model predictions from satellite HCHO and cloud data—not direct station readings.`
    : `No modelled AQI hotspots crossed the alert threshold in ${regionName} on ${state.date} (${modeName}).`;

  document.getElementById('modelNote').textContent = `Prediction model (${modeName}) · CV R² ${r2.toFixed(2)} · screening guidance, not a ground-station replacement.`;

  renderMap();

  const top = [...rows].sort((a, b) => number(props(b).estimated_aqi) - number(props(a).estimated_aqi)).slice(0, 10);
  predictedChart.data.labels = top.map((_, i) => `Cell ${i + 1}`);
  predictedChart.data.datasets[0].data = top.map(f => number(props(f).estimated_aqi));
  predictedChart.data.datasets[0].backgroundColor = top.map(f => aqiColour(number(props(f).estimated_aqi)));
  predictedChart.update('none');

  const ids = Object.keys(state.regions);
  comparisonChart.data.labels = ids.map(id => state.regions[id].label);
  comparisonChart.data.datasets[0].data = ids.map(id => state.hotspots.filter(f => props(f).region_id === id && props(f).date === state.date).length);
  comparisonChart.data.datasets[0].backgroundColor = ids.map(id => id === state.region ? '#c9552e' : '#8a968d');
  comparisonChart.update('none');
}

async function loadMode(modeName) {
  try {
    state.mode = modeName;
    const f = state.manifest.files?.[modeName];
    if (!f) throw new Error(`No ${modeName} prediction export is available.`);

    const [grid, hotspots, summary] = await Promise.all([
      json(f.grid_geojson),
      json(f.hotspots_geojson),
      json(f.summary_json)
    ]);

    state.grid = grid.features || [];
    state.hotspots = hotspots.features || [];
    state.summary = summary;

    setupRegions();
    setupRegionTabs();
    update();
  } catch (e) {
    console.error(e);
    errorView(e);
  }
}

async function start() {
  try {
    state.manifest = await json('manifest.json');
    setupMap();
    setupModeTabs();
    setupCharts();
    await loadMode('live');
  } catch (e) {
    console.error(e);
    errorView(e);
  }
}

start();

