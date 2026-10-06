// Mock of the Backend's public API (IRouteQuery + IExposureSurface, design
// document subbab 3.5.2) for developing and testing the Frontend without the
// real route model. The real Backend still returns a placeholder.
//
//   npm run mock                       # listens on :8000 (override with PORT)
//   curl "localhost:8000/__mock/scenario?name=fallback"
//   curl "localhost:8000/__mock/scenario"      # lists scenarios and current state
//
// Route scenarios apply to POST /v1/routes, "heat_*" scenarios to GET
// /v1/exposure-surface. Both are independent and stay set until changed.
import { createServer } from "node:http";

const PORT = Number(process.env.PORT ?? 8000);

const ROUTE_SCENARIOS = {
  ok: "Rute rendah paparan + tercepat, data terbaru",
  fallback: "route_source=baseline, policy_timeout",
  stale: "data_stale=true (time window 5 jam lalu)",
  partial: "missing_edge_count=7, keyakinan rendah",
  same_route: "Rute rekomendasi identik dengan rute tercepat",
  higher: "Rute rekomendasi dengan paparan lebih tinggi",
  no_recommended: "200 tanpa fitur rute (keadaan tanpa rute)",
  no_route: "422 no_route_found",
  point_not_on_network: "422 point_not_on_network (tujuan)",
  point_origin: "422 point_not_on_network (asal)",
  outside_study_area: "422 outside_study_area",
  invalid_request: "400 invalid_request",
  rate_limited: "429 rate_limited, Retry-After: 10",
  cache_unavailable: "503 cache_unavailable",
  route_unavailable: "503 route_service_unavailable",
  upstream_timeout: "504 upstream_timeout",
  gateway_html: "502 dengan badan HTML (bukan JSON)",
  timeout: "Tidak merespons selama 20 detik (klien menyerah di 12 detik)",
  slow: "Respons sukses setelah 3 detik",
  bad_shape: "200 dengan badan placeholder backend saat ini (tidak sesuai kontrak)",
  bad_json: "200 dengan badan bukan JSON",
};
const HEAT_SCENARIOS = {
  heat_ok: "Heatmap normal",
  heat_stale: "Heatmap dengan data_stale=true",
  heat_error: "500 pada exposure-surface",
  heat_slow: "Respons setelah 7 detik (klien menyerah di 5 detik)",
  heat_empty: "FeatureCollection kosong",
};

let routeScenario = "ok";
let heatScenario = "heat_ok";

const DISCLAIMER =
  "Estimasi konsentrasi polutan merupakan keluaran model dari data satelit dan sensor darat, bukan pengukuran langsung. Bukan rekomendasi medis.";

// ----------------------------------------------------------------- helpers
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const rad = (d) => (d * Math.PI) / 180;
function haversine([lon1, lat1], [lon2, lat2]) {
  const a = Math.sin(rad(lat2 - lat1) / 2) ** 2 + Math.cos(rad(lat1)) * Math.cos(rad(lat2)) * Math.sin(rad(lon2 - lon1) / 2) ** 2;
  return 2 * 6371000 * Math.asin(Math.sqrt(a));
}
const length = (line) => line.slice(1).reduce((m, p, i) => m + haversine(line[i], p), 0);

function windowStart(hoursAgo = 1) {
  const d = new Date(Math.floor(Date.now() / 3_600_000) * 3_600_000 - hoursAgo * 3_600_000);
  return d.toISOString().replace(".000", "");
}

function send(res, status, body, headers = {}) {
  const isString = typeof body === "string";
  res.writeHead(status, {
    "Content-Type": isString ? "text/plain" : "application/json",
    "X-Request-ID": "00000000-mock-0000-0000-000000000000",
    ...headers,
  });
  res.end(isString ? body : JSON.stringify(body));
}

const error = (res, status, code, message, headers) =>
  send(res, status, { error: { code, message, request_id: "00000000-mock-0000-0000-000000000000" } }, headers);

