import "maplibre-gl/dist/maplibre-gl.css";
import "./style.css";

import { compareRoutes } from "./compare";
import { ApiError, requestExposureSurface, submitRouteRequest, surfaceZoom } from "./api";
import { DISCLAIMER, STUDY_AREA, SURFACE_DEBOUNCE_MS } from "./config";
import { clear, h, icon } from "./dom";
import { describeRouteError, type ErrorField, type ErrorView } from "./errors";
import { HEAT_LAYER_LABELS, exposureClass, layerRatio } from "./exposure";
import { confidenceBadge, renderResult } from "./results-view";
import { fmtCoord, fmtNumber, fmtWib } from "./format";
import { Legend, type HeatStatus } from "./legend";
import { MapView, type SegmentClick } from "./map-view";
import type { ExposureSurface, HeatLayer, Mode, Point } from "./types";
import { buildRouteRequest, parseCoordinateText, preferenceFromSlider, validateTrip } from "./validation";

// Presentation layer (PF-01, PF-02, PF-09 – PF-12, PNF-07, PNF-08): trip form,
// route comparison, exposure heatmap, confidence, fallback and error states.

const $ = <T extends HTMLElement>(id: string): T => {
  const el = document.getElementById(id);
  if (!el) throw new Error(`#${id} missing from index.html`);
  return el as T;
};

type Which = "origin" | "destination";

// ------------------------------------------------------------------ elements
const panel = $("panel");
const formView = $("form-view");
const resultView = $("result-view");
const tripForm = $<HTMLFormElement>("trip-form");
const submitBtn = $<HTMLButtonElement>("submit-btn");
const cancelBtn = $<HTMLButtonElement>("cancel-btn");
const submitHelp = $("submit-help");
const formStatus = $("form-status");
const pickHint = $("pick-hint");
const slider = $<HTMLInputElement>("priority");
const heatToggle = $<HTMLButtonElement>("heat-toggle");

const fields: Record<Which, { input: HTMLInputElement; error: HTMLElement; wrap: HTMLElement; pick: HTMLButtonElement }> = {
  origin: {
    input: $("input-origin"),
    error: $("error-origin"),
    wrap: $("field-origin"),
    pick: $("pick-origin"),
  },
  destination: {
    input: $("input-destination"),
    error: $("error-destination"),
    wrap: $("field-destination"),
    pick: $("pick-destination"),
  },
};

// --------------------------------------------------------------------- state
const state = {
  points: { origin: null, destination: null } as Record<Which, Point | null>,
  /** Text the user typed that is not a coordinate pair. */
  parseError: { origin: false, destination: false } as Record<Which, boolean>,
  touched: { origin: false, destination: false } as Record<Which, boolean>,
  /** Error the Backend attached to one point (e.g. point_not_on_network). */
  serverError: { origin: null, destination: null } as Record<Which, string | null>,
  pick: "origin" as Which | null,
  busy: false,
  controller: null as AbortController | null,
  waitUntil: 0,
  heat: {
    on: false,
    userTurnedOff: false,
    layer: "combined" as HeatLayer,
    status: "idle" as HeatStatus,
    surface: null as ExposureSurface | null,
    controller: null as AbortController | null,
    timer: 0 as number,
  },
};

const mapView = new MapView($("map"));
// Handle for manual and browser-driven testing; stripped from production builds.
if (import.meta.env.DEV) (window as unknown as { __map: unknown }).__map = mapView.map;
const legend = new Legend($("legend"));

// -------------------------------------------------------------------- static
$("disclaimer").textContent = DISCLAIMER;
$("mode-walk-label").append(icon("walk"), " Jalan kaki");
$("mode-bike-label").append(icon("bike"), " Sepeda");
$("swap-btn").append(icon("swap", 20));
$("locate-btn").append(icon("locate", 22));
heatToggle.append(icon("layers", 22));
for (const w of ["origin", "destination"] as const) fields[w].pick.append(icon("pin", 20));

