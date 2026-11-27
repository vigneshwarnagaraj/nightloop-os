'use strict';
// NIGHTLOOP - V1 browser prototype (v0.7): multi-window layout, task windows (World / Approach / Tower),
// traffic drawing, settings, search, comms, conflict alerts.
const APP_VERSION = '1.6';

const COLORS = { ai: '#39ff6a', vatsim: '#00d9ff', ivao: '#ffb300', real: '#ff3df2' };
const NAMES = { ai: 'AI planes', vatsim: 'VATSIM', ivao: 'IVAO', real: 'Real world (ADS-B)' };
const PROV_NAMES = { 'airplanes.live': 'airplanes.live', 'adsb.lol': 'adsb.lol', 'adsb.fi': 'adsb.fi', freeflight: 'FreeFlight API (local aggregator)' };
const DR_CAP = { ai: 3, vatsim: 60, ivao: 60, real: 20 };
const LIGHT_STYLES = new Set(['street', 'terrain', 'ofm-liberty', 'satellite']);
const KINDS = {
  world: { title: 'WORLD RADAR', zone: 0 },
  approach: { title: 'APPROACH', zone: 40, zoom: 8.3, rings: 10 },
  tower: { title: 'TOWER', zone: 10, zoom: 12.3, rings: 2 },
  center: { title: 'CENTER', zone: 200, zoom: 6.4, rings: 50, label: 'CENTER SECTOR' },
  ground: { title: 'GROUND', zone: 2.5, zoom: 14.3, rings: 1, label: 'GROUND AREA' },
  guide: { title: 'LEARN ATC', zone: 0 },
};
const chartKind = (k) => S.gndDiagram && (k === 'ground' || (k === 'tower' && S.twrChart));   // airport-chart windows
const D2R = Math.PI / 180, R_NM = 3440.065;
const $ = (id) => document.getElementById(id);
const wrap = (x) => ((((x + 180) % 360) + 360) % 360) - 180;
const near = (lon, ref) => lon + 360 * Math.round((ref - lon) / 360);
const esc = (v) => String(v).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

// ---------- settings (remembered in this browser) ----------
const params = new URLSearchParams(location.search);
const SOLO = KINDS[params.get('solo')] ? params.get('solo') : null;      // a popped-out window shows one task only
const DEFAULTS = { style: 'darktest', placeLabels: false, grid: 'tl', labels: true, trails: true, vectors: true,
  airports: true, runways: true, apLabels: 'full', rate: '1000', statusbar: true, cat: 'general', comms: true, stca: true, voiceIn: true, pttKey: 'Backquote', pttName: '`', pttMode: 'hold', pttHeadset: false, pttGamepad: false, voiceEngine: 'auto', voiceOut: true, voiceRate: '1.1', gndDiagram: true, gndTheme: 'real', twrChart: true, voiceNatural: true, radioFx: true, speakAi: false, hearAiWindows: true, showAiComms: true,
  clock24: 'true', tz: 'system', showUtc: false, showSeconds: true, layout: 1, panes: ['world', 'approach', 'tower', 'world'] };
const S = { ...DEFAULTS };
try { Object.assign(S, JSON.parse(localStorage.getItem('nightloop.settings') || '{}')); } catch (e) { /* ignore */ }
if (!S.v16) { S.gndTheme = 'real'; S.v16 = 1; }
if (SOLO) { S.layout = 1; S.panes = [SOLO]; document.body.classList.add('solo'); }
const saveS = () => {
  try {
    const keep = SOLO ? JSON.parse(localStorage.getItem('nightloop.settings') || '{}') : {};
    localStorage.setItem('nightloop.settings', JSON.stringify(SOLO ? { ...S, layout: keep.layout || 1, panes: keep.panes || DEFAULTS.panes } : S));
  } catch (e) { /* ignore */ }
};

// ---------- time (General settings) ----------
function tzOpt(o) { if (S.tz && S.tz !== 'system') o.timeZone = S.tz; return o; }
function fmtTime(d, sec) {
  try {
    return d.toLocaleTimeString(S.clock24 === 'true' ? 'en-GB' : 'en-US',
      tzOpt({ hour: '2-digit', minute: '2-digit', second: sec ? '2-digit' : undefined, hour12: S.clock24 !== 'true' }));
  } catch (e) { return d.toLocaleTimeString(); }
}
function tzName() {
  try { return new Intl.DateTimeFormat('en-US', tzOpt({ timeZoneName: 'short' })).formatToParts(new Date()).find((p) => p.type === 'timeZoneName').value; }
  catch (e) { return ''; }
}
const hhmm = (unix) => (unix ? `${fmtTime(new Date(unix * 1000), false)} ${tzName()}` : '-');
(function fillTz() {
  const sel = $('tzSel');
  let zones = [];
  try { zones = Intl.supportedValuesOf('timeZone'); } catch (e) {
    zones = ['UTC', 'America/Chicago', 'America/New_York', 'America/Denver', 'America/Los_Angeles', 'Europe/London', 'Europe/Paris', 'Asia/Kolkata', 'Asia/Dubai', 'Asia/Singapore', 'Asia/Tokyo', 'Australia/Sydney'];
  }
  if (!zones.includes('UTC')) zones.unshift('UTC');
  sel.insertAdjacentHTML('beforeend', zones.map((z) => `<option value="${z}">${z.replace(/_/g, ' ')}</option>`).join(''));
}());

// ---------- map styles ----------
const esri = (svc, o = {}) => L.tileLayer(`https://server.arcgisonline.com/ArcGIS/rest/services/${svc}/MapServer/tile/{z}/{y}/{x}`,
  { maxNativeZoom: 16, maxZoom: 18, attribution: 'Tiles &copy; Esri', ...o });
const OFM_ATTR = '<a href="https://openfreemap.org" target="_blank">OpenFreeMap</a> &copy; OpenMapTiles &copy; OpenStreetMap';
const gl = (style) => () => L.maplibreGL({ style, attribution: OFM_ATTR });
const STYLE_MAKERS = {
  darktest: gl('darkmap_test.json'),
  street: () => L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19, attribution: '&copy; OpenStreetMap contributors' }),
  dark: () => esri('Canvas/World_Dark_Gray_Base'),
  satellite: () => esri('World_Imagery'),
  terrain: () => L.tileLayer('https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png', { maxZoom: 17, attribution: '&copy; OpenStreetMap contributors, SRTM | &copy; OpenTopoMap (CC-BY-SA)' }),
  'ofm-dark': gl('https://tiles.openfreemap.org/styles/dark'), 'ofm-fiord': gl('https://tiles.openfreemap.org/styles/fiord'),
  'ofm-liberty': gl('https://tiles.openfreemap.org/styles/liberty'),
  'osm-dark': () => L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19, className: 'dark-tiles', attribution: '&copy; OpenStreetMap contributors' }),
};

// ---------- aircraft canvas as a map layer (scales smoothly with zoom animation) ----------
const RadarLayer = L.Renderer.extend({
  options: { padding: 0.25 },
  _initContainer() { this._container = document.createElement('canvas'); this._container.className = 'nl-radar'; this._ctx = this._container.getContext('2d'); },
  _destroyContainer() { L.DomUtil.remove(this._container); },
  _updateTransform(center, zoom) {
    L.Renderer.prototype._updateTransform.call(this, center, zoom);
    this._scaled = !(this._center && zoom === this._zoom && center.equals(this._center));
  },
  _update() {
    if (this._map._animatingZoom && this._bounds) return;
    L.Renderer.prototype._update.call(this);
    const b = this._bounds, size = b.getSize(), dpr = window.devicePixelRatio || 1;
    L.DomUtil.setTransform(this._container, b.min, 1);
    this._container.width = Math.round(size.x * dpr); this._container.height = Math.round(size.y * dpr);
    this._container.style.width = size.x + 'px'; this._container.style.height = size.y + 'px';
    this._scaled = false;
  },
});

// ---------- shared state ----------
let home = null, towerData = null, selected = null, selAirport = null, route = null;
let lastErr = '', frames = 0, fps = 0, fpsT = performance.now(), aiInfo = null, inputsSet = false, versionWarn = '';
const tgts = new Map();
let views = [], focused = null;

// ---------- one window (World radar / Approach / Tower) ----------
const ZOOM_SVG = '<button class="zin" title="Zoom in"><svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg></button>' +
  '<button class="zout" title="Zoom out"><svg viewBox="0 0 24 24"><path d="M5 12h14"/></svg></button>' +
  '<button class="zhome" title="Back to the airport / your view"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="4"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3"/></svg></button>';
function zoneBounds(h, nm) {
  const dl = nm / 60, dn = nm / 60 / Math.max(Math.cos(h.lat * D2R), 0.2);
  return L.latLngBounds([h.lat - dl, h.lon - dn], [h.lat + dl, h.lon + dn]);
}
class View {
  constructor(kind, el, idx) {
    this.kind = kind; this.el = el; this.idx = idx; this.cfg = KINDS[kind];
    this.airports = []; this.runways = []; this.navaids = []; this.screenPts = []; this.apPts = []; this.ap = null;
    el.className = 'pane' + (chartKind(kind) ? ' diagram' + (S.gndTheme === 'chart' ? ' chart' : S.gndTheme === 'real' ? ' real' : '') : '');
    const kindSel = SOLO ? '' : `<select class="pkind" title="Change what this window shows">${Object.keys(KINDS).map((k) => `<option value="${k}"${k === kind ? ' selected' : ''}>${KINDS[k].title}</option>`).join('')}</select>`;
    el.innerHTML = `<div class="phd">${kindSel}<span class="ptitle">${kindSel ? '' : this.cfg.title}<em></em></span>` +
      (kind !== 'world' ? '<input class="pap" placeholder="Airport" maxlength="4" title="Type an airport (ICAO) and press Enter to move this task there">' : '') +
      (SOLO ? '' : '<button class="pbtn ppop" title="Open this window in a new browser window">&#x29C9;</button><button class="pbtn pclosew" title="Close this window">&times;</button>') +
      `</div><div class="pmap"></div><div class="pzoom">${ZOOM_SVG}</div>` + (kind === 'tower' ? TOWER_SIDE : kind === 'approach' ? APP_SIDE : kind === 'center' ? CENTER_SIDE : kind === 'ground' ? GROUND_SIDE : '');
    const opts = { zoomSnap: 0, zoomDelta: 0.5, wheelPxPerZoomLevel: 90, wheelDebounceTime: 10, zoomControl: false, maxZoom: 17 };
    if (kind === 'world') Object.assign(opts, { worldCopyJump: true, minZoom: 2, maxBounds: [[-85.05, -100000], [85.05, 100000]], maxBoundsViscosity: 1 });
    else Object.assign(opts, { maxBoundsViscosity: 1, minZoom: 3 });
    const start = home || { lat: 41.98, lon: -87.9 };
    this.map = L.map(el.querySelector('.pmap'), opts).setView([start.lat, start.lon], kind === 'world' ? (S.worldView ? S.worldView[2] : 6) : this.cfg.zoom);
    if (kind === 'world' && S.worldView) this.map.setView([S.worldView[0], S.worldView[1]], S.worldView[2]);
    this.radar = new RadarLayer(); this.map.addLayer(this.radar); this.ctx = this.radar._ctx;
    this.base = null; this.place = null; this.applyStyle();
    this.map.on('moveend', () => {
      clearTimeout(this.mt); this.mt = setTimeout(() => this.loadAirports(), 250);
      if (this.kind === 'world' && !SOLO) { const c = this.map.getCenter(); S.worldView = [c.lat, c.lng, this.map.getZoom()]; saveS(); }
    });
    this.map.on('resize', () => { this.fitMin(); this.radar._update(); });
    this.map.on('click', (e) => onMapClick(this, e));
    this.map.on('click dragstart', closePanel);
    el.addEventListener('mousedown', () => setFocus(this), true);
    el.querySelector('.zin').onclick = () => this.map.zoomIn(1);
    el.querySelector('.zout').onclick = () => this.map.zoomOut(1);
    el.querySelector('.zhome').onclick = () => (this.ap ? this.focusAirport(this.ap) : home && this.map.flyTo([home.lat, home.lon], 7));
    const pop = el.querySelector('.ppop'), cls = el.querySelector('.pclosew'), pap = el.querySelector('.pap');
    const ksel = el.querySelector('.pkind');
    if (ksel) ksel.onchange = () => setPaneKind(this.idx, ksel.value);
    if (pop) pop.onclick = () => window.open(`/?solo=${kind}`, `nightloop_${kind}_${Date.now()}`, 'width=1200,height=800');
    if (cls) cls.onclick = () => closeView(this);
    if (pap) pap.addEventListener('keydown', (e) => { if (e.key === 'Enter') setHomeAirport(pap.value.trim().toUpperCase(), pap); });
    if (kind === 'tower') wireTowerSide(el);
    if (kind === 'center') wireCenterSide(el);
    if (kind === 'ground') wireGroundSide(el);
    if (kind !== 'world' && home) this.focusAirport(home);
    this.fitMin();
    this.loadAirports();
  }
  applyStyle() {
    if (this.base) { this.map.removeLayer(this.base); this.base = null; }
    if (this.place) { this.map.removeLayer(this.place); this.place = null; }
    if (chartKind(this.kind)) {             // airport diagram drawn by NIGHTLOOP (or a satellite photo)
      if (S.gndTheme === 'sat') { this.base = esri('World_Imagery', { maxNativeZoom: 19, maxZoom: 20 }); this.base.addTo(this.map); }
      return;
    }
    if (!STYLE_MAKERS[S.style]) S.style = 'darktest';
    try { this.base = STYLE_MAKERS[S.style](); this.base.addTo(this.map); }
    catch (e) { console.error('Map style failed', e); this.base = STYLE_MAKERS.street(); this.base.addTo(this.map); }
    if (this.place) { this.map.removeLayer(this.place); this.place = null; }
    if (S.placeLabels && S.style === 'dark') { this.place = esri('Canvas/World_Dark_Gray_Reference'); this.place.addTo(this.map); }
  }
  fitMin() {
    if (this.kind === 'world') {
      const s = this.map.getSize();
      const mz = Math.max(1, Math.ceil(Math.log2(Math.max(s.x, s.y) / 256) * 4) / 4);
      this.map.setMinZoom(mz);
      if (this.map.getZoom() < mz) this.map.setZoom(mz);
    } else if (this.ap) {
      const mz = this.map.getBoundsZoom(zoneBounds(this.ap, this.cfg.zone * 1.35), false);
      this.map.setMinZoom(mz);
      if (this.map.getZoom() < mz) this.map.setZoom(mz);
    }
  }
  focusAirport(h) {    // task window: centre on the airport, limit zoom-out to the recommended zone
    this.ap = { icao: h.icao, lat: h.lat, lon: h.lon, name: h.name || '' };
    this.el.querySelector('.ptitle em').textContent = h.icao;
    const pap = this.el.querySelector('.pap'); if (pap && document.activeElement !== pap) pap.value = h.icao;
    this.map.setMaxBounds(zoneBounds(h, this.cfg.zone * 2.2));
    this.map.setMinZoom(1);
    this.map.setView([h.lat, h.lon], this.cfg.zoom, { animate: false });
    this.fitMin();
    if (this.map.getZoom() < this.cfg.zoom) this.map.setZoom(this.cfg.zoom);
    const side = this.el.querySelector('.tside');      // keep the airport in the free part of the window, not under the side list
    if (side && !side.classList.contains('min') && this.map.getSize().x > 700) this.map.panBy([Math.round((side.offsetWidth + 16) / 2), 0], { animate: false });
  }
  bounds() {
    const b = this.map.getBounds();
    let w = b.getWest(), e = b.getEast();
    if (e - w >= 359) { w = -180; e = 180; } else { w = wrap(w); e = wrap(e); }
    const f = (x) => x.toFixed(3);
    return [f(Math.max(b.getSouth(), -90)), f(w), f(Math.min(b.getNorth(), 90)), f(e), this.map.getZoom().toFixed(2)];
  }
  async loadAirports() {
    clearTimeout(this.at);
    if (!S.airports) { this.airports = []; this.runways = []; return; }
    try {
      const [s, w, n, e, z] = this.bounds();
      const d = await (await fetch(`/api/airports?s=${s}&w=${w}&n=${n}&e=${e}&z=${z}`)).json();
      if (d.loading) { this.apNote = d.error ? 'Airport data error: ' + d.error : 'Airport data loading...'; this.at = setTimeout(() => this.loadAirports(), 4000); return; }
      this.apNote = ''; this.airports = d.airports; this.runways = d.runways;
      if (this.kind !== 'world' || +z >= 8) {
        const nv = await (await fetch(`/api/navaids?s=${s}&w=${w}&n=${n}&e=${e}&z=${z}`)).json();
        this.navaids = nv.navaids || [];
      } else this.navaids = [];
    } catch (e) { this.at = setTimeout(() => this.loadAirports(), 5000); }
  }
  destroy() { clearTimeout(this.mt); clearTimeout(this.at); this.map.remove(); }
}

const TOWER_SIDE = `<div class="tside"><div class="tsh"><b>TOWER</b><button class="tmin">&ndash;</button></div>
  <label><input type="checkbox" class="twrOn"> Tower control on</label>
  <div class="muted twrWx"></div>
  <label class="sel">Arrival runway <select class="twrArr"></select></label>
  <label class="sel">Departure runway <select class="twrDep"></select></label>
  <h3>Departures waiting</h3><div class="twrDeps"></div>
  <h3>Arrivals on approach</h3><div class="twrArrs"></div>
  <h3>Runway status</h3><div class="twrOcc muted"></div></div>`;
const CENTER_SIDE = `<div class="tside"><div class="tsh"><b>CENTER</b><button class="tmin">&ndash;</button></div>
  <label><input type="checkbox" class="ctrOn"> Center control on</label>
  <label class="sel">Sector size <select class="ctrRad"><option value="100">100 NM</option><option value="150">150 NM</option><option value="200">200 NM</option><option value="250">250 NM</option><option value="300">300 NM</option></select></label>
  <div class="muted" style="margin-top:4px">Hand off arrivals to Approach before 40 NM, and departures / overflights before they leave the sector.</div>
  <h3>Arrivals</h3><div class="ctrArr"></div>
  <h3>Departures</h3><div class="ctrDep"></div>
  <h3>Overflights</h3><div class="ctrOvf"></div></div>`;