// ------------------------------------------------------------------ routes
function buildRoutes(req, scenario) {
  const o = [req.origin.lon, req.origin.lat];
  const d = [req.destination.lon, req.destination.lat];
  const kinkLon = o[0] + (d[0] - o[0]) * 0.35;
  const fast = [o, [kinkLon, o[1]], [kinkLon, d[1]], d];
  const midLat = o[1] + (d[1] - o[1]) * 0.6;
  const swing = o[0] - (d[0] - o[0]) * 0.12; // the low-exposure route detours off the direct corridor
  const low = [o, [swing, o[1]], [swing, midLat], [d[0], midLat], d];

  const speed = req.mode === "bike" ? 250 : 78; // m per minute
  const fastM = length(fast);
  const lowM = length(low);
  const fastMin = fastM / speed;
  const lowMin = lowM / speed;
  const beta = req.preference?.beta ?? 0.5;
  const fastIdx = 1.9;
  const lowIdx = scenario === "higher" ? 2.3 : 1.9 - 0.9 * beta; // more "bersih" → lower index
  const fastExp = fastMin * fastIdx;
  const lowExp = lowMin * lowIdx;

  const fb = scenario === "fallback";
  const same = scenario === "same_route";
  const recLine = fb || same ? fast : low;
  const rec = {
    role: "recommended",
    route_source: fb ? "baseline" : "policy",
    preference_applied: !fb,
    travel_time_min: fb || same ? fastMin : lowMin,
    distance_m: Math.round(fb || same ? fastM : lowM),
    exposure_index_min: fb || same ? fastExp : lowExp,
    confidence_score: fb ? 0.38 : scenario === "partial" ? 0.34 : 0.71,
  };
  const cmp = {
    role: "comparison",
    route_source: "baseline",
    preference_applied: false,
    travel_time_min: fastMin,
    distance_m: Math.round(fastM),
    exposure_index_min: fastExp,
    confidence_score: fb ? 0.36 : scenario === "partial" ? 0.3 : 0.68,
  };
  return {
    type: "FeatureCollection",
    features: [
      { type: "Feature", geometry: { type: "LineString", coordinates: recLine }, properties: rec },
      { type: "Feature", geometry: { type: "LineString", coordinates: fast }, properties: cmp },
    ],
    metadata: {
      request_id: "00000000-mock-0000-0000-000000000000",
      mode: req.mode,
      pollutant: req.pollutant,
      preference: req.preference,
      time_window_start: windowStart(scenario === "stale" ? 5 : 1),
      data_stale: scenario === "stale",
      missing_edge_count: scenario === "partial" ? 7 : 0,
      fallback_reason: fb ? "policy_timeout" : null,
      model_version: { downscaling: "12", route_policy: "4" },
      comparison: {
        delta_travel_time_min: rec.travel_time_min - fastMin,
        delta_exposure_pct: ((rec.exposure_index_min - fastExp) / fastExp) * 100,
      },
      disclaimer: DISCLAIMER,
    },
  };
}