const mode = (): Mode => (tripForm.elements.namedItem("mode") as RadioNodeList).value === "bike" ? "bike" : "walk";

// -------------------------------------------------------------- form helpers
function setPoint(which: Which, p: Point | null, opts: { fill?: boolean; fly?: boolean } = {}): void {
  state.points[which] = p;
  state.parseError[which] = false;
  state.serverError[which] = null;
  if (p) state.touched[which] = true;
  if (opts.fill !== false) fields[which].input.value = p ? fmtCoord(p) : "";
  mapView.setPoint(which, p);
  if (p && opts.fly) mapView.flyTo(p);
  refreshForm();
}

function currentErrors(): Record<Which, string | null> {
  const trip = validateTrip(state.points.origin, state.points.destination, mode());
  const out: Record<Which, string | null> = { origin: trip.origin, destination: trip.destination };
  for (const w of ["origin", "destination"] as const) {
    if (state.parseError[w]) out[w] = 'Format koordinat tidak dikenali. Contoh: -6.2088, 106.8456.';
  }
  return out;
}

function refreshForm(): void {
  const errors = currentErrors();
  const valid = !errors.origin && !errors.destination;
  for (const w of ["origin", "destination"] as const) {
    const f = fields[w];
    const shown = state.serverError[w] ?? (state.touched[w] || state.parseError[w] ? errors[w] : null);
    f.error.hidden = !shown;
    f.error.textContent = shown ?? "";
    f.wrap.classList.toggle("is-invalid", !!shown);
    f.input.setAttribute("aria-invalid", String(!!shown));
    f.pick.setAttribute("aria-pressed", String(state.pick === w));
  }

  const waiting = state.waitUntil > Date.now();
  submitBtn.disabled = !valid || state.busy || waiting;
  submitBtn.textContent = state.busy ? "Mencari rute…" : "Cari rute";
  submitBtn.classList.toggle("is-busy", state.busy);
  submitBtn.setAttribute("aria-busy", String(state.busy));
  cancelBtn.hidden = !state.busy;
  submitHelp.textContent = state.busy
    ? ""
    : waiting
      ? `Tunggu ${Math.ceil((state.waitUntil - Date.now()) / 1000)} detik sebelum mencari lagi.`
      : !valid
        ? "Lengkapi titik asal dan tujuan yang valid untuk mulai mencari."
        : "";

  pickHint.textContent = state.pick
    ? `Klik peta untuk memilih titik ${state.pick === "origin" ? "asal" : "tujuan"}.`
    : "";
  mapView.setPickCursor(state.pick !== null);
}

function onTyped(which: Which): void {
  const text = fields[which].input.value;
  const p = parseCoordinateText(text);
  state.serverError[which] = null;
  state.parseError[which] = text.trim() !== "" && !p;
  state.points[which] = p;
  mapView.setPoint(which, p);
  refreshForm();
}

for (const w of ["origin", "destination"] as const) {
  const f = fields[w];
  f.input.addEventListener("input", () => onTyped(w));
  f.input.addEventListener("blur", () => {
    state.touched[w] = true;
    refreshForm();
  });
  f.pick.addEventListener("click", () => {
    state.pick = state.pick === w ? null : w;
    refreshForm();
  });
}

$("swap-btn").addEventListener("click", () => {
  const { origin, destination } = state.points;
  const texts = [fields.origin.input.value, fields.destination.input.value];
  state.points = { origin: destination, destination: origin };
  state.parseError = { origin: state.parseError.destination, destination: state.parseError.origin };
  state.serverError = { origin: null, destination: null };
  fields.origin.input.value = texts[1];
  fields.destination.input.value = texts[0];
  mapView.setPoint("origin", state.points.origin);
  mapView.setPoint("destination", state.points.destination);
  refreshForm();
});

tripForm.addEventListener("change", refreshForm);