const GROUND_SIDE = `<div class="tside"><div class="tsh"><b>GROUND</b><button class="tmin">&ndash;</button></div>
  <label><input type="checkbox" class="gndOn"> Ground control on</label>
  <div class="muted gndNote" style="margin:4px 0"></div>
  <div class="crow"><button class="gndReload" title="Download the taxiways and gates of this airport again">Reload taxiways</button></div>
  <div class="muted" style="margin-top:4px">Departures: Push back, then Taxi (to the departure runway, Tower takes over at the holding point). Arrivals: Taxi to the gate. Planes stop before runways they must cross: press Cross.</div>
  <h3>Waiting for you</h3><div class="gndWait"></div>
  <h3>Moving</h3><div class="gndMove"></div></div>`;
const APP_SIDE = `<div class="tside"><div class="tsh"><b>INBOUND</b><button class="tmin">&ndash;</button></div>
  <div class="muted" style="margin-top:2px">AI arrivals within 80 NM of the airport. Click to select.</div><div class="appList"></div></div>`;

class GuideView {
  constructor(kind, el, idx) {
    this.kind = kind; this.el = el; this.idx = idx; this.cfg = KINDS[kind]; this.screenPts = []; this.apPts = []; this.airports = []; this.runways = []; this.navaids = [];
    el.className = 'pane guide';
    const kindSel = SOLO ? '' : `<select class="pkind">${Object.keys(KINDS).map((k) => `<option value="${k}"${k === kind ? ' selected' : ''}>${KINDS[k].title}</option>`).join('')}</select>`;
    el.innerHTML = `<div class="phd">${kindSel}<span class="ptitle">${kindSel ? '' : this.cfg.title}<em></em></span>` +
      (SOLO ? '' : '<button class="pbtn ppop" title="Open in a new window">&#x29C9;</button><button class="pbtn pclosew" title="Close this window">&times;</button>') +
      '</div><iframe class="gframe" src="help.html" title="Training guide"></iframe>';
    const ksel = el.querySelector('.pkind'); if (ksel) ksel.onchange = () => setPaneKind(this.idx, ksel.value);
    const pop = el.querySelector('.ppop'); if (pop) pop.onclick = () => window.open('/?solo=guide', `nightloop_guide_${Date.now()}`, 'width=1100,height=800');
    const cls = el.querySelector('.pclosew'); if (cls) cls.onclick = () => closeView(this);
    el.addEventListener('mousedown', () => setFocus(this), true);
    this.map = { invalidateSize() {}, getZoom() { return 0; }, flyTo() {}, panTo() {}, remove() {} };
  }
  applyStyle() {} fitMin() {} focusAirport() {} loadAirports() {} destroy() {}
  bounds() { return null; }
}
const makeView = (kind, el, idx) => (kind === 'guide' ? new GuideView(kind, el, idx) : new View(kind, el, idx));
// ---------- layout manager (1-4 windows) ----------
function buildLayout() {
  views.forEach((v) => v.destroy());
  views = [];
  const host = $('panes');
  host.className = 'l' + S.layout; host.innerHTML = '';
  for (let i = 0; i < S.layout; i++) {
    const el = document.createElement('div'); host.appendChild(el);
    views.push(makeView(S.panes[i] || 'world', el, i));
  }
  setFocus(views[0]);
  document.querySelectorAll('#layouts button').forEach((b) => b.classList.toggle('on', +b.dataset.n === S.layout));
  markRail();
  if (centerData) renderCenter(centerData);
  if (groundData) renderGround(groundData);
  setTimeout(() => views.forEach((v) => { v.map.invalidateSize(); v.fitMin(); }), 50);
  if (towerData) renderTower(towerData);
}
function setFocus(v) {
  focused = v;
  views.forEach((x) => x.el.classList.toggle('focus', x === v && views.length > 1));
  markRail();
}
function setLayout(n) {
  const kinds = views.map((v) => v.kind);
  while (kinds.length < n) kinds.push(['world', 'approach', 'tower'].find((k) => !kinds.includes(k)) || 'world');
  S.layout = n; S.panes = kinds.slice(0, n).concat(S.panes.slice(n)).slice(0, 4); saveS(); buildLayout();
}
function markRail() {
  document.querySelectorAll('.rb.task').forEach((b) => {
    b.classList.toggle('open', views.some((v) => v.kind === b.dataset.kind));
    b.classList.toggle('cur', !!focused && focused.kind === b.dataset.kind);
  });
}
function openTask(kind) {     // a task opens on its own, full screen; split screens only via the window buttons
  if (!KINDS[kind]) return;
  if (views.length === 1 && views[0].kind === kind) return;
  S.layout = 1; S.panes[0] = kind; saveS(); buildLayout(); pollNow();
}
function setPaneKind(idx, kind) {   // split mode: change one window with its header menu
  if (!KINDS[kind] || !views[idx]) return;
  const old = views[idx];
  const el = document.createElement('div');
  old.destroy(); old.el.replaceWith(el);
  views[idx] = makeView(kind, el, idx);
  S.panes[idx] = kind; saveS();
  if (chartKind(kind)) loadDiagram(true);
  setFocus(views[idx]);
  setTimeout(() => { views[idx].map.invalidateSize(); views[idx].fitMin(); }, 50);
  if (towerData) renderTower(towerData);
  if (centerData) renderCenter(centerData);
  if (groundData) renderGround(groundData);
  pollNow();
}
function closeView(v) {
  const kinds = views.map((x) => x.kind);
  if (kinds.length === 1) { S.panes[0] = 'world'; }
  else { kinds.splice(v.idx, 1); S.layout = kinds.length; S.panes = kinds.concat(S.panes.slice(kinds.length + 1)).slice(0, 4); }
  saveS(); buildLayout();
}
document.querySelectorAll('#layouts button').forEach((b) => b.addEventListener('click', () => setLayout(+b.dataset.n)));
document.querySelectorAll('.rb.task').forEach((b) => b.addEventListener('click', () => {
  openTask(b.dataset.kind);
}));

// ---------- settings panel ----------
const isOpen = () => document.body.classList.contains('open');
function showCat(cat) {
  S.cat = cat; saveS();
  document.querySelectorAll('#panel section').forEach((s) => s.classList.toggle('show', s.dataset.cat === cat));
  document.querySelectorAll('.tabs button').forEach((b) => b.classList.toggle('on', b.dataset.cat === cat));
  $('gear').classList.toggle('active', isOpen());
}
function openPanel(cat) { document.body.classList.add('open'); showCat(cat || S.cat || 'general'); }
function closePanel() { document.body.classList.remove('open'); showCat(S.cat); }
const togglePanel = () => (isOpen() ? closePanel() : openPanel());
$('gear').addEventListener('click', togglePanel);
$('expand').addEventListener('click', togglePanel);
$('panelClose').addEventListener('click', closePanel);
document.querySelectorAll('.tabs button').forEach((b) => b.addEventListener('click', () => showCat(b.dataset.cat)));
if (!document.querySelector(`#panel section[data-cat="${S.cat}"]`)) S.cat = 'general';
showCat(S.cat);

document.querySelectorAll('[data-opt]').forEach((el) => {
  const k = el.dataset.opt;
  if (el.type === 'checkbox') el.checked = !!S[k]; else el.value = String(S[k]);
  el.addEventListener('change', () => {
    S[k] = el.type === 'checkbox' ? el.checked : el.value;
    saveS();
    if (k === 'style' || k === 'placeLabels') views.forEach((v) => v.applyStyle());
    if (k === 'airports') views.forEach((v) => v.loadAirports());
    if (k === 'comms') $('comms').hidden = !S.comms;
    if (k === 'tz' || k === 'clock24') updateClockZone();
    if (k === 'gndDiagram' || k === 'gndTheme' || k === 'twrChart') buildLayout();
    if (k === 'pttHeadset' && S.pttHeadset) armHeadset();
    if (k === 'voiceEngine') loadVoiceLocal();
  });
});
$('comms').hidden = !S.comms;

// ---------- home airport (used by all task windows) ----------
function setHome(h) {
  const changed = !home || home.icao !== h.icao;
  home = h;
  if (document.activeElement !== $('homeIcao')) $('homeIcao').value = h.icao;
  if (changed) views.forEach((v) => { if (v.kind !== 'world') v.focusAirport(h); });
}
async function setHomeAirport(icao, inputEl) {
  if (!icao) return;
  try {
    const r = await fetch('/api/home', { method: 'POST', body: JSON.stringify({ icao }) });
    const d = await r.json();
    if (!r.ok) { addLocal(d.error, 'err'); if (inputEl) inputEl.value = home ? home.icao : ''; $('homeMsg').textContent = d.error; return; }
    home = null; setHome(d.home);
    $('homeMsg').textContent = `${d.home.icao} - ${d.home.name || ''}`;
    addLocal(`Task windows moved to ${d.home.icao} ${d.home.name || ''}`, 'event');
    pollTower();
  } catch (e) { addLocal('Server not reachable', 'err'); }
}
$('homeBtn').addEventListener('click', () => setHomeAirport($('homeIcao').value.trim().toUpperCase(), $('homeIcao')));
$('homeIcao').addEventListener('keydown', (e) => { if (e.key === 'Enter') $('homeBtn').click(); });

// ---------- AI controls, refresh ----------
const post = (url, body) => fetch(url, { method: 'POST', body: JSON.stringify(body) }).then((r) => r.json());
$('aiBtn').addEventListener('click', async () => {
  const count = parseInt($('aiCount').value, 10), share = parseFloat($('aiShare').value);
  if (isNaN(count) || count < 0 || count > 5000) { $('aiMsg').textContent = 'Enter a number from 0 to 5000'; return; }
  try {
    const d = await post('/api/ai', { count, home_share: isNaN(share) ? 0.1 : share / 100 });
    $('aiMsg').textContent = `${d.count} AI flights worldwide, ${Math.round(d.home_share * 100)}% to or from the home airport.`;
  } catch (e) { $('aiMsg').textContent = 'Server not reachable'; }
});
['aiShare', 'aiCount'].forEach((id) => $(id).addEventListener('keydown', (e) => { if (e.key === 'Enter') $('aiBtn').click(); }));
$('aiPause').addEventListener('click', async () => {
  try { const d = await post('/api/ai', { paused: !(aiInfo && aiInfo.paused) }); aiInfo = { ...(aiInfo || {}), ...d }; renderAi(); pollNow(); } catch (e) { /* ignore */ }
});
function renderAi() {
  if (!aiInfo) return;
  $('aiPause').textContent = aiInfo.paused ? 'Resume AI planes' : 'Pause AI planes';
  $('aiPause').classList.toggle('on', !!aiInfo.paused);
  $('aiState').textContent = aiInfo.paused ? `Paused since ${hhmm(aiInfo.paused_at)}` : 'Running on the real clock';
}
$('refreshBtn').addEventListener('click', async () => {
  $('refreshBtn').textContent = 'Refreshing...';
  try { await post('/api/refresh', {}); } catch (e) { /* ignore */ }
  setTimeout(() => { pollNow(); $('refreshBtn').textContent = 'Refresh now'; }, 2500);
});

// ---------- search: flights and airports (Ctrl+F) ----------
let searchTimer = null, results = [], hi = -1, scope = 'all';
document.addEventListener('keydown', (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'f') { e.preventDefault(); $('search').focus(); $('search').select(); }
});
$('search').addEventListener('input', () => { clearTimeout(searchTimer); searchTimer = setTimeout(runSearch, 200); });
$('search').addEventListener('focus', () => { if ($('search').value.trim().length >= 2) runSearch(); });
$('search').addEventListener('keydown', (e) => {
  if (e.key === 'ArrowDown') { hi = Math.min(results.length - 1, hi + 1); paintResults(); e.preventDefault(); }
  else if (e.key === 'ArrowUp') { hi = Math.max(0, hi - 1); paintResults(); e.preventDefault(); }
  else if (e.key === 'Enter' && results.length) pick(results[Math.max(0, hi)]);
  else if (e.key === 'Escape') { $('results').hidden = true; $('search').blur(); }
});
$('search').addEventListener('blur', () => setTimeout(() => { if (!$('results').matches(':hover')) $('results').hidden = true; }, 200));
document.querySelectorAll('.scopes button').forEach((b) => b.addEventListener('mousedown', (e) => {
  e.preventDefault(); scope = b.dataset.k;
  document.querySelectorAll('.scopes button').forEach((x) => x.classList.toggle('on', x === b));
  runSearch();
}));
async function runSearch() {
  const q = $('search').value.trim();
  if (q.length < 2) { $('results').hidden = true; return; }
  try {
    const d = await (await fetch(`/api/search?kind=${scope}&q=` + encodeURIComponent(q))).json();
    results = [...d.aircraft.map((a) => ({ kind: 'ac', ...a })), ...d.airports.map((a) => ({ kind: 'ap', ...a }))];
    hi = results.length ? 0 : -1;
    paintResults();
  } catch (e) { /* ignore */ }
}
function paintResults() {
  const box = $('resList');
  $('results').hidden = false;
  if (!results.length) { box.innerHTML = '<div class="res"><span class="muted">No match</span></div>'; return; }
  let html = '', last = '';
  results.forEach((r, i) => {
    const g = r.kind === 'ac' ? 'FLIGHTS' : 'AIRPORTS';
    if (g !== last) { html += `<div class="rgroup">${g}</div>`; last = g; }
    html += r.kind === 'ac'
      ? `<div class="res${i === hi ? ' hi' : ''}" data-i="${i}"><i style="background:${COLORS[r.source]}"></i><b>${esc(r.callsign)}</b> ${esc(r.type || '')}<small>${esc(NAMES[r.source])}${r.dep || r.arr ? ' · ' + esc((r.dep || '?') + ' → ' + (r.arr || '?')) : ''}${r.ground ? ' · ground' : r.alt ? ' · ' + Math.round(r.alt / 100) * 100 + ' ft' : ''}</small></div>`
      : `<div class="res${i === hi ? ' hi' : ''}" data-i="${i}"><i style="background:#9fb4b4;border-radius:50%"></i><b>${esc(r.code)}</b> ${esc(r.name)}<small>${esc(r.city || '')}</small></div>`;
  });
  box.innerHTML = html;
  box.querySelectorAll('.res[data-i]').forEach((el) => el.addEventListener('mousedown', (e) => { e.preventDefault(); pick(results[+el.dataset.i]); }));
}
function mapForGoto() { return (focused && focused.kind === 'world' ? focused : views.find((v) => v.kind === 'world') || focused); }
function pick(r) {
  $('results').hidden = true; $('search').blur();
  const v = mapForGoto();
  if (r.kind === 'ac') { if (v.kind === 'world') v.map.flyTo([r.lat, r.lon], Math.max(v.map.getZoom(), 7), { duration: 1.2 }); selAirport = null; selectTarget(r.id); }
  else if (v.kind === 'world') { v.map.flyTo([r.lat, r.lon], 10, { duration: 1.2 }); selected = null; route = null; selAirport = { ...r, elev: '', type: r.type || '' }; renderCard(); }
  else setHomeAirport(r.code);
}

// ---------- source status ----------
const feedEls = {}, provEls = {};
function renderFeeds(feeds) {
  const st = { ai: 'stAi', vatsim: 'stVatsim', ivao: 'stIvao', real: 'stReal' };
  for (const f of feeds) {
    if (st[f.name]) $(st[f.name]).textContent = f.enabled ? f.count.toLocaleString() : 'off';
    let el = feedEls[f.name];
    if (!el) {
      const box = document.createElement('div');
      box.className = 'feed';
      box.innerHTML = `<input type="checkbox"><span class="sw" style="background:${COLORS[f.name]}"></span><span>${NAMES[f.name] || f.name}</span><span class="cnt"></span><span class="fstat"></span>`;
      $('feeds').appendChild(box);
      el = feedEls[f.name] = { cb: box.querySelector('input'), cnt: box.querySelector('.cnt'), st: box.querySelector('.fstat') };
      el.cb.addEventListener('change', () => post('/api/sources', { name: f.name, enabled: el.cb.checked }));
    }
    el.cb.checked = f.enabled;
    el.cnt.textContent = f.enabled ? f.count.toLocaleString() : 'off';
    el.st.className = 'fstat' + (f.error ? ' err' : '');
    el.st.textContent = !f.enabled ? 'Disabled' : f.error ? f.error : f.age == null ? 'Waiting for first update...' : `Updated ${Math.round(f.age)} s ago` + (f.note ? ` - ${f.note}` : '');
    if (f.providers) renderProviders(f.providers);
  }
}
function renderProviders(provs) {
  for (const [name, p] of Object.entries(provs)) {
    let el = provEls[name];
    if (!el) {
      const box = document.createElement('label');
      box.className = 'prov';
      box.innerHTML = `<input type="checkbox"><span>${PROV_NAMES[name] || name}</span><span class="ps"></span>`;
      $('providers').appendChild(box);
      el = provEls[name] = { cb: box.querySelector('input'), st: box.querySelector('.ps') };
      el.cb.addEventListener('change', async () => { const d = await post('/api/providers', { name, enabled: el.cb.checked }); $('provMsg').textContent = d.message ? `FreeFlight: ${d.message}` : ''; });
    }
    el.cb.checked = p.enabled;
    el.st.className = 'ps' + (p.ok === false ? ' err' : '');
    el.st.textContent = !p.enabled ? 'off' : p.cooling ? `resting ${p.cooling} s - ${p.msg || ''}` : p.last ? (p.ok ? `OK at ${p.last} (${p.ms} ms)` : `${p.msg} at ${p.last}`) : 'waiting for its turn...';
  }
}

// ---------- traffic polling (one request for all windows) ----------
let pollTimer = null;
async function pollNow() {
  clearTimeout(pollTimer);
  try {
    const b = views.map((v) => v.bounds()).filter(Boolean).map((x) => x.join(',')).join('|') || '0,0,0.01,0.01,10';
    const d = await (await fetch('/api/traffic?b=' + encodeURIComponent(b))).json();
    const now = performance.now() / 1000, seen = new Set();
    for (const t of d.targets) {
      seen.add(t.id);
      const old = tgts.get(t.id);
      const trail = old ? old.trail : [];
      if (!old || old.lat !== t.lat || old.lon !== t.lon) { trail.push([t.lat, t.lon]); if (trail.length > 8) trail.shift(); }
      t.trail = trail;
      t.rx = now - (t.age || 0);
      if (old && !t.ground && !t.frozen) {
        const a = posNow(old, now), bb = posNow(t, now);
        const dl = a[0] - bb[0], dn = a[1] - bb[1];
        if (Math.abs(dl) < 0.3 && Math.abs(dn) < 0.3) { t.cor = [dl, dn]; t.corT = now; }
      }
      tgts.set(t.id, t);
    }
    for (const id of [...tgts.keys()]) if (!seen.has(id) && id !== selected) tgts.delete(id);
    renderFeeds(d.feeds);
    setHome(d.home);
    aiInfo = { ...d.ai }; renderAi();
    if (d.version && d.version !== APP_VERSION) {
      try {           // after an update the server restarts with new files: load the new page once
        if (sessionStorage.getItem('nlReload') !== d.version) { sessionStorage.setItem('nlReload', d.version); location.reload(); return; }
      } catch (e) { /* ignore */ }
    }
    versionWarn = d.version !== APP_VERSION ? `The server is version ${d.version || 'old'}, the page is ${APP_VERSION}. Close ALL NIGHTLOOP windows, then start NIGHTLOOP again.` : '';
    if (!inputsSet) { $('aiCount').value = d.ai.count; $('aiShare').value = Math.round(d.ai.home_share * 100); $('ver').textContent = `Version ${d.version} (browser prototype)`; inputsSet = true; }
    lastErr = '';
    hideBoot();
    renderCard(); renderApproach();
  } catch (e) {
    console.error(e);
    lastErr = 'Server not reachable - is START_SIM.bat running?';
  }
  pollTimer = setTimeout(pollNow, parseInt(S.rate, 10) || 1000);
}