async function handleRoutes(req, res) {
  const raw = await new Promise((resolve) => {
    let b = "";
    req.on("data", (c) => (b += c));
    req.on("end", () => resolve(b));
  });
  let body;
  try {
    body = JSON.parse(raw);
  } catch {
    return error(res, 400, "invalid_request", "Badan permintaan bukan JSON.");
  }
  const s = routeScenario;
  console.log(`POST /v1/routes [${s}]`, JSON.stringify(body));

  // The same checks the real Backend promises (Tabel 3.16, 400 invalid_request).
  const p = body.preference;
  if (!body.origin || !body.destination || !["walk", "bike"].includes(body.mode) || !p || p.alpha < 0 || p.alpha > 1 || Math.abs(p.alpha + p.beta - 1) > 1e-9) {
    return error(res, 400, "invalid_request", "Skema tidak sesuai atau α + β ≠ 1.");
  }

  switch (s) {
    case "no_route": return error(res, 422, "no_route_found", "Titik asal dan tujuan tidak terhubung.");
    case "point_not_on_network": return error(res, 422, "point_not_on_network", "Titik tujuan berjarak lebih dari 200 m dari ruas yang dapat dilalui.");
    case "point_origin": return error(res, 422, "point_not_on_network", "Titik asal berjarak lebih dari 200 m dari ruas yang dapat dilalui.");
    case "outside_study_area": return error(res, 422, "outside_study_area", "Titik tujuan berada di luar wilayah studi.");
    case "invalid_request": return error(res, 400, "invalid_request", "Skema tidak sesuai.");
    case "rate_limited": return error(res, 429, "rate_limited", "Batas permintaan terlampaui.", { "Retry-After": "10" });
    case "cache_unavailable": return error(res, 503, "cache_unavailable", "Layer cache tidak dapat dibaca.");
    case "route_unavailable": return error(res, 503, "route_service_unavailable", "Model rute tidak merespons.");
    case "upstream_timeout": return error(res, 504, "upstream_timeout", "Backend tidak merespons.");
    case "gateway_html": return send(res, 502, "<html><body><h1>502 Bad Gateway</h1></body></html>", { "Content-Type": "text/html" });
    case "timeout": await sleep(20_000); break;
    case "slow": await sleep(3_000); break;
    case "bad_shape": return send(res, 200, { route: null, preference_applied: true, message: "Multi-objective route policy inference not implemented yet." });
    case "bad_json": return send(res, 200, "ini bukan json");
    case "no_recommended": return send(res, 200, { type: "FeatureCollection", features: [], metadata: { request_id: "00000000-mock-0000-0000-000000000000" } }, { "Content-Type": "application/geo+json" });
  }
  send(res, 200, buildRoutes(body, s), { "Content-Type": "application/geo+json" });
}

// ---------------------------------------------------------------- surface
function indexAt(lat, lon) {
  const sudirman = lon - (106.822 + (lat + 6.2) * -0.18);
  const corridor = Math.exp(-((sudirman / 0.0035) ** 2));
  const eastWest = Math.exp(-(((lat + 6.215) / 0.003) ** 2)) + 0.7 * Math.exp(-(((lat + 6.185) / 0.003) ** 2));
  const wobble = 0.15 * Math.sin(lat * 900) * Math.cos(lon * 700);
  return Math.max(0.15, 0.55 + 2.5 * corridor + 0.9 * eastWest + wobble);
}
const confidenceAt = (lat, lon) => (lat > -6.17 && lon < 106.8 ? 0.3 : 0.75 + 0.1 * Math.sin(lon * 400));

function props(lat, lon, name) {
  const idx = indexAt(lat, lon);
  return {
    name,
    exposure_index: Number(idx.toFixed(2)),
    pm25_ugm3: Number((idx * 15 * 0.95).toFixed(1)),
    no2_ugm3: Number((idx * 25 * 1.05).toFixed(1)),
    confidence_score: Number(confidenceAt(lat, lon).toFixed(2)),
  };
}