function updatePriority(): void {
  // The slider runs from "Lebih cepat" (left) to "Lebih bersih" (right).
  const { alpha, beta } = preferenceFromSlider(Number(slider.value));
  $("priority-values").textContent = `α ${fmtNumber(alpha, 2)} · β ${fmtNumber(beta, 2)}`;
  $("priority-text").textContent = `Bobot waktu tempuh ${fmtNumber(alpha, 2)}, bobot paparan ${fmtNumber(beta, 2)}.`;
  slider.style.setProperty("--fill", `${Number(slider.value) * 100}%`);
}
slider.addEventListener("input", updatePriority);
updatePriority();

// ---------------------------------------------------------------- map events
mapView.onPointMoved = (which, p) => setPoint(which, p);

mapView.onClick = ({ point, feature }: SegmentClick) => {
  // With the heatmap on, a click on a segment inspects it (its card offers to use
  // it as origin or destination); a click elsewhere falls through to point picking.
  if (state.heat.on && feature) {
    mapView.showPopup(point, segmentCard(feature, point));
    return;
  }
  mapView.closePopup();
  if (!state.pick) return;
  const which = state.pick;
  state.pick = which === "origin" && !state.points.destination ? "destination" : null;
  setPoint(which, point);
};

function segmentCard(f: NonNullable<SegmentClick["feature"]>, at: Point): HTMLElement {
  const layer = state.heat.layer;
  const ratio = layerRatio(layer, f);
  const isCell = f.geometry.type === "Polygon" || f.geometry.type === "MultiPolygon";
  const shown =
    layer === "combined" ? f.exposureIndex : layer === "pm25" ? f.pm25 : f.no2;
  const unit = layer === "combined" ? "indeks paparan" : "µg/m³";
  const conc = (v: number | null) => (v == null ? "tidak tersedia" : `${fmtNumber(v, 0)} µg/m³`);
  return h(
    "div",
    { class: "segment-card" },
    h("div", { class: "segment-name" }, f.name ?? (isCell ? "Rata-rata area sekitar" : "Ruas jalan")),
    h(
      "div",
      { class: "segment-value" },
      h("span", { class: "mono big" }, shown == null ? "—" : fmtNumber(shown, layer === "combined" ? 2 : 0)),
      ` ${unit}`,
      layer !== "combined" && h("span", { class: "segment-layer" }, ` (${HEAT_LAYER_LABELS[layer]})`),
    ),
    h("div", { class: "segment-conc mono" }, `PM2.5 ${conc(f.pm25)} · NO2 ${conc(f.no2)}`),
    h("div", { class: "segment-class" }, ratio == null ? "Tanpa estimasi" : exposureClass(ratio)),
    confidenceBadge(f.confidence),
    h(
      "div",
      { class: "segment-actions" },
      h("button", { type: "button", class: "secondary dark", onclick: () => useSegment("origin", at) }, "Cari rute dari sini"),
      h("button", { type: "button", class: "secondary", onclick: () => useSegment("destination", at) }, "Jadikan tujuan"),
    ),
  );
}

function useSegment(which: Which, at: Point): void {
  mapView.closePopup();
  showFormView();
  state.pick = null;
  setPoint(which, at);
}

// ---------------------------------------------------------------- route flow
function showFormView(): void {
  formView.hidden = false;
  resultView.hidden = true;
}

function clearStatus(): void {
  clear(formStatus);
}

function showStatusLoading(): void {
  clear(formStatus);
  formStatus.append(h("div", { class: "loading" }, h("span", { class: "spinner", "aria-hidden": "true" }), "Menghitung rute… maksimal 12 detik."));
}

function showError(view: ErrorView, retry: () => void): void {
  clear(formStatus);
  const actions = h("div", { class: "error-actions" });
  if (view.action === "retry") actions.append(h("button", { type: "button", class: "secondary", onclick: retry }, "Coba lagi"));
  formStatus.append(
    h(
      "div",
      { class: "notice notice-error", role: "alert" },
      icon("warn", 18),
      h(
        "div",
        {},
        h("strong", {}, view.title),
        h("p", {}, view.message),
        view.requestId && h("p", { class: "req-id" }, `ID permintaan: ${view.requestId}`),
        actions,
      ),
    ),
  );
}