// ---------- geometry ----------
function deadReckon(t, secs) {
  const d = (t.gs || 0) * secs / 3600, r = (t.track || 0) * D2R;
  return [t.lat + d * Math.cos(r) / 60, t.lon + d * Math.sin(r) / (60 * Math.max(Math.cos(t.lat * D2R), 0.01))];
}
function posNow(t, now) {
  if (t.ground || t.frozen) return [t.lat, t.lon];
  const p = deadReckon(t, Math.min(Math.max(now - t.rx, 0), DR_CAP[t.source] || 20));
  if (t.cor) {
    const k = Math.max(0, 1 - (now - t.corT) / 2);
    if (k > 0) { p[0] += t.cor[0] * k; p[1] += t.cor[1] * k; } else t.cor = null;
  }
  return p;
}
function gcPoints(a, b, n = 96) {
  const la1 = a[0] * D2R, lo1 = a[1] * D2R, la2 = b[0] * D2R, lo2 = b[1] * D2R;
  const d = 2 * Math.asin(Math.min(1, Math.sqrt(Math.sin((la2 - la1) / 2) ** 2 + Math.cos(la1) * Math.cos(la2) * Math.sin((lo2 - lo1) / 2) ** 2)));
  if (d < 1e-9) return [a, [b[0], near(b[1], a[1])]];
  const pts = []; let prev = a[1];
  for (let i = 0; i <= n; i++) {
    const f = i / n, A = Math.sin((1 - f) * d) / Math.sin(d), B = Math.sin(f * d) / Math.sin(d);
    const x = A * Math.cos(la1) * Math.cos(lo1) + B * Math.cos(la2) * Math.cos(lo2);
    const y = A * Math.cos(la1) * Math.sin(lo1) + B * Math.cos(la2) * Math.sin(lo2);
    const z = A * Math.sin(la1) + B * Math.sin(la2);
    const lon = near(Math.atan2(y, x) / D2R, prev);
    pts.push([Math.atan2(z, Math.sqrt(x * x + y * y)) / D2R, lon]); prev = lon;
  }
  return pts;
}
const distNm = (a, b) => {
  const la1 = a[0] * D2R, la2 = b[0] * D2R, dl = (b[1] - a[1]) * D2R;
  return 2 * R_NM * Math.asin(Math.min(1, Math.sqrt(Math.sin((la2 - la1) / 2) ** 2 + Math.cos(la1) * Math.cos(la2) * Math.sin(dl / 2) ** 2)));
};
function moveNm(lat, lon, brg, nm) {
  const p1 = lat * D2R, l1 = lon * D2R, b = brg * D2R, a = nm / R_NM;
  const p2 = Math.asin(Math.sin(p1) * Math.cos(a) + Math.cos(p1) * Math.sin(a) * Math.cos(b));
  const l2 = l1 + Math.atan2(Math.sin(b) * Math.sin(a) * Math.cos(p1), Math.cos(a) - Math.sin(p1) * Math.sin(p2));
  return [p2 / D2R, l2 / D2R];
}

