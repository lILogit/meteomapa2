/* Meteomapa main app: Leaflet map, radar overlay, status panel, timeline wiring.
 * The monitoring point comes from the backend (/api/config → .env) as the default,
 * and can be moved interactively by dragging the ✛ or clicking the map.
 */
(async function () {
  "use strict";

  const BOUNDS = [[48.047, 11.267], [52.167, 20.770]]; // [[S,W],[N,E]]
  const STATUS_POLL = 60;   // s
  const FRAMES_POLL = 120;  // s

  // --- load config from backend (single source of truth: .env) ---
  let cfg = { center_lat: 48.9086, center_lon: 14.5948, radius_km: 12 };
  try {
    const r = await fetch("/api/config");
    if (r.ok) cfg = await r.json();
  } catch (e) { console.warn("config load failed, using defaults", e); }

  const CENTER = { lat: cfg.center_lat, lon: cfg.center_lon };
  let RADIUS_KM = cfg.radius_km;

  // --- Map ---
  const map = L.map("map", { zoomControl: true, attributionControl: true })
    .setView([CENTER.lat, CENTER.lon], 9);

  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 13, minZoom: 5,
    attribution: "© OpenStreetMap contributors",
  }).addTo(map);

  // optional borders overlay (vendored) for alignment sanity-check
  const bordersUrl = "/static/assets/borders.png";
  fetch(bordersUrl, { method: "HEAD" }).then(r => {
    if (r.ok) L.imageOverlay(bordersUrl, BOUNDS, { opacity: 0.6, zIndex: 300 }).addTo(map);
  }).catch(() => {});

  // radar data layer (filled by timeline)
  const radar = L.imageOverlay("/static/assets/blank.png", BOUNDS, { opacity: 0.75, zIndex: 400 }).addTo(map);

  // draggable center crosshair + detection radius circle
  const crosshair = L.marker([CENTER.lat, CENTER.lon], {
    icon: L.divIcon({ className: "xhair", html: "✛", iconSize: [22, 22], iconAnchor: [11, 11] }),
    draggable: true, autoPan: true,
  }).addTo(map);
  const circle = L.circle([CENTER.lat, CENTER.lon], {
    radius: RADIUS_KM * 1000, color: "#38bdf8", weight: 1.5, fillOpacity: 0.06, interactive: false,
  }).addTo(map);

  function updateCoords() {
    document.getElementById("status-coords").textContent =
      CENTER.lat.toFixed(4) + ", " + CENTER.lon.toFixed(4);
  }
  function setCenter(lat, lon) {
    CENTER.lat = lat; CENTER.lon = lon;
    crosshair.setLatLng([lat, lon]);
    circle.setLatLng([lat, lon]);
    updateCoords();
    fetchStatus();
  }
  crosshair.on("dragend", () => { const ll = crosshair.getLatLng(); setCenter(ll.lat, ll.lng); });
  map.on("click", (e) => setCenter(e.latlng.lat, e.latlng.lng));
  updateCoords();

  // --- Timeline ---
  const tl = new Timeline({
    container: document.getElementById("timeline"),
    onFrame: (frame) => {
      if (frame && frame.url) radar.setUrl(frame.url);
    },
    onLive: (isLive) => {
      document.getElementById("tl-live").style.opacity = isLive ? "1" : "0.5";
    },
  });

  // --- Status panel ---
  function renderStatus(s) {
    const cat = s.current.category;
    const meta = s.category_meta || {};
    document.getElementById("status-glyph").textContent = (meta.glyph || {})[cat] || "·";
    document.getElementById("status-label").textContent =
      ((meta.label || {})[cat] || cat) + (s.degraded ? "  (stale)" : "");
    document.getElementById("status-detail").textContent = s.verdict.detail;
    document.getElementById("status-mmh").textContent = s.current.intensity_mmh.toFixed(1) + " mm/h";
    document.getElementById("status-coverage").textContent = Math.round(s.current.coverage * 100) + " %";
    document.getElementById("status-updated").textContent = fmtClock(s.latest_observed_time || s.sampled_at);

    const v = document.getElementById("status-verdict");
    v.className = "status-verdict " + s.verdict.status;
    const ne = s.verdict.next_event;
    v.textContent = s.verdict.label + (ne
      ? `  ·  ${ne.type === "clearing" ? "clearing" : ne.type === "onset" ? "starts" : "peaks"} ~${ne.in_min} min`
      : "");

    document.getElementById("status").style.borderLeft =
      "4px solid " + ((meta.color || {})[cat] || "#38bdf8");
  }

  async function fetchStatus() {
    try {
      const r = await fetch(`/api/status?lat=${CENTER.lat}&lon=${CENTER.lon}&radius_km=${RADIUS_KM}`);
      const s = await r.json();
      renderStatus(s);
    } catch (e) { console.warn("status failed", e); }
  }

  async function fetchFrames() {
    try {
      const r = await fetch("/api/frames?history=12&forecast=true");
      const f = await r.json();
      tl.setFrames(f.observed, f.forecast);
    } catch (e) { console.warn("frames failed", e); }
  }

  function fmtClock(iso) {
    try {
      return new Date(iso).toLocaleTimeString("cs-CZ", { hour: "2-digit", minute: "2-digit", timeZone: "Europe/Prague" });
    } catch { return iso; }
  }

  // --- bootstrap ---
  fetchFrames();
  fetchStatus();
  setInterval(fetchStatus, STATUS_POLL * 1000);
  setInterval(fetchFrames, FRAMES_POLL * 1000);
})();