function applyServerField(field: ErrorField, message: string): void {
  if (field) {
    state.serverError[field] = message;
    state.touched[field] = true;
    fields[field].input.focus();
  }
}

/** Removes the previous result from panel, legend and map. The map part is queued
 *  behind the map's load; nothing here waits for the basemap. */
function dropResult(): void {
  legend.setRoutes(null);
  resultView.hidden = true;
  clear(resultView);
  void mapView.clearRoutes();
}

async function submit(): Promise<void> {
  const errors = currentErrors();
  const { origin, destination } = state.points;
  if (state.busy || errors.origin || errors.destination || !origin || !destination) return;
  if (state.waitUntil > Date.now()) return;

  const request = buildRouteRequest(origin, destination, mode(), Number(slider.value));
  const controller = new AbortController();
  state.controller = controller;
  state.busy = true;
  state.serverError = { origin: null, destination: null };
  state.pick = null;
  showStatusLoading();
  refreshForm();

  // A new request starts from a clean slate: nothing from an earlier search may
  // stay on the map or in the panel while this one is pending or if it fails.
  dropResult();

  try {
    const response = await submitRouteRequest(request, controller.signal);
    if (state.controller !== controller) return;
    state.busy = false;
    clearStatus();
    formView.hidden = true;
    resultView.hidden = false;
    const title = renderResult(resultView, { response, request, onBack: backToForm });
    legend.setRoutes({
      fallback: response.recommended.routeSource === "baseline",
      hasComparison: compareRoutes(response).kind !== "none" && compareRoutes(response).kind !== "identical",
    });
    const narrow = window.matchMedia("(max-width: 899px)").matches;
    void mapView.showRoutes(response, { bottom: narrow ? panel.offsetHeight + 16 : 0 });
    title.focus({ preventScroll: true });
    refreshForm();
    // Exposure surface is requested once the route is displayed (Tabel 3.4).
    // Debounced, so it fires once after the map has finished fitting to the route.
    if (!state.heat.userTurnedOff) setHeat(true, false);
    else if (state.heat.on) scheduleSurface();
  } catch (err) {
    if (state.controller !== controller) return;
    state.busy = false;
    if (err instanceof ApiError && err.kind === "aborted") {
      clear(formStatus);
      formStatus.append(h("p", { class: "status-note" }, "Pencarian dibatalkan."));
    } else {
      const view = describeRouteError(err);
      // An error about one point is shown on that field only (Gambar 3.2c).
      if (view.field) clearStatus();
      else showError(view, () => void submit());
      applyServerField(view.field, view.message);
      if (view.waitSec) startWait(view.waitSec);
    }
    refreshForm();
  } finally {
    if (state.controller === controller) state.controller = null;
  }
}

let waitTimer = 0;
function startWait(seconds: number): void {
  state.waitUntil = Date.now() + seconds * 1000;
  window.clearInterval(waitTimer);
  waitTimer = window.setInterval(() => {
    if (state.waitUntil <= Date.now()) {
      window.clearInterval(waitTimer);
      state.waitUntil = 0;
      clearStatus(); // the "coba lagi dalam N detik" notice is no longer true
    }
    refreshForm();
  }, 1000);
}

function backToForm(): void {
  dropResult();
  showFormView();
  refreshForm();
  fields.destination.input.focus();
}

tripForm.addEventListener("submit", (e) => {
  e.preventDefault();
  void submit();
});
cancelBtn.addEventListener("click", () => state.controller?.abort());

// ------------------------------------------------------------------- heatmap
function legendState(): void {
  const hs = state.heat;
  legend.setHeat({
    visible: hs.on,
    layer: hs.layer,
    status: hs.status,
    timeLabel: fmtWib(hs.surface?.timeWindowStart),
    stale: hs.surface?.dataStale ?? false,
    showingOldData: !!hs.surface && hs.surface.features.length > 0,
  });
}