function buildSurface(bbox, zoom, stale) {
  const [minLon, minLat, maxLon, maxLat] = bbox;
  const features = [];
  if (zoom >= 14) {
    const gap = 0.0022;
    const step = 0.0011;
    for (let lat = Math.ceil(minLat / gap) * gap, n = 1; lat <= maxLat; lat += gap, n++) {
      for (let lon = minLon; lon < maxLon; lon += step) {
        const mid = [lon + step / 2, lat];
        features.push({ type: "Feature", geometry: { type: "LineString", coordinates: [[lon, lat], [lon + step, lat]] }, properties: props(mid[1], mid[0], `Jl. Mock Timur-Barat ${n}`) });
      }
    }
    for (let lon = Math.ceil(minLon / gap) * gap, n = 1; lon <= maxLon; lon += gap, n++) {
      for (let lat = minLat; lat < maxLat; lat += step) {
        const nearSudirman = Math.abs(lon - (106.822 + (lat + 6.2) * -0.18)) < 0.002;
        features.push({ type: "Feature", geometry: { type: "LineString", coordinates: [[lon, lat], [lon, lat + step]] }, properties: props(lat + step / 2, lon, nearSudirman ? "Jl. Jend. Sudirman" : `Jl. Mock Utara-Selatan ${n}`) });
      }
    }
  } else {
    const cell = Math.max(0.005, Math.max(maxLon - minLon, maxLat - minLat) / 40);
    for (let lat = Math.floor(minLat / cell) * cell; lat < maxLat; lat += cell) {
      for (let lon = Math.floor(minLon / cell) * cell; lon < maxLon; lon += cell) {
        features.push({
          type: "Feature",
          geometry: { type: "Polygon", coordinates: [[[lon, lat], [lon + cell, lat], [lon + cell, lat + cell], [lon, lat + cell], [lon, lat]]] },
          properties: props(lat + cell / 2, lon + cell / 2, null),
        });
      }
    }
  }
  return {
    type: "FeatureCollection",
    features: features.slice(0, 6000),
    metadata: { time_window_start: windowStart(stale ? 5 : 1), data_stale: stale },
  };
}

async function handleSurface(url, res) {
  const s = heatScenario;
  console.log(`GET /v1/exposure-surface [${s}] ${url.search}`);
  const bbox = (url.searchParams.get("bbox") ?? "").split(",").map(Number);
  const zoom = Number(url.searchParams.get("zoom"));
  if (bbox.length !== 4 || bbox.some((n) => !Number.isFinite(n)) || !Number.isFinite(zoom)) {
    return error(res, 400, "invalid_request", "bbox atau zoom tidak valid.");
  }
  const km2 = (bbox[2] - bbox[0]) * 111.32 * Math.cos(rad((bbox[1] + bbox[3]) / 2)) * (bbox[3] - bbox[1]) * 111.32;
  if (zoom >= 14 && km2 > 25) return error(res, 400, "invalid_request", "bbox terlalu luas untuk zoom yang diminta.");

  if (s === "heat_error") return error(res, 500, "internal_error", "Galat internal.");
  if (s === "heat_slow") await sleep(7_000);
  if (s === "heat_empty") return send(res, 200, { type: "FeatureCollection", features: [], metadata: { time_window_start: windowStart(), data_stale: false } }, { "Content-Type": "application/geo+json" });
  send(res, 200, buildSurface(bbox, zoom, s === "heat_stale"), { "Content-Type": "application/geo+json" });
}

// ------------------------------------------------------------------ server
createServer(async (req, res) => {
  const url = new URL(req.url, `http://${req.headers.host}`);
  try {
    if (url.pathname === "/__mock/scenario") {
      const name = url.searchParams.get("name");
      if (name) {
        if (name in ROUTE_SCENARIOS) routeScenario = name;
        else if (name in HEAT_SCENARIOS) heatScenario = name;
        else return send(res, 404, { error: `unknown scenario ${name}` });
      }
      return send(res, 200, { current: { routes: routeScenario, heat: heatScenario }, routes: ROUTE_SCENARIOS, heat: HEAT_SCENARIOS });
    }
    if (url.pathname === "/health") return send(res, 200, { status: "ok", service: "mock-backend" });
    if (url.pathname === "/v1/routes" && req.method === "POST") return await handleRoutes(req, res);
    if (url.pathname === "/v1/exposure-surface" && req.method === "GET") return await handleSurface(url, res);
    error(res, 404, "not_found", "Tidak ada endpoint ini pada mock.");
  } catch (e) {
    console.error(e);
    if (!res.headersSent) error(res, 500, "internal_error", "Galat pada mock.");
  }
}).listen(PORT, () => {
  console.log(`Mock backend on http://localhost:${PORT} — scenarios: GET /__mock/scenario`);
});