// ---------- drawing (one window at a time; these globals point at the window being drawn) ----------
let map = null, ctx = null, V = null, DPR = 1, CL = 0, OX = 0, OY = 0;
const pt = (lat, lon) => map.latLngToContainerPoint([lat, near(lon, CL)]);
const PLANE = new Path2D('M0,-10 L1.5,-6.5 L1.5,-2 L9.5,3 L9.5,5 L1.5,2.8 L1.1,7 L4,9.2 L4,10.6 L0,9.7 L-4,10.6 L-4,9.2 L-1.1,7 L-1.5,2.8 L-9.5,5 L-9.5,3 L-1.5,-2 L-1.5,-6.5 Z');
const fl = (alt) => String(Math.max(0, Math.round(alt / 100))).padStart(3, '0');
const fmtLat = (v) => `${Math.abs(+v.toFixed(2))}°${v > 0 ? 'N' : v < 0 ? 'S' : ''}`;
const fmtLon = (v) => { v = wrap(v); return `${Math.abs(+v.toFixed(2))}°${v > 0 && v < 180 ? 'E' : v < 0 ? 'W' : ''}`; };
const baseTf = () => ctx.setTransform(DPR, 0, 0, DPR, OX * DPR, OY * DPR);
function label(text, x, y, color, halo) {
  ctx.lineWidth = 3; ctx.lineJoin = 'round'; ctx.strokeStyle = halo; ctx.strokeText(text, x, y);
  ctx.fillStyle = color; ctx.fillText(text, x, y);
}
function drawGrid(s, z, light) {
  if (S.grid === 'off' || chartKind(V.kind)) return;
  const st = z < 3 ? 30 : z < 4 ? 20 : z < 5 ? 10 : z < 6 ? 5 : z < 7 ? 2 : z < 8.5 ? 1 : z < 10 ? 0.5 : z < 12 ? 0.25 : 0.1;
  const b = map.getBounds(), clat = map.getCenter().lat;
  const lineC = light ? 'rgba(20,35,50,0.45)' : 'rgba(170,205,225,0.32)';
  const txtC = light ? '#0d1b2a' : '#e8f3fa', halo = light ? 'rgba(255,255,255,0.95)' : 'rgba(0,0,0,0.9)';
  ctx.font = 'bold 12px Consolas, monospace';
  for (let i = Math.ceil(Math.max(b.getSouth(), -85) / st); i * st <= Math.min(b.getNorth(), 85); i++) {
    const la = i * st, y = Math.round(map.latLngToContainerPoint([la, CL]).y) + 0.5;
    ctx.strokeStyle = lineC; ctx.lineWidth = 1.2; ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(s.x, y); ctx.stroke();
    const txt = fmtLat(la);
    if (y > 48) label(txt, 6, y - 4, txtC, halo);
    if (S.grid === 'all') label(txt, s.x - ctx.measureText(txt).width - 50, y - 4, txtC, halo);
  }
  for (let i = Math.ceil(b.getWest() / st); i * st <= b.getEast(); i++) {
    const lo = i * st, x = Math.round(map.latLngToContainerPoint([clat, lo]).x) + 0.5;
    ctx.strokeStyle = lineC; ctx.lineWidth = 1.2; ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, s.y); ctx.stroke();
    const txt = fmtLon(lo);
    label(txt, x + 4, 56, txtC, halo);
    if (S.grid === 'all') label(txt, x + 4, s.y - 30, txtC, halo);
  }
}
function drawZone(light) {      // task windows only: range rings + recommended zone + extended runway centrelines
  if (V.kind === 'world' || !V.ap) return;
  const c = pt(V.ap.lat, V.ap.lon), px = map.latLngToContainerPoint([V.ap.lat + 1 / 60, near(V.ap.lon, CL)]);
  const r1 = Math.abs(c.y - px.y);
  ctx.lineWidth = 1;
  for (let nm = V.cfg.rings; nm < V.cfg.zone; nm += V.cfg.rings) {
    ctx.strokeStyle = light ? 'rgba(20,60,80,0.35)' : 'rgba(90,150,160,0.45)';
    ctx.beginPath(); ctx.arc(c.x, c.y, r1 * nm, 0, Math.PI * 2); ctx.stroke();
  }
  ctx.save(); ctx.setLineDash([10, 8]); ctx.lineWidth = 2; ctx.strokeStyle = 'rgba(240,71,154,0.85)';
  ctx.beginPath(); ctx.arc(c.x, c.y, r1 * V.cfg.zone, 0, Math.PI * 2); ctx.stroke(); ctx.restore();
  ctx.font = 'bold 11px Consolas, monospace';
  label(`${V.cfg.label || V.cfg.title + ' ZONE'} ${V.cfg.zone} NM`, c.x - 60, c.y - r1 * V.cfg.zone - 8, '#ff8cc6', 'rgba(0,0,0,0.9)');
  if (V.kind === 'center') {
    ctx.save(); ctx.setLineDash([4, 6]); ctx.lineWidth = 1.5; ctx.strokeStyle = 'rgba(0,217,255,0.7)';
    ctx.beginPath(); ctx.arc(c.x, c.y, r1 * 40, 0, Math.PI * 2); ctx.stroke(); ctx.restore();
    label('APPROACH 40 NM', c.x - 45, c.y - r1 * 40 - 6, '#7fe9ff', 'rgba(0,0,0,0.9)');
  }
  if (towerData && towerData.enabled && towerData.home && towerData.home.icao === V.ap.icao) {
    for (const [id, col] of [[towerData.arr_rwy, 'rgba(57,255,106,0.8)'], [towerData.dep_rwy, 'rgba(0,217,255,0.7)']]) {
      const e = towerData.runways.find((r) => r.id === id);
      if (!e) continue;
      const far = moveNm(e.lat, e.lon, (e.crs + 180) % 360, V.kind === 'tower' ? 10 : 18), a = pt(e.lat, e.lon), b = pt(far[0], far[1]);
      ctx.save(); ctx.setLineDash([6, 6]); ctx.lineWidth = 1.5; ctx.strokeStyle = col;
      ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke(); ctx.restore();
    }
  }
}
function drawAirports(s, z, light) {
  V.apPts = []; V.rwyHits = [];
  if (!S.airports) return;
  const halo = light ? 'rgba(255,255,255,0.9)' : 'rgba(0,0,0,0.85)';
  if (S.runways && z >= 9) {
    const mpp = 40075016.7 * Math.cos(map.getCenter().lat * D2R) / (256 * 2 ** z);       // metres per pixel
    const rw = Math.max(z >= 12 ? 5 : 3, 45 / mpp);
    const tw = towerData && towerData.enabled && towerData.home ? towerData : null;
    ctx.lineCap = 'butt';
    for (const r of V.runways) {
      const a = pt(r[0], r[1]), b = map.latLngToContainerPoint([r[2], near(r[3], CL)]);
      let col = light ? '#4d5b66' : '#9fb6c6', act = false;
      if (tw && Math.abs(r[0] - tw.home.lat) < 0.08 && Math.abs(r[1] - tw.home.lon) < 0.12) {
        const ids = [r[4], r[5]];
        if (ids.includes(tw.arr_rwy)) { col = '#39ff6a'; act = true; } else if (ids.includes(tw.dep_rwy)) { col = '#00d9ff'; act = true; }
      }
      const diag = chartKind(V.kind) && groundDiagram;
      if (diag && !act) continue;
      if (diag) { ctx.globalAlpha = 0.45; }
      if (act && !diag) { ctx.strokeStyle = 'rgba(0,0,0,0.7)'; ctx.lineWidth = rw + 4; ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke(); }
      ctx.strokeStyle = col; ctx.lineWidth = rw;
      ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
      ctx.globalAlpha = 1;
    }
    ctx.lineWidth = 1;
    if (z >= 10.5 || V.kind === 'tower') {     // runway names at each end, e.g. 10L / 28R
      ctx.font = 'bold 12px Consolas, monospace';
      for (const r of V.runways) {
        if (!r[4] && !r[5]) continue;
        const a = pt(r[0], r[1]), b = map.latLngToContainerPoint([r[2], near(r[3], CL)]);
        const dx = a.x - b.x, dy = a.y - b.y, L2 = Math.hypot(dx, dy) || 1;
        if (L2 < 12) continue;
        const ux = dx / L2, uy = dy / L2;
        for (const [id, p, sx, sy] of [[r[4], a, ux, uy], [r[5], b, -ux, -uy]]) {
          if (!id) continue;
          const w = ctx.measureText(id).width, sel = RB && RB.to === id;
          label(id, p.x + sx * 14 - w / 2, p.y + sy * 14 + 4, sel ? '#b02fc4' : light ? '#0d1b2a' : '#ffffff', halo);
          V.rwyHits.push([p.x + sx * 14 - w / 2, p.y + sy * 14 - 8, w, 14, id]);
        }
      }
    }
  }
  const boxes = [];
  ctx.font = '11px "Segoe UI", Arial, sans-serif';
  for (const a of V.airports) {
    const p = pt(a.lat, a.lon);
    if (p.x < -20 || p.y < -20 || p.x > s.x + 20 || p.y > s.y + 20) continue;
    const big = a.type === 'large_airport', med = a.type === 'medium_airport';
    const r = big ? 4.5 : med ? 3.2 : 2;
    ctx.strokeStyle = light ? '#33424d' : (big ? '#c9dbe6' : '#8ea6b4'); ctx.fillStyle = light ? 'rgba(40,60,70,0.25)' : 'rgba(160,190,200,0.25)';
    ctx.lineWidth = 1.2; ctx.beginPath(); ctx.arc(p.x, p.y, r, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
    V.apPts.push([p.x, p.y, a]);
    if (chartKind(V.kind)) continue;
    if (S.apLabels === 'off' || (!big && !med && z < 9.5)) continue;
    const full = S.apLabels === 'full' && (z >= 7 || (big && z >= 5));
    const txt = full ? `${a.code}  ${a.name}` : a.code;
    const w = ctx.measureText(txt).width, bx = p.x + r + 4, by = p.y - 6;
    if (boxes.some((q) => bx < q[0] + q[2] && bx + w > q[0] && by < q[1] + 13 && by + 13 > q[1])) continue;
    boxes.push([bx, by, w]);
    label(txt, bx, p.y + 4, light ? (big ? '#10202c' : '#33424d') : (big ? '#dfe9f0' : '#a9bcc8'), halo);
  }
}
function drawNavaids(s, z, light) {
  if (!V.navaids.length) return;
  const col = light ? '#2a5d7a' : '#6fb8d8', halo = light ? 'rgba(255,255,255,0.9)' : 'rgba(0,0,0,0.85)';
  ctx.font = '10px Consolas, monospace';
  const names = z >= 7 || V.kind === 'approach';
  for (const n of V.navaids) {
    const p = pt(n.lat, n.lon);
    if (p.x < -10 || p.y < -10 || p.x > s.x + 10 || p.y > s.y + 10) continue;
    ctx.strokeStyle = col; ctx.lineWidth = 1.3; ctx.beginPath();
    if (n.kind === 'VOR') {
      for (let i = 0; i <= 6; i++) { const a = i * Math.PI / 3, x = p.x + 5 * Math.cos(a), y = p.y + 5 * Math.sin(a); if (i) ctx.lineTo(x, y); else ctx.moveTo(x, y); }
      ctx.stroke(); ctx.fillStyle = col; ctx.fillRect(p.x - 1, p.y - 1, 2, 2);
    } else {
      ctx.setLineDash([1.5, 2]); ctx.arc(p.x, p.y, 4.5, 0, Math.PI * 2); ctx.stroke(); ctx.setLineDash([]);
      ctx.fillStyle = col; ctx.beginPath(); ctx.arc(p.x, p.y, 1.3, 0, Math.PI * 2); ctx.fill();
    }
    if (names) label(n.id, p.x + 7, p.y + 11, col, halo);
  }
}
let groundDiagram = null, diagramAp = null;
async function loadDiagram(force) {
  if (!groundData || !groundData.airport) return;
  if (!force && diagramAp === groundData.airport && groundDiagram) return;
  try {
    const d = await (await fetch('/api/ground/diagram')).json();
    if (!d.diagram) return;
    diagramAp = d.airport;
    const P = (pts) => { const a = new Float64Array(pts.length * 2); pts.forEach((q, i) => { const pp = L.CRS.EPSG3857.latLngToPoint(L.latLng(q[0], q[1]), 20); a[i * 2] = pp.x; a[i * 2 + 1] = pp.y; }); return a; };
    const apr = d.diagram.apron.map((x) => (Array.isArray(x[0]) && Array.isArray(x[0][0]) ? x : [x, '']));
    groundDiagram = {
      grass: (d.diagram.grass || []).map(P), bridge: (d.diagram.bridge || []).map(P), holdl: (d.diagram.holdl || []).map(P),
      stand: (d.diagram.stand || []).map(([pts, ref]) => [P(pts), ref]),
      apron: apr.map(([pts]) => P(pts)), apronName: apr.map(([pts, n]) => {
        if (!n) return null; let la = 0, lo = 0; pts.forEach((q) => { la += q[0]; lo += q[1]; }); return [la / pts.length, lo / pts.length, n]; }).filter(Boolean),
      bldg: d.diagram.bldg.map(P),
      rwy: d.diagram.rwy.map(([pts, w, ref]) => [P(pts), w, ref]), twy: d.diagram.twy.map(([pts, w, ref]) => [P(pts), w, ref]),
      hold: d.diagram.hold.map((q) => P([q])) };
  } catch (e) { /* ignore */ }
}
function drawDiagram(s, z) {
  if (!groundDiagram || !chartKind(V.kind)) return;
  const sc = 2 ** (z - 20), tl = map.project(map.containerPointToLatLng([0, 0]), z);
  const mpp = 40075016.7 * Math.cos(map.getCenter().lat * D2R) / (256 * 2 ** z);
  const path = (a, close) => {
    ctx.beginPath();
    for (let i = 0; i < a.length; i += 2) { const x = a[i] * sc - tl.x, y = a[i + 1] * sc - tl.y; if (i) ctx.lineTo(x, y); else ctx.moveTo(x, y); }
    if (close) ctx.closePath();
  };
  if (S.gndTheme === 'real') return drawRealistic(s, z, sc, tl, mpp, path);
  if (S.gndTheme === 'sat') {                     // photo underneath: only the hold-short points and stand numbers
    if (z >= 15) { ctx.fillStyle = '#ff3b4e'; for (const a of groundDiagram.hold) { const x = a[0] * sc - tl.x, y = a[1] * sc - tl.y; ctx.beginPath(); ctx.arc(x, y, 3, 0, Math.PI * 2); ctx.fill(); } }
    return;
  }
  const C = S.gndTheme === 'chart'
    ? { apron: '#e4ede7', apronEdge: '#b4c7bb', bldg: '#6f7a7a', bldgEdge: '#566161', twyEdge: '#7f8a8a', twy: '#a9b2b2', twyLine: null, rwy: '#727d7d', rwyEdge: '#ffffff', name: '#47574f' }
    : { apron: '#16202a', apronEdge: null, bldg: '#2b3743', bldgEdge: '#3d4b58', twyEdge: null, twy: '#3a4754', twyLine: 'rgba(242,194,48,0.55)', rwy: '#6d7a86', rwyEdge: null, name: '#7f8c98' };
  ctx.fillStyle = C.apron; ctx.strokeStyle = C.apronEdge || C.apron; ctx.lineWidth = 1;
  for (const a of groundDiagram.apron) { path(a, true); ctx.fill(); if (C.apronEdge) ctx.stroke(); }
  ctx.fillStyle = C.bldg; ctx.strokeStyle = C.bldgEdge;
  for (const a of groundDiagram.bldg) { path(a, true); ctx.fill(); ctx.stroke(); }
  ctx.lineCap = 'round'; ctx.lineJoin = 'round';
  if (C.twyEdge) { ctx.strokeStyle = C.twyEdge; for (const [a, w] of groundDiagram.twy) { ctx.lineWidth = Math.max(3, w / mpp) + 2; path(a); ctx.stroke(); } }
  ctx.strokeStyle = C.twy;
  for (const [a, w] of groundDiagram.twy) { ctx.lineWidth = Math.max(2, w / mpp); path(a); ctx.stroke(); }
  if (C.twyLine && z >= 14.5) { ctx.strokeStyle = C.twyLine; ctx.lineWidth = 1; for (const [a] of groundDiagram.twy) { path(a); ctx.stroke(); } }
  ctx.lineCap = 'butt';
  for (const [a, w] of groundDiagram.rwy) {
    const rw = Math.max(4, w / mpp);
    if (C.rwyEdge) { ctx.strokeStyle = C.rwyEdge; ctx.lineWidth = rw + 2; path(a); ctx.stroke(); }
    ctx.strokeStyle = C.rwy; ctx.lineWidth = rw; path(a); ctx.stroke();
    if (z >= 14) { ctx.save(); ctx.setLineDash([12, 10]); ctx.strokeStyle = 'rgba(255,255,255,0.85)'; ctx.lineWidth = 1.2; path(a); ctx.stroke(); ctx.restore(); }
  }
  if (z >= 15) { ctx.fillStyle = '#e0202e'; for (const a of groundDiagram.hold) { const x = a[0] * sc - tl.x, y = a[1] * sc - tl.y; ctx.fillRect(x - 3, y - 3, 6, 6); } }
  if (z >= 14) {
    ctx.font = 'bold 13px "Segoe UI", Arial, sans-serif'; ctx.fillStyle = C.name;
    for (const [la, lo, n] of groundDiagram.apronName) {
      const p = map.latLngToContainerPoint([la, lo]); const w = ctx.measureText(n).width;
      if (p.x > -w && p.y > 0 && p.x < s.x + w && p.y < s.y) ctx.fillText(n, p.x - w / 2, p.y + 4);
    }
  }
}
// realistic airport: grass, concrete aprons, asphalt with painted lines, runway markings, terminals with shadows, jet bridges, stands
function drawRealistic(s, z, sc, tl, mpp, path) {
  const G = groundDiagram, P = (a, i) => [a[i] * sc - tl.x, a[i + 1] * sc - tl.y];
  ctx.fillStyle = '#4b6b3c'; for (const a of G.grass || []) { path(a, true); ctx.fill(); }
  ctx.fillStyle = '#9ea4a6'; ctx.strokeStyle = '#8b9193'; ctx.lineWidth = 1;
  for (const a of G.apron) { path(a, true); ctx.fill(); ctx.stroke(); }
  ctx.lineCap = 'round'; ctx.lineJoin = 'round';
  ctx.strokeStyle = '#b9a24a'; for (const [a, w] of G.twy) { ctx.lineWidth = Math.max(3, w / mpp) + Math.max(1, 1.5 / mpp * 2); path(a); ctx.stroke(); }  // yellow edge lines
  ctx.strokeStyle = '#3b3f44'; for (const [a, w] of G.twy) { ctx.lineWidth = Math.max(3, w / mpp); path(a); ctx.stroke(); }
  if (z >= 14) { ctx.strokeStyle = '#e8c330'; ctx.lineWidth = Math.max(1, 0.4 / mpp * 2); for (const [a] of G.twy) { path(a); ctx.stroke(); } }
  ctx.lineCap = 'butt';
  for (const [a, w, ref] of G.rwy) {
    const rw = Math.max(5, w / mpp);
    ctx.strokeStyle = '#f2f2f2'; ctx.lineWidth = rw + Math.max(1.5, 2 / mpp); path(a); ctx.stroke();      // edge lines
    ctx.strokeStyle = '#2a2d31'; ctx.lineWidth = rw; path(a); ctx.stroke();
    if (z >= 13.5) { ctx.save(); ctx.setLineDash([Math.max(6, 36 / mpp), Math.max(5, 24 / mpp)]); ctx.strokeStyle = '#ffffff'; ctx.lineWidth = Math.max(1, 0.9 / mpp); path(a); ctx.stroke(); ctx.restore(); }
    if (z >= 14 && a.length >= 4) {                  // threshold "piano keys" and painted numbers at both ends
      const ids = (ref || '').split('/');
      for (const [i0, i1, id] of [[0, 2, ids[0]], [a.length - 2, a.length - 4, ids[1]]]) {
        const p0 = P(a, i0), p1 = P(a, i1), dx = p1[0] - p0[0], dy = p1[1] - p0[1], L = Math.hypot(dx, dy) || 1;
        const ux = dx / L, uy = dy / L, nx = -uy, ny = ux, keys = 8, half = rw / 2 * 0.86;
        ctx.strokeStyle = '#ffffff'; ctx.lineWidth = Math.max(1, (half * 2 / keys) * 0.55);
        for (let k = 0; k < keys; k++) {
          const off = -half + (k + 0.5) * (half * 2 / keys);
          const sx = p0[0] + ux * 6 / mpp + nx * off, sy = p0[1] + uy * 6 / mpp + ny * off;
          ctx.beginPath(); ctx.moveTo(sx, sy); ctx.lineTo(sx + ux * 30 / mpp, sy + uy * 30 / mpp); ctx.stroke();
        }
        if (id && z >= 14.5) {
          const tx = p0[0] + ux * 70 / mpp, ty = p0[1] + uy * 70 / mpp;
          ctx.save(); ctx.translate(tx, ty); ctx.rotate(Math.atan2(uy, ux) - Math.PI / 2);
          ctx.fillStyle = '#ffffff'; ctx.font = `bold ${Math.max(10, Math.min(rw * 0.7, 40))}px Arial`; ctx.textAlign = 'center'; ctx.fillText(id, 0, 0);
          ctx.restore();
        }
      }
    }
  }
  if (z >= 14.5) {                                   // red/yellow hold-short markings
    ctx.strokeStyle = '#e8c330'; ctx.lineWidth = Math.max(2, 1 / mpp);
    for (const a of G.holdl || []) { path(a); ctx.stroke(); }
    ctx.fillStyle = '#e0202e'; for (const a of G.hold) { const [x, y] = P(a, 0); ctx.beginPath(); ctx.arc(x, y, 3, 0, Math.PI * 2); ctx.fill(); }
  }
  if (z >= 15) { ctx.strokeStyle = '#f0d040'; ctx.lineWidth = 1.2; for (const [a] of G.stand || []) { path(a); ctx.stroke(); } }
  // buildings with shadow
  ctx.fillStyle = 'rgba(0,0,0,0.35)';
  for (const a of G.bldg) { ctx.save(); ctx.translate(Math.max(2, 4 / mpp), Math.max(2, 4 / mpp)); path(a, true); ctx.fill(); ctx.restore(); }
  ctx.fillStyle = '#d5d9dc'; ctx.strokeStyle = '#7b8187'; ctx.lineWidth = 1;
  for (const a of G.bldg) { path(a, true); ctx.fill(); ctx.stroke(); }
  ctx.lineCap = 'round'; ctx.strokeStyle = '#7f868c'; ctx.lineWidth = Math.max(2, 4 / mpp);
  for (const a of G.bridge || []) { path(a); ctx.stroke(); }
  ctx.lineCap = 'butt';
  if (z >= 16) {
    ctx.font = 'bold 10px Consolas, monospace';
    for (const [a, ref] of G.stand || []) { if (!ref) continue; const [x, y] = P(a, 0); label(ref, x - 8, y - 4, '#1a1a1a', 'rgba(240,208,64,0.95)'); }
  }
  if (z >= 14) {
    ctx.font = 'bold 13px "Segoe UI", Arial, sans-serif'; ctx.fillStyle = 'rgba(30,34,38,0.7)';
    for (const [la, lo, n] of G.apronName) {
      const p = map.latLngToContainerPoint([la, lo]); const w = ctx.measureText(n).width;
      if (p.x > -w && p.y > 0 && p.x < s.x + w && p.y < s.y) ctx.fillText(n, p.x - w / 2, p.y + 4);
    }
  }
}
// real wingspans (m) for drawing aircraft at true size when zoomed in on an airport
function wingspan(type) {
  const t = (type || '').toUpperCase();
  if (/^A38/.test(t)) return 80; if (/^B74/.test(t)) return 68; if (/^(B77|A35|A34)/.test(t)) return 64; if (/^(B78|A33)/.test(t)) return 60;
  if (/^(B76|A30|A31[0-9]?$)/.test(t)) return 47; if (/^(A3[12]|A2|B73|B3[89]|A22|BCS)/.test(t)) return 35; if (/^(E19|E2|E17|E75)/.test(t)) return 28;
  if (/^(CRJ|E14|E13)/.test(t)) return 23; if (/^(AT|DH8|Q4)/.test(t)) return 27; if (/^(C1|C2|PA|SR2|DA)/.test(t)) return 11;
  return 35;
}
// ---------- taxi route builder: click taxiway names in order ----------
let RB = null;
function drawRoutePreview() {
  if (!RB || !RB.prev || !RB.prev.path) return;
  const pts = RB.prev.path;
  ctx.save(); ctx.lineCap = 'round'; ctx.lineJoin = 'round';
  ctx.beginPath(); pts.forEach((q, i) => { const p = pt(q[0], q[1]); if (i) ctx.lineTo(p.x, p.y); else ctx.moveTo(p.x, p.y); });
  ctx.strokeStyle = 'rgba(255,255,255,0.7)'; ctx.lineWidth = 9; ctx.stroke();
  ctx.strokeStyle = '#b02fc4'; ctx.lineWidth = 6; ctx.stroke();
  for (const x of RB.prev.xpts || []) {           // red hold-short bars where the route crosses a runway
    const i = pts.findIndex((q) => q[0] === x[0] && q[1] === x[1]);
    const a = pt(...(pts[Math.max(0, i - 1)])), b = pt(...(pts[Math.min(pts.length - 1, i + 1)])), c = pt(x[0], x[1]);
    const dx = b.x - a.x, dy = b.y - a.y, L2 = Math.hypot(dx, dy) || 1, ux = dx / L2, uy = dy / L2;
    const hx = c.x - ux * 16, hy = c.y - uy * 16;
    ctx.strokeStyle = '#e0202e'; ctx.lineWidth = 7; ctx.lineCap = 'round';
    ctx.beginPath(); ctx.moveTo(hx - uy * 12, hy + ux * 12); ctx.lineTo(hx + uy * 12, hy - ux * 12); ctx.stroke();
  }
  ctx.restore();
}
function startRoute(id) {
  const t = tgts.get(id);
  if (!t || !t.gnd) { addLocal('Select a plane that is with Ground (at the gate, pushed back, holding or clear of the runway).', 'err'); return; }
  RB = { id, cs: t.callsign, via: [], to: '', prev: null, kind: null };
  if (!views.some((v) => v.kind === 'ground')) openTask('ground');
  $('rbar').hidden = false;
  previewRoute();
}
async function previewRoute() {
  if (!RB) return;
  try {
    const r = await fetch('/api/ground/preview', { method: 'POST', body: JSON.stringify({ id: RB.id, via: RB.via, to: RB.to }) });
    const d = await r.json();
    if (!RB) return;
    RB.prev = d.ok ? d : null; RB.kind = d.kind || RB.kind; RB.msg = d.message || '';
  } catch (e) { RB.msg = 'Server not reachable'; }
  paintRouteBar();
}
function paintRouteBar() {
  if (!RB) { $('rbar').hidden = true; return; }
  const d = RB.prev, dest = d ? (d.dest[0] === 'rwy' ? 'runway ' + d.dest[1] : 'gate ' + d.dest[1]) : '?';
  $('rbTitle').textContent = `TAXI ROUTE \u00b7 ${RB.cs}`;
  $('rbVia').innerHTML = (RB.via.length ? RB.via.map((n) => `<span class="rbt">${esc(n)}</span>`).join('<i>\u2192</i>') : '<span class="muted">click taxiway names on the map in order</span>') +
    `<i>\u2192</i><span class="rbd">${esc(dest)}</span>`;
  $('rbInfo').textContent = (RB.msg ? RB.msg + ' \u00b7 ' : '') + (d && d.cross.length ? `hold short of runway ${d.cross[0]} (Cross later)` : '') +
    (RB.kind === 'dep' ? ' \u00b7 click a runway name to change the runway' : RB.kind === 'arr' ? ' \u00b7 click a gate to change the gate' : '');
}
function routeClick(v, c) {
  const hit = (arr) => arr && arr.find(([x, y, w, h]) => c.x >= x - 4 && c.x <= x + w + 4 && c.y >= y - 4 && c.y <= y + h + 4);
  const tw = hit(v.twyHits);
  if (tw) { if (RB.via[RB.via.length - 1] !== tw[4]) RB.via.push(tw[4]); previewRoute(); return true; }
  const rw = hit(v.rwyHits);
  if (rw && RB.kind !== 'arr') { RB.to = rw[4]; previewRoute(); return true; }
  const gt = hit(v.gateHits);
  if (gt && RB.kind === 'arr') { RB.to = gt[4]; previewRoute(); return true; }
  return false;
}
function closeRoute() { RB = null; paintRouteBar(); }
$('rbUndo').onclick = () => { if (RB) { RB.via.pop(); previewRoute(); } };
$('rbClear').onclick = () => { if (RB) { RB.via = []; RB.to = ''; previewRoute(); } };
$('rbCancel').onclick = closeRoute;
$('rbSend').onclick = () => {
  if (!RB) return;
  sendCmd(['TAXI', RB.to, RB.via.length ? 'VIA ' + RB.via.join(' ') : ''].filter(Boolean).join(' '), RB.id);
  closeRoute();
};
function drawGroundInfo(s, z, light) {
  V.twyHits = []; V.gateHits = [];
  if (!groundData || !groundData.gates || z < 13) return;
  if (V.kind !== 'ground' && V.kind !== 'tower') return;
  ctx.font = 'bold 11px Consolas, monospace';
  const placed = [];
  for (const [la, lo, name] of groundData.labels) {
    const p = pt(la, lo);
    if (p.x < 0 || p.y < 0 || p.x > s.x || p.y > s.y) continue;
    if (placed.some(([x, y, n]) => (n === name && Math.hypot(x - p.x, y - p.y) < 260) || Math.hypot(x - p.x, y - p.y) < 34)) continue;
    placed.push([p.x, p.y, name]);
    const w = ctx.measureText(name).width + 6, on = RB && RB.via.includes(name);
    ctx.fillStyle = on ? '#b02fc4' : '#f2c230'; ctx.fillRect(p.x - w / 2, p.y - 7, w, 14);
    ctx.strokeStyle = 'rgba(0,0,0,0.55)'; ctx.lineWidth = 1; ctx.strokeRect(p.x - w / 2, p.y - 7, w, 14);
    ctx.fillStyle = on ? '#fff' : '#111'; ctx.fillText(name, p.x - w / 2 + 3, p.y + 4);
    V.twyHits.push([p.x - w / 2, p.y - 7, w, 14, name]);
  }
  if (z < 14 && V.kind !== 'ground') return;
  ctx.font = '10px Consolas, monospace';
  for (const [name, la, lo] of groundData.gates) {
    const p = pt(la, lo);
    if (p.x < 0 || p.y < 0 || p.x > s.x || p.y > s.y) continue;
    const pc = light ? '#6a2bb0' : '#c9a0ff', sel = RB && RB.to === name;
    const quiet = (S.gndTheme === 'real' || S.gndTheme === 'sat') && !sel && !(RB && RB.kind === 'arr') && z < 16.3;
    V.gateHits.push([p.x - 6, p.y - 14, ctx.measureText(name).width + 14, 20, name]);
    if (quiet) continue;
    ctx.strokeStyle = sel ? '#b02fc4' : pc; ctx.lineWidth = sel ? 2.5 : 1.2; ctx.strokeRect(p.x - 3, p.y - 3, 6, 6);
    if (z >= (S.gndTheme === 'real' ? 16.3 : 15.2) || sel || (RB && RB.kind === 'arr')) label(name, p.x + 5, p.y - 4, sel ? '#b02fc4' : light ? '#4b1d80' : '#d9c2ff', light ? 'rgba(255,255,255,0.9)' : 'rgba(0,0,0,0.85)');
  }
}
function drawTaxiPath(t, p, sel) {
  const path = t.gnd && t.gnd.path;
  if (!path || !path.length) return;
  ctx.save(); ctx.lineJoin = 'round'; ctx.lineCap = 'round';
  ctx.beginPath(); const a = pt(p[0], p[1]); ctx.moveTo(a.x, a.y);
  for (const q of path) { const b = pt(q[0], q[1]); ctx.lineTo(b.x, b.y); }
  ctx.strokeStyle = 'rgba(0,0,0,0.6)'; ctx.lineWidth = sel ? 6 : 4; ctx.stroke();
  ctx.strokeStyle = sel ? '#ffe14d' : 'rgba(255,225,77,0.45)'; ctx.lineWidth = sel ? 3 : 2; ctx.setLineDash(sel ? [] : [6, 5]); ctx.stroke();
  ctx.restore();
}
const GND_TAG = { clrd: (g) => 'CLEARANCE? ' + g.gate, gate: (g) => 'AT GATE ' + g.gate, push: () => 'PUSHBACK', pushed: () => 'READY TO TAXI', taxi: (g) => 'TAXI ' + (g.rwy && !g.gate ? g.rwy : ''),
  held: () => 'HOLDING POSITION', hs_rwy: (g) => 'HOLD SHORT ' + g.xr, roll: () => 'ROLLOUT', exit: () => 'CLEAR OF RWY' };
function drawRoute(t, p) {
  if (!route || route.id !== t.id || (!route.dep && !route.arr)) return;
  const here = [p[0], near(p[1], CL)];
  const col = COLORS[t.source] || '#fff';
  const line = (to, dashed, alpha) => {
    const pts = gcPoints(here, [to.lat, to.lon]);
    ctx.save(); ctx.lineCap = 'round'; ctx.beginPath();
    pts.forEach((q, i) => { const c = map.latLngToContainerPoint(q); if (i) ctx.lineTo(c.x, c.y); else ctx.moveTo(c.x, c.y); });
    ctx.setLineDash(dashed ? [9, 7] : []);
    ctx.strokeStyle = 'rgba(0,0,0,0.6)'; ctx.lineWidth = 4.5; ctx.stroke();
    ctx.strokeStyle = col; ctx.globalAlpha = alpha; ctx.lineWidth = 2.2; ctx.stroke();
    ctx.restore();
    return map.latLngToContainerPoint(pts[pts.length - 1]);
  };
  const ends = [];
  if (route.dep) ends.push([line(route.dep, false, 0.75), route.dep.code]);
  if (route.arr) ends.push([line(route.arr, true, 1), route.arr.code]);
  ctx.font = 'bold 12px Consolas, monospace';
  for (const [e, code] of ends) {
    ctx.fillStyle = '#05080b'; ctx.beginPath(); ctx.arc(e.x, e.y, 6, 0, Math.PI * 2); ctx.fill();
    ctx.strokeStyle = col; ctx.lineWidth = 2; ctx.stroke(); ctx.lineWidth = 1;
    label(code, e.x + 9, e.y - 8, col, 'rgba(0,0,0,0.9)');
  }
}
function drawPlane(x, y, trk, scale, col, alpha) {
  const c = Math.cos(trk * D2R) * scale * DPR, sn = Math.sin(trk * D2R) * scale * DPR;
  ctx.setTransform(c, sn, -sn, c, (x + OX) * DPR, (y + OY) * DPR);
  ctx.globalAlpha = alpha;
  ctx.lineWidth = 2.2 / scale; ctx.lineJoin = 'round'; ctx.strokeStyle = 'rgba(0,0,0,0.9)'; ctx.stroke(PLANE);
  ctx.fillStyle = col; ctx.fill(PLANE);
  baseTf(); ctx.globalAlpha = 1;
}
function drawView(v, now) {
  if (!v.radar) return 0;
  const cv = v.radar._container;
  if (!cv || !v.radar._bounds || v.radar._scaled || v.map._animatingZoom) return 0;
  V = v; map = v.map; ctx = v.ctx;
  const chart = chartKind(v.kind) && (S.gndTheme === 'chart' || S.gndTheme === 'real');      // light labels (not on the dark satellite photo)
  const s = map.getSize(), z = map.getZoom(), light = chart || (LIGHT_STYLES.has(S.style) && !chartKind(v.kind));
  DPR = window.devicePixelRatio || 1;
  const off = map.containerPointToLayerPoint([0, 0]).subtract(v.radar._bounds.min);
  OX = off.x; OY = off.y; CL = map.getCenter().lng;
  ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.clearRect(0, 0, cv.width, cv.height); baseTf();
  drawGrid(s, z, light);
  drawDiagram(s, z);
  drawZone(light);
  drawNavaids(s, z, light);
  drawAirports(s, z, light);
  drawGroundInfo(s, z, light);
  if (v.kind === 'ground' || v.kind === 'tower') drawRoutePreview();
  v.screenPts = [];
  const labels = S.labels && (z >= 6 || tgts.size < 250);
  const scale = z < 3 ? 0.6 : z < 5 ? 0.75 : z < 7 ? 0.9 : z < 11 ? 1.05 : 1.25;
  let drawn = 0, selPos = null;
  ctx.font = 'bold 11px Consolas, "Courier New", monospace';
  for (const t of tgts.values()) {
    const p = posNow(t, now);
    if (t.id === selected) selPos = [t, p];
    const sp = pt(p[0], p[1]);
    if (sp.x < -60 || sp.y < -60 || sp.x > s.x + 60 || sp.y > s.y + 60) continue;
    drawn++;
    v.screenPts.push([sp.x, sp.y, t.id]);
    const col = COLORS[t.source] || '#fff';
    if (t.gnd && (v.kind === 'ground' || v.kind === 'tower' || t.id === selected)) drawTaxiPath(t, p, t.id === selected);
    if (S.trails && !t.gnd) {
      ctx.globalAlpha = 0.75;
      for (const h of t.trail) { const hp = pt(h[0], h[1]); ctx.fillStyle = 'rgba(0,0,0,0.8)'; ctx.fillRect(hp.x - 2, hp.y - 2, 4, 4); ctx.fillStyle = col; ctx.fillRect(hp.x - 1, hp.y - 1, 2, 2); }
      ctx.globalAlpha = 1;
    }
    if (S.vectors && !t.ground && t.gs > 40 && z >= 5) {
      const vv = deadReckon({ ...t, lat: p[0], lon: p[1] }, 60), vp = pt(vv[0], vv[1]);
      ctx.lineWidth = 3; ctx.strokeStyle = 'rgba(0,0,0,0.6)'; ctx.beginPath(); ctx.moveTo(sp.x, sp.y); ctx.lineTo(vp.x, vp.y); ctx.stroke();
      ctx.lineWidth = 1.3; ctx.strokeStyle = col; ctx.stroke();
    }
    let psc = t.ground ? scale * 0.7 : scale;
    if (chartKind(v.kind) && z >= 14) {               // true size on the airport map
      const mppx = 40075016.7 * Math.cos(p[0] * D2R) / (256 * 2 ** z);
      psc = Math.max(psc, wingspan(t.type) / mppx / 19);
    }
    drawPlane(sp.x, sp.y, t.track || 0, psc, col, t.ground && !chartKind(v.kind) ? 0.7 : 1);
    if (t.id === selected) { ctx.lineWidth = 2; ctx.strokeStyle = col; ctx.beginPath(); ctx.arc(sp.x, sp.y, 16, 0, Math.PI * 2); ctx.stroke(); }
    if ((labels && (!t.ground || t.twr || t.ctl || t.gnd || v.kind === 'tower' || v.kind === 'ground')) || t.id === selected) {
      const trend = t.vs > 300 ? '↑' : t.vs < -300 ? '↓' : ' ';
      label(t.callsign, sp.x + 14, sp.y - 10, col, 'rgba(0,0,0,0.9)');
      label(`${fl(t.alt)}${trend} ${String(Math.round(t.gs / 10)).padStart(2, '0')} ${t.type || ''}`, sp.x + 14, sp.y + 3, col, 'rgba(0,0,0,0.9)');
      if (t.twr) label(t.twr === 'lined' ? 'LINED UP' : 'HOLDING SHORT', sp.x + 14, sp.y + 16, '#ffffff', 'rgba(0,0,0,0.9)');
      if (t.gnd) { const g = t.gnd, tag = (GND_TAG[g.st] || (() => 'GROUND'))(g); label(t.gnd.st === 'taxi' ? (t.phase || '').replace('GND: ', '').toUpperCase() : tag, sp.x + 14, sp.y + 16, '#ffe14d', 'rgba(0,0,0,0.9)'); }
      if (t.ctr === 'in' && !t.ctl && v.kind === 'center') label('CTR', sp.x + 14, sp.y + 16, '#ffd84d', 'rgba(0,0,0,0.9)');
      if (t.ctl) {
        const c = t.ctl;
        const lat = c.mode === 'takeoff' ? 'T/O' : c.mode === 'app_auto' ? `VEC${c.rwy || ''}` : c.mode === 'ils' ? `ILS${c.rwy}${c.cap ? '*' : ''}` : c.mode === 'direct' ? 'DCT' : c.mode === 'ron' ? 'RON' : `H${String(Math.round(c.hdg || 0) || 360).padStart(3, '0')}`;
        label(`${lat} →${fl(c.alt)}${c.spd ? ' S' + Math.round(c.spd) : ''}${c.clr ? ' CLR' : ''}${t.shadow ? ' SHD' : ''}`, sp.x + 14, sp.y + 16, '#ffffff', 'rgba(0,0,0,0.9)');
      }
    }
    const cf = conflicts.get(t.id);
    if (cf && S.stca) {
      const pulse = 14 + 4 * Math.sin(now * 8);
      ctx.lineWidth = 2.5; ctx.strokeStyle = cf === 'now' ? '#ff3b4e' : '#ff9f1a';
      ctx.beginPath(); ctx.arc(sp.x, sp.y, pulse, 0, Math.PI * 2); ctx.stroke();
      label(cf === 'now' ? 'CONFLICT' : 'PREDICT', sp.x - 26, sp.y + 30, cf === 'now' ? '#ff3b4e' : '#ff9f1a', 'rgba(0,0,0,0.9)');
    }
  }
  if (selPos && !(selPos[0].ground && (v.kind === 'ground' || v.kind === 'tower'))) drawRoute(selPos[0], selPos[1]);
  return drawn;
}
function draw() {
  const now = performance.now() / 1000;
  frames++;
  if (now * 1000 - fpsT > 1000) { fps = frames; frames = 0; fpsT = now * 1000; }
  updateClock();
  let drawn = 0, apNote = '';
  for (const v of views) {
    try { const n = drawView(v, now); if (v === focused || views.length === 1) drawn = n; if (v.apNote) apNote = v.apNote; }
    catch (e) { console.error('draw error', e); lastErr = 'Display error: ' + e.message; }
  }
  const sb = $('statusbar'), err = versionWarn || lastErr;
  sb.hidden = !S.statusbar && !err;
  sb.className = err ? 'err' : '';
  if (err) sb.textContent = err;
  else sb.innerHTML = `<b>${drawn.toLocaleString()}</b> aircraft in the active window · <b>FPS ${fps}</b>` +
    (aiInfo && aiInfo.paused ? ' · AI PAUSED' : '') + (apNote ? ' · ' + esc(apNote) : '');
  requestAnimationFrame(draw);
}

// ---------- clock (General settings) ----------
let zoneTxt = '';
function updateClockZone() { zoneTxt = tzName(); }
function updateClock() {
  const d = new Date();
  $('lt').textContent = fmtTime(d, S.showSeconds);
  $('ltz').textContent = zoneTxt;
  $('utcPart').hidden = !S.showUtc;
  if (S.showUtc) $('utc').textContent = d.toISOString().slice(11, S.showSeconds ? 19 : 16);
}
updateClockZone();
setInterval(updateClockZone, 60000);

// ---------- selection and info card ----------
function onMapClick(v, e) {
  const c = e.containerPoint;
  if (RB && routeClick(v, c)) return;
  let best = null, bd = 256;
  for (const [x, y, id] of v.screenPts) { const d = (x - c.x) ** 2 + (y - c.y) ** 2; if (d < bd) { bd = d; best = id; } }
  selAirport = null;
  if (!best) {
    let ba = null, bda = 100;
    for (const [x, y, a] of v.apPts) { const d = (x - c.x) ** 2 + (y - c.y) ** 2; if (d < bda) { bda = d; ba = a; } }
    selAirport = ba;
  }
  selectTarget(best);
}
async function selectTarget(id) {
  selected = id; route = null;
  renderCard();
  if (!id) return;
  try {
    const d = await (await fetch('/api/route?id=' + encodeURIComponent(id))).json();
    if (selected === id) { route = { ...d, id }; renderCard(); }
  } catch (e) { console.error(e); }
}
function closeCard() { selAirport = null; selectTarget(null); }
function renderCard() {
  const card = $('card'), info = $('cardInfo');
  if (selAirport && !selected) {
    const a = selAirport;
    card.hidden = false; $('cardCtl').innerHTML = ''; $('cardCtl').dataset.id = '';
    info.innerHTML = `<div class="ct"><span class="cs">${esc(a.code)}</span><button class="x" onclick="closeCard()">×</button></div>` +
      `<div>${esc(a.name)}</div><table><tr><td>City</td><td>${esc(a.city || '-')}</td></tr>` +
      `<tr><td>Type</td><td>${esc((a.type || '-').replace('_', ' '))}</td></tr><tr><td>Elevation</td><td>${esc(a.elev || '-')} ft</td></tr>` +
      `<tr><td>Position</td><td>${a.lat.toFixed(4)}, ${a.lon.toFixed(4)}</td></tr></table>` +
      `<div class="crow"><button class="primary" onclick="setHomeAirport('${esc(a.code)}')">Use for task windows</button></div>`;
    return;
  }
  const t = selected && tgts.get(selected);
  if (!t) { card.hidden = true; $('cardCtl').innerHTML = ''; $('cardCtl').dataset.id = ''; $('cTarget').textContent = '--'; return; }
  $('cTarget').textContent = t.source === 'ai' ? t.callsign : '--';
  const p = posNow(t, performance.now() / 1000);
  let routeHtml = '<div class="route muted">Looking up route...</div>';
  if (route && route.id === t.id) {
    if (route.dep || route.arr) {
      const left = route.arr ? Math.round(distNm(p, [route.arr.lat, route.arr.lon])) : null;
      routeHtml = `<div class="route"><table>` +
        `<tr><td>From</td><td>${route.dep ? esc(route.dep.code + ' ' + route.dep.name) : '?'}</td></tr>` +
        `<tr><td>To</td><td>${route.arr ? esc(route.arr.code + ' ' + route.arr.name) : '?'}</td></tr>` +
        (left != null ? `<tr><td>Remaining</td><td>${left.toLocaleString()} NM` + (t.gs > 50 && !t.eta ? ` (~${Math.round(left / t.gs * 60)} min)` : '') + `</td></tr>` : '') +
        (t.off_block ? `<tr><td>Departed gate</td><td>${hhmm(t.off_block)}</td></tr>` : '') + (t.eta ? `<tr><td>Lands at</td><td>${hhmm(t.eta)}</td></tr>` : '') +
        `</table><div class="muted">Route source: ${esc(route.via || '-')}</div></div>`;
    } else routeHtml = `<div class="route muted">Route unknown${route.error ? ' (' + esc(route.error) + ')' : ''}</div>`;
  }
  const status = t.phase || (t.ground ? 'On ground' : t.vs > 300 ? 'Climbing' : t.vs < -300 ? 'Descending' : 'Level');
  const rows = [['Status', status + (t.frozen ? ' (paused)' : '')], ['Type', t.type || '-'], ['Altitude', `${t.alt.toLocaleString()} ft`],
    ['Ground speed', `${t.gs} kt`], ['Track', `${Math.round(t.track)}°`], ['Vertical speed', `${t.vs} fpm`],
    ['Squawk', t.squawk || '-'], ['Position', `${p[0].toFixed(4)}, ${wrap(p[1]).toFixed(4)}`], ['Data age', `${Math.round(performance.now() / 1000 - t.rx)} s`]];
  if (t.reg) rows.splice(2, 0, ['Registration', t.reg]);
  card.hidden = false;
  const mode = t.gnd ? 'gnd-' + (t.gnd.st === 'clrd' ? 'clrd' : 'go') : 'air';
  if ($('cardCtl').dataset.id !== t.id || $('cardCtl').dataset.mode !== mode) renderControls(t);
  info.innerHTML = `<div class="ct"><span class="cs">${esc(t.callsign)}</span>${t.shadow ? '<span class="tag">SHADOW</span>' : ''}${t.ctl ? '<span class="tag">UNDER YOUR CONTROL</span>' : ''}` +
    `<span><span class="chip" style="background:${COLORS[t.source]}">${NAMES[t.source]}</span> <button class="x" onclick="closeCard()">×</button></span></div>` +
    `<table>${rows.map(([k, v]) => `<tr><td>${k}</td><td>${esc(v)}</td></tr>`).join('')}</table>` + routeHtml;
}
function renderControls(t) {
  const box = $('cardCtl');
  box.dataset.id = t.id; box.dataset.mode = t.gnd ? 'gnd-' + (t.gnd.st === 'clrd' ? 'clrd' : 'go') : 'air';
  if (t.gnd) {
    const dep = !t.gnd.gate || t.gnd.st === 'gate' || t.gnd.st === 'push' || t.gnd.st === 'pushed' || (t.dep === (home && home.icao) && t.gnd.st !== 'exit' && t.gnd.st !== 'roll');
    box.innerHTML = `<div class="ctl"><h4>Ground</h4>
      ${t.gnd.st === 'clrd' ? `<h4>Clearance delivery</h4><div class="crow"><label>INITIAL<input id="dAlt" placeholder="5000" maxlength="5" style="width:58px"></label>
        <label>CRUISE<input id="dFl" placeholder="FL350" maxlength="5" style="width:58px"></label><label>SQUAWK<input id="dSq" placeholder="auto" maxlength="4" style="width:50px"></label>
        <button class="primary" id="dClr">Give clearance</button></div>` : ''}
      <div class="crow"><button class="primary" id="gRoute" title="Click taxiway names on the Ground map to build the route">Build taxi route...</button></div>
      <div class="crow"><button id="gPb">Push back</button><button id="gHold">Hold position</button><button id="gCont">Continue</button><button id="gCross">Cross runway</button></div>
      <div class="crow"><label>${dep ? 'RUNWAY' : 'GATE'}<input id="gTo" placeholder="${dep ? '10L' : 'C17'}" maxlength="5" style="width:56px"></label>
        <label>VIA<input id="gVia" placeholder="B C1 A" style="width:90px"></label><button class="primary" id="gTaxi">Taxi</button></div>
      <div class="muted">Leave runway/gate empty for the active runway or the assigned gate. VIA is optional.</div></div>`;
    const send = (text) => sendCmd(text, t.id);
    $('gPb').onclick = () => send('PB');
    $('gRoute').onclick = () => startRoute(t.id);
    if ($('dClr')) $('dClr').onclick = () => {
      const a = $('dAlt').value.trim(), f = $('dFl').value.trim().toUpperCase().replace(/^FL/, ''), q = $('dSq').value.trim();
      send(['CLR', a ? 'A' + a : '', f ? 'FL' + f : '', q ? 'SQ' + q : ''].filter(Boolean).join(' '));
    };
    $('gHold').onclick = () => send('HOLD');
    $('gCont').onclick = () => send('CONT');
    $('gCross').onclick = () => send('CROSS');
    $('gTaxi').onclick = () => { const to = $('gTo').value.trim().toUpperCase(), via = $('gVia').value.trim().toUpperCase(); send(['TAXI', to, via ? 'VIA ' + via : ''].filter(Boolean).join(' ')); };
    ['gTo', 'gVia'].forEach((id) => $(id).addEventListener('keydown', (e) => { if (e.key === 'Enter') $('gTaxi').click(); }));
    return;
  }
  if (t.source !== 'ai') {
    box.innerHTML = `<div class="ctl"><h4>Control</h4><div class="muted">${NAMES[t.source]} flights are flown by real pilots and can't be controlled. Shadow mode makes an AI copy at the same position, heading, altitude and speed that you can control.</div>` +
      `<div class="crow"><button class="primary" id="shadowBtn">Shadow this flight</button></div></div>`;
    $('shadowBtn').onclick = async () => {
      const r = await fetch('/api/shadow', { method: 'POST', body: JSON.stringify({ id: t.id }) });
      const d = await r.json();
      if (!r.ok) { addLocal(d.error, 'err'); return; }
      setTimeout(() => selectTarget(d.id), 1200);
    };
    return;
  }
  box.innerHTML = `<div class="ctl"><h4>ATC instructions</h4>
    <div class="crow"><label>HDG<input id="cHdg" placeholder="270" maxlength="3"></label>
      <label>TURN<select id="cDir"><option value="">auto</option><option value="L">left</option><option value="R">right</option></select></label>
      <label>ALT<input id="cAlt" placeholder="050/FL350" maxlength="5" style="width:72px"></label>
      <label>SPD<input id="cSpd" placeholder="210" maxlength="3" style="width:46px"></label></div>
    <div class="crow"><button class="primary" id="cSend">Send</button><button id="cSR">Normal speed</button><button id="cRon">Own nav</button></div>
    <div class="crow"><label>DIRECT TO<input id="cDct" placeholder="KORD / OBK" maxlength="5" style="width:74px" title="Airport or radio beacon (VOR/NDB)"></label><button id="cDctB">Direct</button><button id="cHo" title="Center: hand off to Approach (arrivals) or the next sector">Hand off</button></div>
    <div class="crow"><label>APPROACH<select id="cIls"><option value="">runway...</option></select></label><button id="cIlsB">Clear ILS</button></div>
    <h4 style="margin-top:8px">Tower</h4>
    <div class="crow"><button id="cLuaw">Line up</button><button id="cCto" title="Uses HDG/ALT above if filled, otherwise own navigation">Takeoff</button><button id="cCl">Clear to land</button><button id="cGa">Go around</button></div></div>`;
  const send = (text) => sendCmd(text, t.id);
  const vecParts = () => {
    const parts = [], h = $('cHdg').value.trim(), a = $('cAlt').value.trim().toUpperCase(), s = $('cSpd').value.trim();
    if (h) parts.push(($('cDir').value || 'H') + h.padStart(3, '0'));
    if (a) parts.push(a.startsWith('FL') ? a : 'A' + a);
    return [parts, s];
  };
  $('cSend').onclick = () => { const [parts, s] = vecParts(); if (s) parts.push('S' + s); if (parts.length) send(parts.join(' ')); };
  ['cHdg', 'cAlt', 'cSpd'].forEach((id) => $(id).addEventListener('keydown', (e) => { if (e.key === 'Enter') $('cSend').click(); }));
  $('cSR').onclick = () => send('SR');
  $('cRon').onclick = () => send('RON');
  $('cDctB').onclick = () => $('cDct').value.trim() && send('D ' + $('cDct').value.trim());
  $('cHo').onclick = () => send('HO');
  $('cIlsB').onclick = () => $('cIls').value && send('ILS ' + $('cIls').value);
  $('cLuaw').onclick = () => send('LUAW');
  $('cCto').onclick = () => { const [parts] = vecParts(); send(['CTO', ...parts].join(' ')); };
  $('cCl').onclick = () => send('CL');
  $('cGa').onclick = () => send('GA');
  fetch('/api/runways?id=' + encodeURIComponent(t.id)).then((r) => r.json()).then((d) => {
    const sel = $('cIls');
    if (!sel || box.dataset.id !== t.id) return;
    sel.innerHTML = '<option value="">runway...</option>' + d.runways.map((r) => `<option value="${esc(r.id)}">${esc(r.ap)} ${esc(r.id)} (${String(r.crs).padStart(3, '0')}°)</option>`).join('');
  }).catch(() => {});
}

// ---------- comms log + command line ----------
let commsSeq = 0;
function addLine(m) {
  if (m.kind === 'ai' && !S.showAiComms) return;
  const log = $('clog'), div = document.createElement('div');
  div.className = 'cm ' + (m.kind || 'atc');
  div.innerHTML = `<span class="tm">${fmtTime(new Date(m.t * 1000), true)}</span><span class="who">${esc(m.who)}</span>${esc(m.text)}`;
  if (m.id) div.querySelector('.who').onclick = () => selectTarget(m.id);
  const stick = log.scrollTop + log.clientHeight >= log.scrollHeight - 20;
  log.appendChild(div);
  while (log.children.length > 200) log.removeChild(log.firstChild);
  if (stick) log.scrollTop = log.scrollHeight;
}
const addLocal = (text, kind) => addLine({ t: Date.now() / 1000, who: 'NIGHTLOOP', text, kind });
let commsLoaded = false;
// AI controller radio is heard for the positions whose window is open (Clearance Delivery belongs to the Ground window)
const POS_WIN = { delivery: 'ground', ground: 'ground', tower: 'tower', approach: 'approach', center: 'center' };
const listening_to = (pos) => !!pos && S.hearAiWindows !== false && views.some((v) => v.kind === POS_WIN[pos]);
async function pollComms() {
  try {
    const d = await (await fetch('/api/comms?since=' + commsSeq)).json();
    d.items.forEach((m) => { addLine(m); if (commsLoaded && m.who !== 'NIGHTLOOP' && (m.kind === 'pilot' || m.kind === 'unable' || (m.kind === 'ai' && (S.speakAi || listening_to(m.pos))))) speak(m.who, m.text); });
    commsSeq = d.seq; commsLoaded = true;
  } catch (e) { /* ignore */ }
}
setInterval(pollComms, 1000);
async function sendCmd(text, id) {
  try {
    const r = await fetch('/api/command', { method: 'POST', body: JSON.stringify({ text, id }) });
    const d = await r.json();
    if (!d.ok) addLocal(d.message, 'err');
  } catch (e) { addLocal('Server not reachable', 'err'); }
  pollComms(); setTimeout(pollNow, 300);
}
$('cmdGo').onclick = () => { const v = $('cmd').value.trim(); if (v) { sendCmd(v, selected); $('cmd').value = ''; } };
$('cmd').addEventListener('keydown', (e) => { if (e.key === 'Enter') $('cmdGo').click(); });
$('cHelp').onclick = () => { $('chelp').hidden = !$('chelp').hidden; };
$('cMin').onclick = () => $('comms').classList.toggle('min');
addLocal('Welcome to NIGHTLOOP. Choose the positions you work at the top (GND TWR APP CTR) - AI controllers handle the rest. Open a task from the left bar; split the screen with the 1-4 window buttons. Hold the ` key (or the mic button) to talk. Press ? for commands.', 'event');

// ---------- conflict alerts (STCA) with ignore + warning window ----------
const conflicts = new Map(), ignored = new Set();
let conflictPairs = [];
const pairKey = (a, b) => (a < b ? a + '|' + b : b + '|' + a);
function scanConflicts() {
  conflicts.clear(); conflictPairs = [];
  if (!S.stca) { $('stca').hidden = true; $('cwin').hidden = true; return; }
  const now = performance.now() / 1000, cells = new Map(), list = [];
  const onScreen = new Set(); views.forEach((v) => v.screenPts.forEach((p) => onScreen.add(p[2])));
  for (const t of tgts.values()) {
    if (t.source !== 'ai' || t.ground || t.alt < 1000 || !onScreen.has(t.id)) continue;   // only planes you can control
    const p = posNow(t, now), base = { ...t, lat: p[0], lon: p[1] };
    const o = { t, p, f: deadReckon(base, 60), h: deadReckon(base, 30), alt: t.alt, alt2: t.alt + (t.vs || 0), alth: t.alt + (t.vs || 0) / 2 };
    const k = Math.floor(p[0] * 10) + ':' + Math.floor(p[1] * 10);
    if (!cells.has(k)) cells.set(k, []);
    cells.get(k).push(o); list.push(o);
  }
  const active = new Set();
  for (const a of list) {
    const ci = Math.floor(a.p[0] * 10), cj = Math.floor(a.p[1] * 10);
    for (let di = -2; di <= 2; di++) for (let dj = -2; dj <= 2; dj++) {
      for (const b of cells.get((ci + di) + ':' + (cj + dj)) || []) {
        if (b.t.id <= a.t.id) continue;
        const dn = distNm(a.p, b.p), dv = Math.abs(a.alt - b.alt);
        const ca = a.t.ctl, cb = b.t.ctl;
        if (ca && cb && ca.cap && cb.cap && ca.rwy === cb.rwy) continue;      // both established on the same ILS: normal sequence
        let kind = null;
        if (dn < 3 && dv < 1000) kind = 'now';
        else if ((distNm(a.h, b.h) < 3 && Math.abs(a.alth - b.alth) < 1000) || (distNm(a.f, b.f) < 3 && Math.abs(a.alt2 - b.alt2) < 1000)) kind = 'pred';
        if (!kind) continue;
        const key = pairKey(a.t.id, b.t.id); active.add(key);
        const ign = ignored.has(key);
        const dn30 = distNm(a.h, b.h), dn60 = distNm(a.f, b.f);
        const when = kind === 'now' ? 0 : dn30 < 3 ? 30 : 60;
        const closing = dn30 < dn - 0.05;
        conflictPairs.push({ key, a: a.t, b: b.t, kind, dn, dv, ign, when, closing, fix: conflictFix(a.t, b.t) });
        if (!ign) for (const x of [a.t.id, b.t.id]) if (conflicts.get(x) !== 'now') conflicts.set(x, kind);
      }
    }
  }
  for (const k of [...ignored]) if (!active.has(k)) ignored.delete(k);    // pair separated: forget the "ignore"
  const live = conflictPairs.filter((c) => !c.ign);
  const nNow = live.filter((c) => c.kind === 'now').length, nPred = live.length - nNow;
  $('stca').hidden = !live.length;
  if (live.length) {
    const c = live.find((x) => x.kind === 'now') || live[0];
    $('stcaText').textContent = `\u26a0 ${conflictText(c)}` + (live.length > 1 ? `  (+${live.length - 1} more)` : '');
    stcaTop = c;
    $('stcaFix').hidden = !c.fix;
    if (c.fix) { $('stcaFix').textContent = 'Fix it'; $('stcaFix').title = 'Send: ' + c.fix.label; }
  } else stcaTop = null;
  if (!$('cwin').hidden) renderConflictWin();
}
let stcaTop = null;
const lvl = (ft) => (ft >= 18000 ? 'FL' + String(Math.round(ft / 100)).padStart(3, '0') : Math.round(ft).toLocaleString() + ' ft');
function conflictText(c) {
  const what = c.kind === 'now' ? 'LOSS OF SEPARATION' : `CONFLICT in about ${c.when} s`;
  return `${what}: ${c.a.callsign} (${lvl(c.a.alt)}) and ${c.b.callsign} (${lvl(c.b.alt)}) are ${c.dn.toFixed(1)} NM apart with only ${Math.round(c.dv)} ft between them` +
    (c.closing ? ', and closing' : '') + ` - need 3 NM or 1,000 ft.` + (c.fix ? ` Suggested: ${c.fix.label}.` : '');
}
function conflictFix(a, b) {          // move the higher plane up 1,000 ft (or the lower one down), a turn also works
  const hi = a.alt >= b.alt ? a : b, lo = hi === a ? b : a;
  const up = Math.round((Math.max(hi.alt, lo.alt) + 1000) / 1000) * 1000;
  const dn = Math.round((Math.min(hi.alt, lo.alt) - 1000) / 1000) * 1000;
  const fmt = (ft) => (ft >= 18000 ? 'FL' + String(ft / 100).padStart(3, '0') : 'A' + String(ft / 100).padStart(3, '0'));
  if (up <= 41000 && !(hi.ctl && (hi.ctl.mode === 'ils' || hi.ctl.mode === 'takeoff'))) return { id: hi.id, cmd: fmt(up), label: `climb ${hi.callsign} to ${lvl(up)}` };
  if (dn >= 3000 && !(lo.ctl && lo.ctl.mode === 'ils')) return { id: lo.id, cmd: fmt(dn), label: `descend ${lo.callsign} to ${lvl(dn)}` };
  const t = (hi.track + 30) % 360;
  return { id: hi.id, cmd: 'R' + String(Math.round(t) || 360).padStart(3, '0'), label: `turn ${hi.callsign} right heading ${String(Math.round(t)).padStart(3, '0')}` };
}
function renderConflictWin() {
  $('cwList').innerHTML = conflictPairs.length ? conflictPairs.sort((x, y) => (x.kind > y.kind ? 1 : -1) || x.dn - y.dn).map((c) =>
    `<div class="cwr${c.ign ? ' ign' : ''}"><i class="k" style="background:${c.kind === 'now' ? '#ff3b4e' : '#ff9f1a'}"></i>` +
    `<span><b>${esc(c.a.callsign)}</b> / <b>${esc(c.b.callsign)}</b> <small>${c.kind === 'now' ? 'loss of separation' : 'in about ' + c.when + ' s'}</small>` +
    `<span class="adv">${esc(c.dn.toFixed(1))} NM, ${Math.round(c.dv)} ft apart${c.closing ? ', closing' : ''}${c.fix ? ' \u2192 ' + esc(c.fix.label) : ''}</span></span>` +
    (c.fix ? `<button data-fix="${esc(c.key)}">Fix</button>` : '') + `<button data-show="${esc(c.a.id)}">Show</button>` +
    `<button data-ign="${esc(c.key)}">${c.ign ? 'Ignored' : 'Ignore'}</button></div>`).join('')
    : '<div class="cwr"><span class="muted">No conflicts right now</span></div>';
}
$('cwList').addEventListener('click', (e) => {
  const s = e.target.dataset.show, g = e.target.dataset.ign, fx = e.target.dataset.fix;
  if (fx) { const c = conflictPairs.find((x) => x.key === fx); if (c && c.fix) sendCmd(c.fix.cmd, c.fix.id); }
  if (s) { const t = tgts.get(s), v = mapForGoto(); if (t && v.kind === 'world') v.map.panTo([t.lat, t.lon]); selectTarget(s); }
  if (g) { if (ignored.has(g)) ignored.delete(g); else ignored.add(g); scanConflicts(); renderConflictWin(); }
});
$('stcaList').onclick = () => { $('cwin').hidden = !$('cwin').hidden; renderConflictWin(); };
$('stcaFix').onclick = () => { if (stcaTop && stcaTop.fix) sendCmd(stcaTop.fix.cmd, stcaTop.fix.id); };
$('stcaIgnore').onclick = () => { conflictPairs.forEach((c) => ignored.add(c.key)); scanConflicts(); };
$('cwIgnoreAll').onclick = () => { conflictPairs.forEach((c) => ignored.add(c.key)); scanConflicts(); renderConflictWin(); };
$('cwClose').onclick = () => { $('cwin').hidden = true; };
setInterval(scanConflicts, 500);

// ---------- Tower window side panel ----------
function wireTowerSide(el) {
  const side = el.querySelector('.tside');
  side.querySelector('.tmin').onclick = () => side.classList.toggle('min');
  side.querySelector('.twrOn').addEventListener('change', (e) => post('/api/tower', { enabled: e.target.checked }).then(pollTower));
  side.querySelector('.twrArr').addEventListener('change', (e) => post('/api/tower', { arr_rwy: e.target.value }).then(pollTower));
  side.querySelector('.twrDep').addEventListener('change', (e) => post('/api/tower', { dep_rwy: e.target.value }).then(pollTower));
  side.addEventListener('click', (e) => {
    const b = e.target.closest('[data-id]');
    if (!b) return;
    if (b.dataset.c) sendCmd(b.dataset.c, b.dataset.id).then(pollTower);
    else selectTarget(b.dataset.id);
  });
}
const mmss = (s) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
function renderTower(d) {
  document.querySelectorAll('.pane .tside').forEach((side) => {
    if (!side.querySelector('.twrOn')) return;
    side.querySelector('.twrOn').checked = d.enabled;
    for (const [cls, cur] of [['.twrArr', d.arr_rwy], ['.twrDep', d.dep_rwy]]) {
      const sel = side.querySelector(cls);
      const opts = d.runways.map((r) => `<option value="${esc(r.id)}">${esc(r.id)} (${String(r.crs).padStart(3, '0')}°, ${r.len.toLocaleString()} ft)</option>`).join('');
      if (sel.dataset.opts !== opts) { sel.innerHTML = opts; sel.dataset.opts = opts; }
      if (cur && document.activeElement !== sel) sel.value = cur;
      sel.disabled = !d.enabled;
    }
    side.querySelector('.twrDeps').innerHTML = d.holding.length ? d.holding.map((h) => `<div class="trow"><b data-id="${h.id}">${esc(h.cs)}</b><small>${esc(h.type)} → ${esc(h.arr)} · ${h.state === 'takeoff roll' ? 'takeoff roll' : (h.state === 'lined' ? 'lined up ' : 'holding ') + esc(h.rwy) + ' ' + mmss(h.waited)}</small>` +
      (h.state === 'holding' ? `<button data-id="${h.id}" data-c="LUAW">Line up</button>` : '') + (h.state !== 'takeoff roll' ? `<button data-id="${h.id}" data-c="CTO">Takeoff</button>` : '') + '</div>').join('')
      : '<div class="trow empty">No departures waiting</div>';
    side.querySelector('.twrArrs').innerHTML = d.finals.length ? d.finals.map((f) => `<div class="trow"><b data-id="${f.id}">${esc(f.cs)}</b><small>${esc(f.type)} · ${f.dist} NM · ${f.est ? 'established' : 'vectors'} ${esc(f.rwy)}</small>` +
      (f.clr ? `<span class="ok">CLEARED</span><button data-id="${f.id}" data-c="GA">Go around</button>` : `<button data-id="${f.id}" data-c="CL">Clear to land</button>`) + '</div>').join('')
      : '<div class="trow empty">No arrivals on approach</div>';
    side.querySelector('.twrOcc').textContent = d.occupied.length ? d.occupied.map((o) => `Runway ${o.rwy} occupied by ${o.cs}`).join(' · ') : (d.enabled ? 'All runways clear' : 'Tower is off');
  });
}
let towerTimer = null;
async function pollTower() {
  clearTimeout(towerTimer);
  try { towerData = await (await fetch('/api/tower')).json(); renderTower(towerData); } catch (e) { /* ignore */ }
  towerTimer = setTimeout(pollTower, 2000);
}
function renderApproach() {
  const sides = document.querySelectorAll('.pane .appList');
  if (!sides.length || !home) return;
  const now = performance.now() / 1000, rows = [];
  for (const t of tgts.values()) {
    if (t.source !== 'ai' || t.arr !== home.icao || t.ground) continue;
    const p = posNow(t, now), d = distNm(p, [home.lat, home.lon]);
    if (d < 80) rows.push([d, t]);
  }
  rows.sort((a, b) => a[0] - b[0]);
  const html = rows.length ? rows.slice(0, 15).map(([d, t]) => `<div class="trow"><b data-id="${t.id}">${esc(t.callsign)}</b><small>${esc(t.type)} · ${d.toFixed(0)} NM · ${fl(t.alt)} · ${esc(t.phase || '')}</small></div>`).join('')
    : '<div class="trow empty">No AI arrivals within 80 NM</div>';
  sides.forEach((s) => {
    s.innerHTML = html;
    if (!s.dataset.wired) { s.dataset.wired = 1; s.addEventListener('click', (e) => { const b = e.target.closest('[data-id]'); if (b) selectTarget(b.dataset.id); }); }
    const min = s.parentElement.querySelector('.tmin');
    if (min && !min.dataset.wired) { min.dataset.wired = 1; min.onclick = () => s.parentElement.classList.toggle('min'); }
  });
}

// ---------- Center window side panel ----------
let centerData = null, centerTimer = null;
function wireCenterSide(el) {
  const side = el.querySelector('.tside');
  side.querySelector('.tmin').onclick = () => side.classList.toggle('min');
  side.querySelector('.ctrOn').addEventListener('change', (e) => post('/api/center', { enabled: e.target.checked }).then(pollCenter));
  side.querySelector('.ctrRad').addEventListener('change', (e) => post('/api/center', { radius: +e.target.value }).then(pollCenter));
  side.addEventListener('click', (e) => {
    const b = e.target.closest('[data-id]');
    if (!b) return;
    if (b.dataset.c) sendCmd(b.dataset.c, b.dataset.id).then(pollCenter);
    else selectTarget(b.dataset.id);
  });
}
function renderCenter(d) {
  if (d.radius && KINDS.center.zone !== d.radius) {
    KINDS.center.zone = d.radius;
    views.forEach((v) => { if (v.kind === 'center' && v.ap) v.focusAirport(v.ap); });
  }
  const row = (x) => {
    const st = x.st === 'handed' ? '<span class="ok">HANDED OFF</span>' : `<button data-id="${x.id}" data-c="HO">Hand off</button>`;
    const trend = x.vs > 300 ? '\u2191' : x.vs < -300 ? '\u2193' : '';
    return `<div class="trow"><b data-id="${x.id}">${esc(x.cs)}</b><small>${esc(x.type)} \u00b7 ${fl(x.alt)}${trend} \u00b7 ${x.dist} NM \u00b7 ${esc(x.dep)}\u2192${esc(x.arr)}${x.ctl ? ' \u00b7 yours' : ''}</small>${st}</div>`;
  };
  document.querySelectorAll('.pane .tside').forEach((side) => {
    if (!side.querySelector('.ctrOn')) return;
    side.querySelector('.ctrOn').checked = d.enabled;
    const sel = side.querySelector('.ctrRad');
    if (document.activeElement !== sel) sel.value = String(Math.round(d.radius));
    for (const [cls, k, empty] of [['.ctrArr', 'arr', 'No arrivals in the sector'], ['.ctrDep', 'dep', 'No departures in the sector'], ['.ctrOvf', 'ovf', 'No overflights in the sector']])
      side.querySelector(cls).innerHTML = d[k].length ? d[k].slice(0, 20).map(row).join('') : `<div class="trow empty">${d.enabled ? empty : 'Center is off'}</div>`;
  });
}
async function pollCenter() {
  clearTimeout(centerTimer);
  try { centerData = await (await fetch('/api/center')).json(); renderCenter(centerData); } catch (e) { /* ignore */ }
  centerTimer = setTimeout(pollCenter, 2000);
}

// ---------- Ground window side panel ----------
let groundData = null, groundTimer = null;
function wireGroundSide(el) {
  const side = el.querySelector('.tside');
  side.querySelector('.tmin').onclick = () => side.classList.toggle('min');
  side.querySelector('.gndOn').addEventListener('change', (e) => post('/api/ground', { enabled: e.target.checked }).then(pollGround));
  side.querySelector('.gndReload').onclick = () => post('/api/ground', { reload: true }).then(pollGround);
  side.addEventListener('click', (e) => {
    const b = e.target.closest('[data-id]');
    if (!b) return;
    if (b.dataset.c === 'ROUTE') { selectTarget(b.dataset.id); startRoute(b.dataset.id); }
    else if (b.dataset.c) sendCmd(b.dataset.c, b.dataset.id).then(pollGround);
    else selectTarget(b.dataset.id);
  });
}
const GND_TXT = { clrd: (f) => `at gate ${f.gate}, wants IFR clearance to ${f.to}`, gate: (f) => `at gate ${f.gate} \u2192 ${f.to}`, pushed: (f) => `pushed back, gate ${f.gate}`, push: () => 'pushing back',
  hs_rwy: (f) => `holding short runway ${f.xr.replace('|', ' and ')}`, exit: (f) => `clear of runway ${f.rwy}, gate ${f.gate}`, held: () => 'holding position',
  taxi: (f) => (f.kind === 'dep' ? `taxi to ${f.rwy}` : `taxi to gate ${f.gate}`) + (f.via.length ? ' via ' + f.via.join(' ') : ''), roll: (f) => `landing roll ${f.rwy}` };
const GND_BTN = { clrd: [['CLR', 'Clearance']], gate: [['PB', 'Push back'], ['ROUTE', 'Route'], ['TAXI', 'Taxi']], pushed: [['ROUTE', 'Route'], ['TAXI', 'Taxi']], hs_rwy: [['CROSS', 'Cross']],
  exit: [['ROUTE', 'Route'], ['TAXI', 'Taxi to gate']], held: [['ROUTE', 'Route'], ['CONT', 'Continue']], taxi: [['HOLD', 'Hold']] };
function renderGround(d) {
  document.querySelectorAll('.pane .tside').forEach((side) => {
    if (!side.querySelector('.gndOn')) return;
    side.querySelector('.gndOn').checked = d.enabled;
    side.querySelector('.gndNote').textContent = !d.enabled ? 'Ground is off' : !d.tower_on ? 'Turn Tower control on first (Tower window)' :
      d.state === 'loading' ? 'Loading taxiways and gates...' : (d.src === 'simple' ? 'Simple layout: ' : '') + (d.note || '');
    const row = (f) => `<div class="trow"><b data-id="${f.id}">${esc(f.cs)}</b><small>${esc(f.type)} \u00b7 ${esc((GND_TXT[f.st] || (() => f.st))(f))}</small>` +
      (GND_BTN[f.st] || []).map(([c, n]) => `<button data-id="${f.id}" data-c="${c}">${n}</button>`).join('') + '</div>';
    const wait = d.flights.filter((f) => ['clrd', 'gate', 'pushed', 'hs_rwy', 'exit', 'held'].includes(f.st));
    const move = d.flights.filter((f) => !['clrd', 'gate', 'pushed', 'hs_rwy', 'exit', 'held'].includes(f.st));
    side.querySelector('.gndWait').innerHTML = wait.length ? wait.map(row).join('') : '<div class="trow empty">Nobody waiting</div>';
    side.querySelector('.gndMove').innerHTML = move.length ? move.map(row).join('') : '<div class="trow empty">Nothing moving</div>';
  });
}
async function pollGround() {
  clearTimeout(groundTimer);
  try { groundData = await (await fetch('/api/ground')).json(); renderGround(groundData); if (groundData.state === 'ready' && views.some((v) => chartKind(v.kind))) loadDiagram(false); } catch (e) { /* ignore */ }
  groundTimer = setTimeout(pollGround, 2000);
}

// ---------- voice: push-to-talk commands and spoken pilot replies ----------
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
let rec = null, listening = false, voiceStarted = false;
function micState(on, txt) {
  listening = on;
  $('micBtn').classList.toggle('on', on);
  if (txt !== undefined) $('cmd').placeholder = txt;
}
// offline speech engine (built into the Windows app / NIGHTLOOP OS)
let VL = null, vlTimer = null, vlLast = '', vlSince = 0;
const IN_APP = params.get('app') === '1' || params.get('pi') === '1' || !!window.pywebview;
async function loadVoiceLocal() {
  try { VL = await (await fetch('/api/voice/local')).json(); } catch (e) { VL = null; }
  const el = $('voiceEngineInfo');
  if (!el) return;
  const eng = useLocal() ? 'Offline speech engine (no internet needed)' : SR ? 'Browser speech service (needs internet)' : 'none available';
  el.textContent = `In use: ${eng}.` + (VL ? (VL.available ? ` Microphone: ${VL.mics[0] || '-'}` : VL.engine ? (VL.model ? ` Offline engine: ${VL.message || 'no microphone found'}` : ' Offline voice not installed yet.') : '') : '') +
    (VL && VL.installing ? ' Downloading...' : '');
  $('voiceInstall').hidden = !(VL && VL.engine && !VL.model);
  paintMics();
  $('micRow').hidden = !(VL && VL.engine);
  if (VL && VL.installing) setTimeout(loadVoiceLocal, 3000);
}
function paintMics() {
  const sel = $('voiceMic');
  if (!sel || !VL) return;
  const opts = '<option value="default">Windows default microphone</option>' + (VL.mics || []).map((m) => `<option value="${m.i}">${esc(m.name)}${m.default ? ' (default)' : ''}</option>`).join('');
  if (sel.dataset.o !== opts) { sel.innerHTML = opts; sel.dataset.o = opts; }
  sel.value = VL.mic == null ? 'default' : String(VL.mic);
}
function useLocal() {
  if (!VL || !VL.available) return false;
  return S.voiceEngine === 'offline' || (S.voiceEngine === 'auto' && (IN_APP || !SR));
}
async function startLocal() {
  try {
    const r = await fetch('/api/voice/local/start', { method: 'POST', body: '{}' }); const d = await r.json();
    if (!d.ok) { addLocal(d.message, 'err'); return; }
  } catch (e) { addLocal('Server not reachable', 'err'); return; }
  micState(true, 'Listening... ' + (S.pttMode === 'toggle' ? 'press again to send' : 'release to send')); $('cmd').value = '';
  vlLast = ''; vlSince = performance.now();
  const t0 = performance.now();
  clearInterval(vlTimer);
  vlTimer = setInterval(async () => {
    try {
      const d = await (await fetch('/api/voice/local/partial')).json();
      $('cmd').value = d.text;
      $('micBtn').style.background = `linear-gradient(to top, #39ff6a ${d.level}%, #e0245e ${d.level}%)`;
      if (d.text !== vlLast) { vlLast = d.text; vlSince = performance.now(); }
      if (S.pttMode === 'toggle' && ((vlLast && performance.now() - vlSince > 1600) || performance.now() - t0 > 12000)) stopLocal();
    } catch (e) { /* ignore */ }
  }, 300);
}
async function stopLocal() {
  clearInterval(vlTimer);
  $('micBtn').style.background = '';
  if (!listening) return;
  micState(false, 'Type a command, e.g. H270 A050 S210');
  $('cmd').value = '';
  try {
    const r = await fetch('/api/voice/local/stop', { method: 'POST', body: JSON.stringify({ id: selected }) }); const d = await r.json();
    if (!d.ok && d.message && !/not understood|No AI flight/.test(d.message)) addLocal(d.message === 'Nothing heard' ? 'Voice: nothing heard' : d.message, 'err');
  } catch (e) { /* ignore */ }
  pollComms(); setTimeout(pollNow, 300);
}
function startListen() {
  if (listening) return;
  if (!S.voiceIn) { addLocal('Voice commands are off (Settings > Voice).', 'err'); return; }
  if (useLocal()) { startLocal(); return; }
  if (!SR) { addLocal(VL && VL.engine && !VL.model ? 'Install the offline voice first: Settings > Voice.' : 'Voice commands need the offline voice (Settings > Voice) or Chrome / Edge.', 'err'); return; }
  rec = new SR();
  rec.lang = 'en-US'; rec.interimResults = true; rec.maxAlternatives = 4; rec.continuous = S.pttMode !== 'toggle';
  let finals = [];
  rec.onresult = (e) => {
    let interim = '';
    for (let i = e.resultIndex; i < e.results.length; i++) {
      const r = e.results[i];
      if (r.isFinal) finals.push([...r].map((a) => a.transcript.trim()));
      else interim += r[0].transcript;
    }
    $('cmd').value = (finals.map((f) => f[0]).join(' ') + ' ' + interim).trim();
  };
  rec.onerror = (e) => { if (e.error !== 'no-speech' && e.error !== 'aborted') addLocal('Voice: ' + (e.error === 'not-allowed' ? 'microphone blocked - allow it in the browser address bar' : e.error), 'err'); };
  rec.onend = () => {
    micState(false, 'Type a command, e.g. H270 A050 S210');
    const typed = $('cmd').value.trim();
    if (!finals.length && !typed) return;
    const alts = finals.length ? [finals.map((f) => f[0]).join(' ')].concat(finals.length === 1 ? finals[0].slice(1) : []) : [typed];
    if (typed && !alts.includes(typed)) alts.unshift(typed);
    $('cmd').value = '';
    sendVoice(alts);
  };
  try { rec.start(); micState(true, 'Listening... release to send'); $('cmd').value = ''; } catch (e) { addLocal('Voice could not start: ' + e.message, 'err'); }
}
function stopListen() { if (listening && useLocal()) { stopLocal(); return; } if (rec && listening) { try { rec.stop(); } catch (e) { /* ignore */ } } }
async function sendVoice(alts) {
  try {
    const r = await fetch('/api/voice', { method: 'POST', body: JSON.stringify({ alts, id: selected }) });
    const d = await r.json();
    if (!d.ok && d.message && !/not understood|No AI flight/.test(d.message)) addLocal(d.message, 'err');
  } catch (e) { addLocal('Server not reachable', 'err'); }
  pollComms(); setTimeout(pollNow, 300);
}
$('micBtn').addEventListener('mousedown', (e) => { e.preventDefault(); startListen(); });
$('micBtn').addEventListener('mouseup', stopListen);
$('micBtn').addEventListener('mouseleave', stopListen);
$('micBtn').addEventListener('touchstart', (e) => { e.preventDefault(); startListen(); });
$('micBtn').addEventListener('touchend', stopListen);
const typing = () => { const a = document.activeElement; return a && (a.tagName === 'INPUT' || a.tagName === 'SELECT' || a.tagName === 'TEXTAREA'); };
let pttLearning = false;
function pttDown(code, name) {
  if (pttLearning) {
    pttLearning = false; S.pttKey = code; S.pttName = name; saveS();
    $('pttName').textContent = name; $('pttLearn').textContent = 'Change...';
    addLocal(`Push-to-talk button set to ${name}`, 'event');
    return true;
  }
  if (code !== S.pttKey) return false;
  if (S.pttMode === 'toggle') { if (listening) stopListen(); else startListen(); } else startListen();
  return true;
}
function pttUp(code) { if (code === S.pttKey && listening && S.pttMode !== 'toggle') { stopListen(); return true; } return false; }
document.addEventListener('keydown', (e) => {
  if (e.repeat || (typing() && !pttLearning)) return;
  if (pttDown(e.code, e.key === ' ' ? 'Space' : e.key.length === 1 ? e.key.toUpperCase() : e.code)) e.preventDefault();
});
document.addEventListener('keyup', (e) => { if (pttUp(e.code)) e.preventDefault(); });
document.addEventListener('mousedown', (e) => {            // mouse side buttons (3, 4) or middle button
  if (e.button < 1 || (e.button === 1 && !pttLearning && S.pttKey !== 'Mouse1')) return;
  if (pttDown('Mouse' + e.button, ['', 'Middle mouse button', '', 'Mouse back button', 'Mouse forward button'][e.button] || 'Mouse ' + e.button)) e.preventDefault();
}, true);
document.addEventListener('mouseup', (e) => { if (e.button >= 1 && (pttUp('Mouse' + e.button) || S.pttKey === 'Mouse' + e.button)) e.preventDefault(); }, true);
$('pttLearn').onclick = () => { pttLearning = true; $('pttLearn').textContent = 'Press the button now...'; };
$('pttName').textContent = S.pttName || S.pttKey;
// joystick / gamepad buttons
const padPrev = {};
function pollPads() {
  if (S.pttGamepad || pttLearning) {
    for (const gp of (navigator.getGamepads ? navigator.getGamepads() : [])) {
      if (!gp) continue;
      gp.buttons.forEach((b, i) => {
        const k = `Pad${gp.index}:${i}`, was = padPrev[k];
        padPrev[k] = b.pressed;
        if (b.pressed && !was) pttDown(k, `Joystick button ${i + 1}`);
        if (!b.pressed && was) pttUp(k);
      });
    }
  }
  requestAnimationFrame(pollPads);
}
requestAnimationFrame(pollPads);
// headset / media button (play-pause): works as press-to-start, press-again-to-send
let mediaEl = null;
function armHeadset() {
  if (!S.pttHeadset || mediaEl || !('mediaSession' in navigator)) return;
  const sr = 8000, n = sr, buf = new ArrayBuffer(44 + n * 2), dv = new DataView(buf);
  const w = (o, t) => [...t].forEach((ch, i) => dv.setUint8(o + i, ch.charCodeAt(0)));
  w(0, 'RIFF'); dv.setUint32(4, 36 + n * 2, true); w(8, 'WAVEfmt '); dv.setUint32(16, 16, true); dv.setUint16(20, 1, true); dv.setUint16(22, 1, true);
  dv.setUint32(24, sr, true); dv.setUint32(28, sr * 2, true); dv.setUint16(32, 2, true); dv.setUint16(34, 16, true); w(36, 'data'); dv.setUint32(40, n * 2, true);
  mediaEl = new Audio(URL.createObjectURL(new Blob([buf], { type: 'audio/wav' })));
  mediaEl.loop = true; mediaEl.play().catch(() => { mediaEl = null; });
  navigator.mediaSession.metadata = new MediaMetadata({ title: 'NIGHTLOOP push to talk', artist: 'NIGHTLOOP' });
  const tog = () => { if (listening) stopListen(); else startListen(); mediaEl && mediaEl.play().catch(() => {}); };
  ['play', 'pause', 'nexttrack', 'previoustrack', 'stop'].forEach((a) => { try { navigator.mediaSession.setActionHandler(a, tog); } catch (e) { /* ignore */ } });
}
document.addEventListener('click', armHeadset);
document.addEventListener('keydown', armHeadset);
// spoken pilot replies
let voices = [];
const loadVoices = () => { try { voices = speechSynthesis.getVoices().filter((v) => /^en/i.test(v.lang)); } catch (e) { voices = []; } };
if (window.speechSynthesis) { loadVoices(); speechSynthesis.onvoiceschanged = loadVoices; }
// radio-style reading of numbers: FL350 -> "flight level three five zero", 270 -> "two seven zero", 10L -> "one zero left"
const DIG = ['zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'niner'];
const digits = (n) => n.split('').map((d) => DIG[+d]).join(' ');
function speakable(text) {
  return text
    .replace(/\b(1[1-3]\d)\.(\d{1,3})\b/g, (m, a, b) => digits(a) + ' decimal ' + digits(b.replace(/0+$/, '') || '0'))
    .replace(/\bFL\s?(\d{2,3})\b/g, (m, n) => 'flight level ' + digits(n))
    .replace(/\b(\d{1,2})([LRC])\b/g, (m, n, s) => digits(n) + ' ' + { L: 'left', R: 'right', C: 'center' }[s])
    .replace(/\b(\d{1,3}),?000\b/g, (m, n) => digits(n) + ' thousand')
    .replace(/\b(\d{1,2}),?(\d)00\b/g, (m, a, b) => digits(a) + ' thousand ' + DIG[+b] + ' hundred')
    .replace(/\b\d{2,4}\b/g, (m) => digits(m))
    .replace(/\bILS\b/g, 'I L S').replace(/\bNM\b/g, 'miles').replace(/\bkt\b/g, 'knots');
}
let TTSI = null, audioCtx = null, sayChain = Promise.resolve();
async function loadTts() {
  try { TTSI = await (await fetch('/api/tts/info')).json(); } catch (e) { TTSI = null; }
  const el = $('ttsInfo');
  if (el) {
    el.textContent = TTSI && TTSI.installed ? `Natural voices ready (${TTSI.voices.join(', ')}).` : TTSI && TTSI.installing ? TTSI.message : (TTSI && TTSI.message ? TTSI.message + ' ' : '') + 'Natural voices are not installed - the computer voice is used.';
    $('ttsInstall').hidden = !!(TTSI && (TTSI.installed || TTSI.installing));
  }
  if (TTSI && TTSI.installing) setTimeout(loadTts, 3000);
}
function bestSystemVoice(h) {
  const nice = voices.filter((v) => /natural|neural|online|google uk english male|google us english/i.test(v.name));
  const pool = nice.length ? nice : voices;
  return pool.length ? pool[h % pool.length] : null;
}
async function playRadio(buf) {
  audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
  const audio = await audioCtx.decodeAudioData(buf);
  const src = audioCtx.createBufferSource(); src.buffer = audio;
  let node = src;
  if (S.radioFx) {                                   // VHF radio: narrow band, light crunch, squelch hiss at start/end
    const hp = audioCtx.createBiquadFilter(); hp.type = 'highpass'; hp.frequency.value = 320;
    const lp = audioCtx.createBiquadFilter(); lp.type = 'lowpass'; lp.frequency.value = 3200;
    const ws = audioCtx.createWaveShaper(); const c = new Float32Array(256);
    for (let i = 0; i < 256; i++) { const x = i / 128 - 1; c[i] = Math.tanh(2.2 * x) / Math.tanh(2.2); }
    ws.curve = c;
    node.connect(hp); hp.connect(lp); lp.connect(ws); node = ws;
    const n = audioCtx.createBuffer(1, audioCtx.sampleRate * 0.12, audioCtx.sampleRate), d = n.getChannelData(0);
    for (let i = 0; i < d.length; i++) d[i] = (Math.random() * 2 - 1) * 0.06;
    for (const at of [0, audio.duration + 0.1]) {
      const ns = audioCtx.createBufferSource(); ns.buffer = n; const g = audioCtx.createGain(); g.gain.value = 0.7;
      ns.connect(g); g.connect(audioCtx.destination); ns.start(audioCtx.currentTime + at);
    }
  }
  const g = audioCtx.createGain(); g.gain.value = 1.0; node.connect(g); g.connect(audioCtx.destination);
  src.start(audioCtx.currentTime + 0.1);
  return new Promise((res) => { src.onended = () => setTimeout(res, 250); });
}
function speak(who, text) {
  if (!S.voiceOut) return;
  let h = 0; for (const ch of who) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  const say = speakable(text);
  const rate = parseFloat(S.voiceRate) || 1.1;
  sayChain = sayChain.then(async () => {
    if (S.voiceNatural && TTSI && TTSI.installed) {
      try {
        const r = await fetch(`/api/tts?n=${h}&speed=${rate}&text=` + encodeURIComponent(say));
        if (r.ok) { await playRadio(await r.arrayBuffer()); return; }
      } catch (e) { /* fall back */ }
    }
    if (!window.speechSynthesis) return;
    await new Promise((res) => {
      const u = new SpeechSynthesisUtterance(say);
      const v = bestSystemVoice(h); if (v) u.voice = v;
      u.pitch = 0.9 + (h % 5) * 0.04; u.rate = rate; u.onend = res; u.onerror = res;
      speechSynthesis.speak(u);
    });
  }).catch(() => {});
}
$('ttsInstall').onclick = async () => { try { await post('/api/tts/install', {}); } catch (e) { /* ignore */ } setTimeout(loadTts, 800); };
loadTts();
$('voiceTest').onclick = () => speak('TEST123', 'Tower, Speedbird 123, holding short runway 10 left, ready for departure');
$('voiceMic').onchange = async () => { await post('/api/voice/mic', { index: $('voiceMic').value }); loadVoiceLocal(); };
$('micTest').onclick = async () => {
  const msg = $('micTestMsg'), bar = $('micLevel');
  try { const r = await post('/api/voice/local/start', {}); if (!r.ok) { msg.textContent = r.message; return; } } catch (e) { msg.textContent = 'Not available'; return; }
  msg.textContent = 'Speak now for 4 seconds...';
  const iv = setInterval(async () => { try { const d = await (await fetch('/api/voice/local/partial')).json(); bar.style.width = d.level + '%'; } catch (e) { /* ignore */ } }, 150);
  setTimeout(async () => {
    clearInterval(iv); bar.style.width = '0%';
    try {
      const d = await post('/api/voice/local/stop', { test: true });
      msg.textContent = d.peak < 300 ? `No sound from '${d.mic}'. Choose another microphone above, or turn on "Let desktop apps access your microphone" in Windows Settings > Privacy & security > Microphone.`
        : `Microphone '${d.mic}' works (level ${Math.min(100, Math.round(d.peak / 120))}%).` + (d.text ? ` Heard: "${d.text}"` : ' No words recognised - try a full command.');
    } catch (e) { msg.textContent = 'Test failed'; }
  }, 4000);
};
$('voiceInstall').onclick = async () => { try { await post('/api/voice/local/install', {}); } catch (e) { /* ignore */ } setTimeout(loadVoiceLocal, 800); };
loadVoiceLocal();

// ---------- positions: what you work, AI does the rest ----------
let positions = {};
function paintPositions() { document.querySelectorAll('#positions button').forEach((b) => b.classList.toggle('on', !!positions[b.dataset.p])); }
fetch('/api/positions').then((r) => r.json()).then((d) => { positions = d.positions || {}; paintPositions(); }).catch(() => {});
document.querySelectorAll('#positions button').forEach((b) => b.addEventListener('click', async () => {
  const p = b.dataset.p, on = !positions[p];
  try { const d = await post('/api/positions', { [p]: on }); positions = d.positions; paintPositions(); } catch (e) { return; }
  addLocal(`${{ delivery: 'Clearance Delivery', ground: 'Ground', tower: 'Tower', approach: 'Approach', center: 'Center' }[p]} is now ${on ? 'yours' : 'handled by an AI controller'}.`, 'event');
}));

// ---------- dialogs (Wi-Fi, power) ----------
function dialog(title, html) { $('dlgTitle').textContent = title; $('dlgBody').innerHTML = html; $('dlg').hidden = false; }
$('dlgClose').onclick = () => { $('dlg').hidden = true; };
$('dlg').addEventListener('click', (e) => { if (e.target.id === 'dlg') $('dlg').hidden = true; });
let SYS = {};
async function wifiDialog() {
  dialog('WI-FI', '<div class="muted">Searching for networks...</div>');
  let d;
  try { d = await (await fetch('/api/system/wifi')).json(); } catch (e) { dialog('WI-FI', 'Not reachable'); return; }
  const ips = (d.ips || SYS.ips || []).map((i) => `http://${i}:${SYS.port || 8080}`).join(', ');
  $('dlgBody').innerHTML = `<div class="muted">Open NIGHTLOOP from another computer: ${esc(ips || '-')}</div>` +
    (d.networks || []).map((n, i) => `<div class="wnet" data-i="${i}">${n.active ? '<b style="color:#39ff6a">\u2713</b>' : ''}<span>${esc(n.ssid)}</span><small>${n.signal}%${n.secure ? ' \u{1F512}' : ''}</small></div>`).join('') +
    (d.error ? `<div class="muted">${esc(d.error)}</div>` : '') + '<div id="wsel"></div>';
  $('dlgBody').querySelectorAll('.wnet').forEach((el) => el.onclick = () => {
    const n = d.networks[+el.dataset.i];
    $('wsel').innerHTML = `<h3>${esc(n.ssid)}</h3>` + (n.secure ? '<input id="wpw" type="password" placeholder="Wi-Fi password">' : '') + '<button class="primary big" id="wgo">Connect</button><div id="wmsg" class="muted"></div>';
    $('wgo').onclick = async () => {
      $('wmsg').textContent = 'Connecting... (if this is a different network, VNC will disconnect - reconnect on the new network)';
      try { const r = await post('/api/system/wifi', { ssid: n.ssid, password: $('wpw') ? $('wpw').value : '' }); $('wmsg').textContent = r.message; } catch (e) { $('wmsg').textContent = 'Connection changed - reconnect to the Pi on the new network.'; }
    };
  });
}
function powerDialog() {
  dialog('POWER', '<div class="muted">Always shut down here before unplugging the Raspberry Pi, so the SD card is not damaged.</div>' +
    '<button class="primary big" data-a="shutdown">Shut down</button><button class="big" data-a="reboot">Restart</button><button class="big" data-a="exit_kiosk">Exit full screen (desktop)</button><div id="pmsg" class="muted"></div>');
  $('dlgBody').querySelectorAll('button[data-a]').forEach((b) => b.onclick = async () => {
    if (b.dataset.a === 'shutdown' && !confirm('Shut down the Raspberry Pi now?')) return;
    try { const r = await post('/api/system/power', { action: b.dataset.a }); $('pmsg').textContent = r.message; } catch (e) { $('pmsg').textContent = 'Done.'; }
  });
}
$('sysWifi').onclick = wifiDialog;
$('sysPower').onclick = powerDialog;

// ---------- updates ----------
async function loadSystem() {
  try { SYS = await (await fetch('/api/system')).json(); } catch (e) { return; }
  $('sysbar').hidden = !SYS.is_pi;
  $('updPi').hidden = !SYS.is_pi;
  $('updWin').hidden = SYS.is_pi;
  if (!$('updFolder').value) $('updFolder').value = SYS.update_source || '';
  $('updVer').textContent = `This NIGHTLOOP: version ${SYS.version} on ${SYS.is_pi ? 'Raspberry Pi' : SYS.platform} (${SYS.hostname})` +
    (SYS.update_source_version ? ` \u00b7 update folder has version ${SYS.update_source_version}` : '');
  $('peerHelp').textContent = SYS.is_pi ? 'Get the newer version from your PC (NIGHTLOOP must be running on the PC).' :
    'Send this computer\'s version to your Raspberry Pi (it must be switched on and on the same Wi-Fi).';
}
$('updLocal').onclick = async () => {
  $('updLocalMsg').textContent = 'Installing...';
  try { const r = await post('/api/update/local', { folder: $('updFolder').value.trim() }); $('updLocalMsg').textContent = r.message; } catch (e) { $('updLocalMsg').textContent = 'Restarting...'; }
};
$('peerFind').onclick = async () => {
  $('peerList').innerHTML = '<div class="muted">Searching your Wi-Fi...</div>';
  let d = { peers: [] };
  try { d = await (await fetch('/api/peers')).json(); } catch (e) { /* ignore */ }
  $('peerList').innerHTML = d.peers.length ? d.peers.map((p) => `<div class="peer"><b>${esc(p.name)}</b><small>${p.is_pi ? 'Raspberry Pi' : esc(p.platform)} \u00b7 v${esc(p.version)} \u00b7 ${esc(p.url)}</small>` +
    `<button data-u="${esc(p.url)}" data-a="send">Send update</button><button data-u="${esc(p.url)}" data-a="pull">Get update</button></div>`).join('')
    : '<div class="muted">No other NIGHTLOOP found. Check that it is running and on the same Wi-Fi, or type its address below.</div>';
  $('peerList').querySelectorAll('button').forEach((b) => b.onclick = () => peerAct(b.dataset.a, b.dataset.u));
};
async function peerAct(a, url) {
  $('peerMsg').textContent = a === 'send' ? `Sending version ${APP_VERSION} to ${url}...` : `Getting the version from ${url}...`;
  try {
    const r = a === 'send' ? await post('/api/update/push', { target: url }) : await post('/api/update/pull', { source: url });
    $('peerMsg').textContent = r.message || (r.ok ? 'Done' : 'Failed');
  } catch (e) { $('peerMsg').textContent = 'Restarting with the new version...'; }
}
$('peerSend').onclick = () => $('peerAddr').value.trim() && peerAct('send', $('peerAddr').value.trim());
$('peerPull').onclick = () => $('peerAddr').value.trim() && peerAct('pull', $('peerAddr').value.trim());
$('osUpd').onclick = async () => {
  if (!confirm('Update Raspberry Pi OS now? It needs internet and can take 10-30 minutes.')) return;
  try { const r = await post('/api/system/osupdate', {}); $('osLog').textContent = r.message; } catch (e) { /* ignore */ }
  const tick = async () => { try { const d = await (await fetch('/api/system/osupdate')).json(); $('osLog').textContent = d.log; if (d.running) setTimeout(tick, 3000); } catch (e) { setTimeout(tick, 5000); } };
  tick();
};
loadSystem();
$('dataAll').onclick = async () => {
  try { await post('/api/data/refresh', {}); } catch (e) { /* ignore */ }
  const tick = async () => {
    try {
      const d = await (await fetch('/api/data/status')).json();
      $('dataLog').textContent = d.log.join('\n');
      if (d.running) setTimeout(tick, 1500); else { views.forEach((v) => v.loadAirports()); loadDiagram(true); pollGround(); }
    } catch (e) { setTimeout(tick, 3000); }
  };
  tick();
};
async function loadGh() {
  try {
    const d = await (await fetch('/api/update/github')).json();
    $('ghMsg').textContent = (d.msg || '') + (d.repo ? '' : ' (internet updates are mainly for NIGHTLOOP OS on the Raspberry Pi)');
  } catch (e) { /* ignore */ }
}
$('ghCheck').onclick = async () => {
  $('ghMsg').textContent = 'Checking the internet for a newer NIGHTLOOP...';
  try { const r = await post('/api/update/github', {}); $('ghMsg').textContent = r.message; } catch (e) { $('ghMsg').textContent = 'Restarting with the new version...'; }
};
loadGh();

// ---------- Learn (help & training guide) ----------
$('learnBtn').onclick = (e) => {
  if (e.shiftKey || views.length > 1) { const v = views.find((x) => x.kind === 'guide'); if (v) setFocus(v); else setPaneKind(focused ? focused.idx : 0, 'guide'); return; }
  $('learn').hidden = false;
};
$('learnSplit').onclick = () => { $('learn').hidden = true; if (S.layout < 2) setLayout(2); setPaneKind(views.length - 1, 'guide'); };
$('learnClose').onclick = () => { $('learn').hidden = true; };

// ---------- weather (METAR) and ATIS ----------
let WXD = null;
async function pollWx() {
  try {
    WXD = await (await fetch('/api/weather')).json();
    const w = WXD.wdir == null ? (WXD.wspd >= 3 ? 'VRB' : 'CALM') : String(WXD.wdir).padStart(3, '0') + '/' + WXD.wspd + (WXD.wgst ? 'G' + WXD.wgst : '');
    $('wxChip').hidden = !WXD.icao;
    $('wxChip').innerHTML = `<b>${esc(WXD.icao || '')}</b>${WXD.atis ? ` ATIS <b>${esc(WXD.atis)}</b>` : ''} \u00b7 ${esc(w)}${WXD.altimeter ? ' \u00b7 ' + esc(WXD.altimeter.replace('altimeter ', 'A').replace('QNH ', 'Q')) : ''}`;
    $('wxChip').title = (WXD.raw || 'No live weather (offline) - wind calm assumed') + (WXD.best_runway ? `\nWind favours runway ${WXD.best_runway}` : '') +
      (WXD.freqs ? '\n' + Object.entries(WXD.freqs).map(([k, v]) => `${k} ${v}`).join('  ') : '');
    document.querySelectorAll('.twrWx').forEach((el) => { el.textContent = `${WXD.wind}${WXD.altimeter ? ', ' + WXD.altimeter : ''}${WXD.best_runway ? ' - favours runway ' + WXD.best_runway : ''}`; });
  } catch (e) { /* ignore */ }
  setTimeout(pollWx, 60000);
}
pollWx();

// ---------- boot screen ----------
const bootT0 = performance.now();
function hideBoot() {
  const b = $('boot');
  if (!b || b.classList.contains('gone')) return;
  setTimeout(() => { b.classList.add('gone'); setTimeout(() => b.remove(), 900); }, Math.max(0, 1800 - (performance.now() - bootT0)));
}

// ---------- start ----------
fetch('/api/config').then((r) => r.json()).then((c) => { setHome(c.home); }).catch(() => {});
buildLayout();
pollNow();
pollTower();
pollCenter();
pollGround();
requestAnimationFrame(draw);