function setHeat(on: boolean, immediate = true): void {
  const hs = state.heat;
  hs.on = on;
  heatToggle.setAttribute("aria-pressed", String(on));
  heatToggle.setAttribute("aria-label", on ? "Sembunyikan heatmap paparan" : "Tampilkan heatmap paparan");
  void mapView.showHeat(on);
  if (on) {
    scheduleSurface(immediate);
  } else {
    window.clearTimeout(hs.timer);
    hs.controller?.abort();
    hs.controller = null;
  }
  legendState();
}

function scheduleSurface(immediate = false): void {
  const hs = state.heat;
  window.clearTimeout(hs.timer);
  if (!hs.on) return;
  hs.timer = window.setTimeout(() => void loadSurface(), immediate ? 0 : SURFACE_DEBOUNCE_MS);
}

async function loadSurface(): Promise<void> {
  const hs = state.heat;
  hs.controller?.abort();
  const controller = new AbortController();
  hs.controller = controller;
  hs.status = "loading";
  legendState();

  const { bbox, zoom } = mapView.getView();
  try {
    const surface = await requestExposureSurface(bbox, surfaceZoom(zoom, bbox), controller.signal);
    if (hs.controller !== controller) return;
    hs.surface = surface;
    hs.status = "ready";
    void mapView.setSurface(surface);
    void mapView.setHeatDimmed(false);
  } catch (err) {
    if (hs.controller !== controller) return;
    if (err instanceof ApiError && err.kind === "aborted") return;
    // The previous heatmap stays (route is unaffected, Tabel 3.15) but is dimmed
    // and labelled as old so it cannot pass for current data.
    hs.status = "error";
    void mapView.setHeatDimmed(true);
  }
  legendState();
}

heatToggle.addEventListener("click", () => {
  state.heat.userTurnedOff = state.heat.on;
  setHeat(!state.heat.on);
  if (state.heat.on) legend.expand(true);
});
legend.onLayer = (layer) => {
  state.heat.layer = layer;
  void mapView.setHeatLayer(layer); // re-colours data in memory; no request
  mapView.closePopup();
  legendState();
};
legend.onRetry = () => scheduleSurface(true);
mapView.onViewChange = () => scheduleSurface();

// ------------------------------------------------------------------ geolocate
$("locate-btn").addEventListener("click", () => {
  clearStatus();
  const say = (text: string) => {
    showFormView();
    clear(formStatus);
    formStatus.append(h("p", { class: "status-note" }, text));
  };
  if (!navigator.geolocation) return say("Peramban ini tidak mendukung lokasi.");
  navigator.geolocation.getCurrentPosition(
    (pos) => {
      const p = { lat: pos.coords.latitude, lon: pos.coords.longitude };
      const a = STUDY_AREA;
      if (p.lat < a.minLat || p.lat > a.maxLat || p.lon < a.minLon || p.lon > a.maxLon) {
        say("Lokasi Anda berada di luar wilayah studi (Jakarta), jadi tidak dipakai sebagai titik asal.");
        return;
      }
      state.pick = state.points.destination ? null : "destination";
      setPoint("origin", p, { fly: true });
    },
    () => say("Lokasi tidak dapat diakses. Pilih titik asal di peta."),
    { enableHighAccuracy: false, timeout: 10_000, maximumAge: 60_000 },
  );
});

// ------------------------------------------------------------- bottom sheet
const sheetHandle = $("sheet-handle");
sheetHandle.addEventListener("click", () => {
  const collapsed = panel.classList.toggle("collapsed");
  sheetHandle.setAttribute("aria-expanded", String(!collapsed));
  sheetHandle.setAttribute("aria-label", collapsed ? "Perbesar panel" : "Ciutkan panel");
});

refreshForm();
legendState();
