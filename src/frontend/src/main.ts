import "maplibre-gl/dist/maplibre-gl.css";
import "./style.css";

import { compareRoutes } from "./compare";
import { ApiError, requestExposureSurface, reversePlace, searchPlaces, submitRouteRequest, surfaceZoom } from "./api";
import {
  GEOCODE_DEBOUNCE_MS,
  GEOCODE_MIN_CHARS,
  STUDY_AREA,
  SURFACE_DEBOUNCE_MS,
} from "./config";
import { clear, h, icon } from "./dom";
import { describeRouteError, type ErrorField, type ErrorView } from "./errors";
import { getLang, onLangChange, setLang, tr, translateDom, type Lang } from "./i18n";
import { renderResult, setRouteVia } from "./results-view";
import { initTooltips } from "./tooltip";
import { routeMidpoint, streetFromPlace } from "./route-names";
import { fmtCoord, fmtNumber } from "./format";
import { Legend } from "./legend";
import { MapView, type SegmentClick } from "./map-view";
import { NavView, type NavMessage } from "./nav-view";
import type { RouteLine } from "./nav-geometry";
import { NavigationSession, type Fix } from "./navigation";
import { PlaceCombobox } from "./place-combobox";
import { PlaceSearch, PointNamer } from "./place-search";
import {
  DevicePosition,
  describePositionProblem,
  isFatal,
  positionProblem,
  problemFromCode,
  type PositionProblem,
  type PositionSource,
} from "./position";
import { SurfaceRefresher } from "./surface-refresher";
import type { BBox, HeatLayer, Mode, Place, Point, RouteRequest, RouteResponse, RouteResult } from "./types";
import { buildRouteRequest, parseCoordinateText, preferenceFromSlider, validatePoint, validateTrip } from "./validation";

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
const navViewEl = $("nav-view");
const tripForm = $<HTMLFormElement>("trip-form");
const submitBtn = $<HTMLButtonElement>("submit-btn");
const cancelBtn = $<HTMLButtonElement>("cancel-btn");
const submitHelp = $("submit-help");
const formStatus = $("form-status");
const pickHint = $("pick-hint");
const slider = $<HTMLInputElement>("priority");
const heatToggle = $<HTMLButtonElement>("heat-toggle");

const fields: Record<Which, { input: HTMLInputElement; error: HTMLElement; wrap: HTMLElement; pick: HTMLButtonElement; clear: HTMLButtonElement }> = {
  origin: {
    input: $("input-origin"),
    error: $("error-origin"),
    wrap: $("field-origin"),
    pick: $("pick-origin"),
    clear: $("clear-origin"),
  },
  destination: {
    input: $("input-destination"),
    error: $("error-destination"),
    wrap: $("field-destination"),
    pick: $("pick-destination"),
    clear: $("clear-destination"),
  },
};

// --------------------------------------------------------------------- state
const state = {
  points: { origin: null, destination: null } as Record<Which, Point | null>,
  /** Name shown for the point (a chosen suggestion or the map point's name); null for bare coordinates. */
  labels: { origin: null, destination: null } as Record<Which, string | null>,
  /** Text the user typed that is neither a coordinate pair nor a chosen place. */
  parseError: { origin: false, destination: false } as Record<Which, boolean>,
  touched: { origin: false, destination: false } as Record<Which, boolean>,
  /** A map point whose name is being looked up: its field shows "Memuat lokasi…" until then. */
  naming: { origin: null, destination: null } as Record<Which, Point | null>,
  /** Error the Backend attached to one point (e.g. point_not_on_network). */
  serverError: { origin: null, destination: null } as Record<Which, string | null>,
  pick: "origin" as Which | null,
  /** The route search on screen, kept so navigation can return to it. */
  result: null as {
    response: RouteResponse;
    request: RouteRequest;
    places: Record<Which, string | null>;
    /** Streets for the routes' "via" lines, filled in as they are found. */
    names: Partial<Record<RouteResult["role"], string>>;
    /** The route chosen in the list. */
    selected: RouteResult["role"];
  } | null,
  /** Why the result panel shows a notice (e.g. navigation could not start); rebuilt on a language switch. */
  resultNotice: null as Notice,
  busy: false,
  controller: null as AbortController | null,
  waitUntil: 0,
  heat: {
    on: false,
    userTurnedOff: false,
    layer: "combined" as HeatLayer,
  },
};

const mapView = new MapView($("map"));
mapView.mountZoomButtons(document.querySelector<HTMLElement>(".map-buttons")!);

// ------------------------------------------------------------------- splash
// The splash (index.html) leaves once the map has loaded, but stays at least 1.4 s from page
// start so its animation reads, and never longer than 6 s if the map is slow or fails.
const SPLASH_MIN_MS = 1_400;
const SPLASH_MAX_MS = 6_000;
let splashGone = false;
function hideSplash(): void {
  if (splashGone) return;
  splashGone = true;
  const splash = document.getElementById("splash");
  if (!splash) return;
  window.setTimeout(() => {
    splash.classList.add("is-leaving");
    window.setTimeout(() => splash.remove(), 500);
  }, Math.max(0, SPLASH_MIN_MS - performance.now()));
}
mapView.map.once("load", hideSplash);
window.setTimeout(hideSplash, SPLASH_MAX_MS);
// Handle for manual and browser-driven testing; stripped from production builds.
if (import.meta.env.DEV) (window as unknown as { __map: unknown }).__map = mapView.map;
const legend = new Legend($("legend"));

// -------------------------------------------------------------------- static
/** Texts that are not part of a view: index.html markup, fine print and mode labels. */
function applyStaticText(): void {
  translateDom();
  renderCredits();
  $("mode-walk-label").replaceChildren(icon("walk"), ` ${tr("Jalan kaki", "Walk")}`);
  $("mode-bike-label").replaceChildren(icon("bike"), ` ${tr("Sepeda", "Bike")}`);
  for (const b of Array.from(document.querySelectorAll<HTMLButtonElement>(".lang-btn"))) {
    b.setAttribute("aria-pressed", String(b.dataset.lang === getLang()));
  }
}
/** Credits for the basemap and OpenStreetMap data (roads, places, place search). Shown in the
 *  panel instead of on the map. */
function renderCredits(): void {
  const link = (href: string, text: string) => h("a", { href, target: "_blank", rel: "noopener noreferrer" }, text);
  const line = (...parts: (string | HTMLElement)[]) => h("p", {}, ...parts);
  // Panel hidden (desktop slid out, phone sheet closed or navigating): a short credit line on
  // the map instead, so the attribution stays visible (review PR #119, style.css).
  $("map-credits").replaceChildren(
    link("https://openfreemap.org", "OpenFreeMap"),
    " · ",
    link("https://www.openmaptiles.org/", "© OpenMapTiles"),
    " · ",
    link("https://www.openstreetmap.org/copyright", tr("© kontributor OpenStreetMap", "© OpenStreetMap contributors")),
  );
  $("panel-credits").replaceChildren(
    line(`© ${new Date().getFullYear()} SIPPolusi`),
    line(
      tr("Peta ", "Map "),
      link("https://openfreemap.org", "OpenFreeMap"),
      " · ",
      link("https://www.openmaptiles.org/", "© OpenMapTiles"),
      " · ",
      tr("Data © ", "Data © "),
      link("https://www.openstreetmap.org/copyright", tr("kontributor OpenStreetMap", "OpenStreetMap contributors")),
      " · ",
      tr("Pencarian tempat ", "Place search "),
      link("https://photon.komoot.io", "Photon (komoot)"),
    ),
  );
}

applyStaticText();
initTooltips();
$("swap-btn").append(icon("swap", 20));
$("locate-btn").append(icon("locate", 22));
heatToggle.append(icon("layers", 22));
for (const w of ["origin", "destination"] as const) {
  fields[w].pick.append(icon("pin", 20));
  fields[w].clear.append(icon("close", 18));
}

const mode = (): Mode => (tripForm.elements.namedItem("mode") as RadioNodeList).value === "bike" ? "bike" : "walk";
// The position marker shows the chosen mode's figure; while navigating, the route's own mode.
mapView.setUserMode(mode());
tripForm.addEventListener("change", (e) => {
  if (!nav.session) mapView.setUserMode(mode());
  // A new mode is another trip. (Text fields also fire "change" when they lose focus: those are
  // handled where their point changes, setPoint and onTyped.)
  if ((e.target as HTMLInputElement).name === "mode") cancelPendingSearch();
});
slider.addEventListener("change", cancelPendingSearch); // a new priority too (on release)

// -------------------------------------------------------------- form helpers
/** The trip changed while a search was pending (review PR #119): that search is for a trip that
 *  no longer exists, so it is dropped silently. Its response, if it still comes, is ignored
 *  (submit() checks state.controller), and the form is usable again at once. */
function cancelPendingSearch(): void {
  const controller = state.controller;
  if (!controller) return;
  state.controller = null;
  controller.abort();
  state.busy = false;
  resultView.removeAttribute("aria-busy");
  resultView.inert = false;
  clearStatus();
  refreshForm();
}

function setPoint(which: Which, p: Point | null, opts: { fill?: boolean; fly?: boolean; label?: string } = {}): void {
  // A route belongs to the points it was found for: once one moves (pin dragged, map clicked,
  // place chosen, own location), the old route and its list are removed rather than left
  // drawn between points that no longer match.
  const old = state.points[which];
  const moved = !old || !p || old.lat !== p.lat || old.lon !== p.lon;
  if (moved) cancelPendingSearch();
  if (state.result && moved && !nav.session) dropResult();
  state.points[which] = p;
  state.labels[which] = opts.label ?? null;
  state.parseError[which] = false;
  state.serverError[which] = null;
  if (p) state.touched[which] = true;
  searches[which].stop();
  namers[which].cancel();
  state.naming[which] = null;
  // A map point gets its name looked up; meanwhile the field shows "Memuat lokasi…" rather than
  // coordinates. Points outside the study area are never sent, so they keep their coordinates.
  const named = !!p && !opts.label && opts.fill !== false && !validatePoint(p, "asal");
  if (opts.fill !== false) fields[which].input.value = opts.label ?? (p && !named ? fmtCoord(p) : "");
  fields[which].input.title = p ? fmtCoord(p) : "";
  mapView.setPoint(which, p);
  if (p && opts.fly) mapView.flyTo(p);
  if (named) startNaming(which, p!);
  else if (p && !opts.label) nameIfUsable(which, p);
  refreshForm();
}

function startNaming(which: Which, p: Point): void {
  state.naming[which] = p;
  namers[which].name(p);
}

/** Looks up the name of a map point. Points outside the study area are never sent. */
function nameIfUsable(which: Which, p: Point): void {
  if (!validatePoint(p, "asal")) namers[which].name(p);
}

/** A found name fills the field, unless the user has moved on in the meantime. */
function showPointName(which: Which, place: Place, p: Point): void {
  const { input } = fields[which];
  if (state.naming[which] === p) {
    state.naming[which] = null;
    state.labels[which] = place.name;
    input.value = place.name;
    refreshForm();
    return;
  }
  // A point shown with its coordinates (e.g. after a swap) also takes the name.
  if (state.points[which] !== p || input.value !== fmtCoord(p)) return;
  state.labels[which] = place.name;
  input.value = place.name;
}

/** No name found: the field shows the point's coordinates after all. */
function pointNameMissed(which: Which, p: Point): void {
  if (state.naming[which] !== p) return;
  state.naming[which] = null;
  fields[which].input.value = fmtCoord(p);
  refreshForm();
}

function choosePlace(which: Which, place: Place): void {
  // Same step as a map click: the next click on the map must not move this point.
  if (state.pick === which) state.pick = which === "origin" && !state.points.destination ? "destination" : null;
  setPoint(which, { lat: place.lat, lon: place.lon }, { fly: true, label: place.name });
}

function currentErrors(): Record<Which, string | null> {
  const trip = validateTrip(state.points.origin, state.points.destination, mode());
  const out: Record<Which, string | null> = { origin: trip.origin, destination: trip.destination };
  for (const w of ["origin", "destination"] as const) {
    if (state.parseError[w]) {
      out[w] = tr(
        'Pilih salah satu saran atau pilih titik di peta.',
        'Choose one of the suggestions or pick a point on the map.',
      );
    }
  }
  return out;
}

function refreshForm(): void {
  const errors = currentErrors();
  const valid = !errors.origin && !errors.destination;
  for (const w of ["origin", "destination"] as const) {
    const f = fields[w];
    const shown = state.serverError[w] ?? (state.touched[w] ? errors[w] : null);
    f.error.hidden = !shown;
    f.error.textContent = shown ?? "";
    f.wrap.classList.toggle("is-invalid", !!shown);
    f.input.setAttribute("aria-invalid", String(!!shown));
    f.pick.setAttribute("aria-pressed", String(state.pick === w));
    f.clear.hidden = f.input.value === "" && !state.naming[w];
    f.wrap.classList.toggle("is-naming", !!state.naming[w]);
    f.input.placeholder = state.naming[w]
      ? tr("Memuat lokasi…", "Loading location…")
      : tr(f.input.getAttribute("data-id-placeholder") ?? "Cari tempat atau klik peta", f.input.dataset.enPlaceholder ?? "Search a place or click the map");
  }

  const waiting = state.waitUntil > Date.now();
  submitBtn.disabled = !valid || state.busy || waiting;
  // While searching the button is disabled and shows only a spinner; its name stays for screen readers.
  if (state.busy) {
    submitBtn.replaceChildren(
      h("span", { class: "spinner", "aria-hidden": "true" }),
      h("span", { class: "visually-hidden" }, tr("Mencari rute…", "Finding routes…")),
    );
  } else {
    submitBtn.textContent = tr("Cari rute", "Find routes");
  }
  submitBtn.classList.toggle("is-busy", state.busy);
  submitBtn.setAttribute("aria-busy", String(state.busy));
  cancelBtn.hidden = !state.busy;
  // Only the rate-limit wait needs saying; an incomplete form shows on its fields.
  submitHelp.textContent =
    !state.busy && waiting
      ? tr(
          `Tunggu ${Math.ceil((state.waitUntil - Date.now()) / 1000)} detik sebelum mencari lagi.`,
          `Wait ${Math.ceil((state.waitUntil - Date.now()) / 1000)} seconds before searching again.`,
        )
      : "";

  pickHint.textContent = state.pick
    ? state.pick === "origin"
      ? tr("Klik peta untuk memilih titik asal.", "Click the map to pick the starting point.")
      : tr("Klik peta untuk memilih titik tujuan.", "Click the map to pick the destination.")
    : "";
  mapView.setPickCursor(state.pick !== null);
}

function onTyped(which: Which): void {
  cancelPendingSearch(); // typing changes the trip's point
  const text = fields[which].input.value;
  const p = parseCoordinateText(text);
  const isSearch = text.trim() !== "" && !p;
  state.serverError[which] = null;
  state.parseError[which] = isSearch;
  state.points[which] = p;
  state.labels[which] = null;
  state.naming[which] = null;
  namers[which].cancel();
  fields[which].input.title = "";
  // Still typing a name is not a mistake: the message waits until the field is left.
  if (isSearch) {
    state.touched[which] = false;
    searches[which].schedule(text);
  } else {
    searches[which].stop();
  }
  mapView.setPoint(which, p);
  refreshForm();
}

const searches = {} as Record<Which, PlaceSearch>;
const namers = {} as Record<Which, PointNamer>;
for (const w of ["origin", "destination"] as const) {
  const combobox = new PlaceCombobox(
    fields[w].input,
    $(`menu-${w}`),
    $(`list-${w}`),
    $(`status-${w}`),
    (place) => choosePlace(w, place),
    () => searches[w].stop(),
  );
  searches[w] = new PlaceSearch(searchPlaces, (view) => combobox.render(view), GEOCODE_DEBOUNCE_MS, GEOCODE_MIN_CHARS);
  namers[w] = new PointNamer(reversePlace, (place, p) => showPointName(w, place, p), (p) => pointNameMissed(w, p));
}

for (const w of ["origin", "destination"] as const) {
  const f = fields[w];
  f.input.addEventListener("input", () => onTyped(w));
  // While a field has the cursor, a click on the map sets its point (no need for the pin button).
  f.input.addEventListener("focus", () => {
    if (state.pick === w) return;
    state.pick = w;
    refreshForm();
  });
  f.input.addEventListener("blur", (e) => {
    searches[w].stop();
    // Focus moving to the map (a click there sets this point) or to this field's own buttons
    // (clear, pick on map) means the user is still choosing: judging the field now would flash
    // an error for a moment before the point arrives. Any other move counts as leaving it, and
    // ends picking on the map for this field.
    const to = e.relatedTarget as Node | null;
    const stillChoosing = !!to && (f.wrap.contains(to) || $("map").contains(to));
    if (!stillChoosing) {
      state.touched[w] = true;
      if (state.pick === w) state.pick = null;
    }
    refreshForm();
  });
  // Empties the field and its point, and leaves the cursor there for a new place.
  const clearField = () => {
    dropResult();
    setPoint(w, null);
    state.touched[w] = false;
    refreshForm();
    f.input.focus();
  };
  f.clear.addEventListener("click", clearField);
  // Escape clears the field, but only when no suggestion list is open: then Escape closes the
  // list instead (the combobox handles it first and marks the event, WAI-ARIA combobox pattern).
  f.input.addEventListener("keydown", (e) => {
    if (e.key !== "Escape" || e.defaultPrevented) return;
    if (f.input.value !== "") {
      e.preventDefault();
      clearField();
    } else if (state.pick === w) {
      // An empty field: Escape stops picking on the map.
      e.preventDefault();
      state.pick = null;
      refreshForm();
    }
  });
  // Arms picking on the map for this field (focusing the field does the same).
  f.pick.addEventListener("click", () => {
    state.pick = w;
    refreshForm();
  });
}

$("swap-btn").addEventListener("click", () => {
  cancelPendingSearch(); // the trip is reversed
  const { origin, destination } = state.points;
  const texts = [fields.origin.input.value, fields.destination.input.value];
  const titles = [fields.origin.input.title, fields.destination.input.title];
  for (const w of ["origin", "destination"] as const) {
    searches[w].stop();
    namers[w].cancel();
  }
  state.points = { origin: destination, destination: origin };
  state.labels = { origin: state.labels.destination, destination: state.labels.origin };
  state.parseError = { origin: state.parseError.destination, destination: state.parseError.origin };
  state.naming = { origin: state.naming.destination, destination: state.naming.origin };
  state.serverError = { origin: null, destination: null };
  fields.origin.input.value = texts[1];
  fields.destination.input.value = texts[0];
  fields.origin.input.title = titles[1];
  fields.destination.input.title = titles[0];
  mapView.setPoint("origin", state.points.origin);
  mapView.setPoint("destination", state.points.destination);
  // A lookup cancelled above goes on for the field that holds the point now.
  for (const w of ["origin", "destination"] as const) {
    const p = state.points[w];
    if (p && state.naming[w]) startNaming(w, p);
    else if (p && !state.labels[w] && fields[w].input.value === fmtCoord(p)) nameIfUsable(w, p);
  }
  refreshForm();
});

tripForm.addEventListener("change", refreshForm);

function updatePriority(): void {
  // The slider runs from "Lebih cepat" (left) to "Lebih bersih" (right).
  const { alpha, beta } = preferenceFromSlider(Number(slider.value));
  $("priority-text").textContent = tr(
    `Bobot waktu tempuh ${fmtNumber(alpha, 2)}, bobot paparan ${fmtNumber(beta, 2)}.`,
    `Travel-time weight ${fmtNumber(alpha, 2)}, exposure weight ${fmtNumber(beta, 2)}.`,
  );
  slider.style.setProperty("--fill", `${Number(slider.value) * 100}%`);
  showPriorityZone();
}

/** Darkens the label of the zone the handle is in (faster < 1/3 ≤ balanced ≤ 2/3 < cleaner),
 *  and pops the handle when the zone changes (style.css). */
let priorityZone = -1;
function showPriorityZone(): void {
  const v = Number(slider.value);
  const zone = v < 1 / 3 ? 0 : v > 2 / 3 ? 2 : 1;
  document.querySelectorAll(".priority-scale span").forEach((s, i) => s.classList.toggle("is-current", i === zone));
  if (priorityZone !== -1 && zone !== priorityZone) {
    slider.classList.remove("zone-pop");
    void slider.offsetWidth; // restart the animation when zones change quickly
    slider.classList.add("zone-pop");
    // A timer, not animationend: the animation runs on the handle (a pseudo-element), whose
    // end event browsers do not reliably send.
    window.clearTimeout(zonePopTimer);
    zonePopTimer = window.setTimeout(() => slider.classList.remove("zone-pop"), 340);
  }
  priorityZone = zone;
}
let zonePopTimer = 0;
slider.addEventListener("input", updatePriority);
updatePriority();

// ---------------------------------------------------------------- map events
// While navigating the pins cannot be dragged (MapView.setPointsDraggable); the guard covers a drag
// that started just before navigation did.
mapView.onPointMoved = (which, p) => {
  if (!nav.session) setPoint(which, p);
};

// A click on the map picks a point when the form asks for one; the heatmap itself is not clickable.
mapView.onClick = ({ point }: SegmentClick) => {
  // The trip is locked while navigating: a map click must not move its points.
  if (!state.pick || nav.session) return;
  const which = state.pick;
  state.pick = which === "origin" && !state.points.destination ? "destination" : null;
  setPoint(which, point);
};

// ---------------------------------------------------------------- route flow
function showFormView(): void {
  formView.hidden = false;
  resultView.hidden = !state.result;
  navViewEl.hidden = true;
}

/** Height of the bottom sheet over the map on phones; 0 beside it on wider screens. */
function panelInset(): { bottom: number } {
  return { bottom: window.matchMedia("(max-width: 899px)").matches ? panel.offsetHeight + 16 : 0 };
}

/** Space the navigation banner and bottom card cover, so the camera keeps the user between them. */
function navInset(): { top: number; bottom: number } {
  const i = nav.view?.insets() ?? { top: 0, bottom: 0 };
  return { top: i.top, bottom: i.bottom + 16 };
}

/** A notice shown above the routes, built when shown so it follows the interface language. */
type Notice = (() => { title: string; text: string }) | null;

/** Renders state.result below the trip form and returns its heading. */
function showResultView(notice: Notice = null): HTMLElement | null {
  const r = state.result;
  state.resultNotice = notice;
  if (!r) return null;
  formView.hidden = false;
  // Redrawn mid-trip (a language switch): the navigation view over the map stays.
  navViewEl.hidden = !nav.session;
  resultView.hidden = false;
  return renderResult(resultView, {
    ...r,
    onNavigate: (role) => void startNavigation(role),
    onSelect: (role) => {
      r.selected = role;
      void mapView.selectRoute(role);
    },
    notice: notice?.() ?? null,
  });
}

/** Looks up a street near the middle of each route for its "via" line (one reverse-geocoding
 *  request per route). Failures leave the route's own title in place. */
function nameRoutes(result: NonNullable<typeof state.result>): void {
  const { response } = result;
  const routes = compareRoutes(response).kind === "identical" || !response.comparison ? [response.recommended] : [response.recommended, response.comparison];
  for (const route of routes) {
    const mid = routeMidpoint(route.coordinates);
    if (!mid) continue;
    reversePlace(mid)
      .then((place) => {
        const street = streetFromPlace(place);
        if (!street || state.result !== result) return;
        result.names[route.role] = street;
        if (!resultView.hidden) setRouteVia(resultView, route.role, street);
      })
      .catch(() => {});
  }
}

/** Draws the form status; kept so a language switch can draw it again. */
let statusRender: (() => void) | null = null;

function setStatus(render: (() => void) | null): void {
  statusRender = render;
  clear(formStatus);
  render?.();
}

function clearStatus(): void {
  setStatus(null);
}

/** The spinner is in the button; the live region only tells screen readers the search started. */
function showStatusLoading(): void {
  setStatus(() => formStatus.append(h("p", { class: "visually-hidden" }, tr("Mencari rute…", "Finding routes…"))));
}

function statusNote(text: () => string): void {
  setStatus(() => formStatus.append(h("p", { class: "status-note" }, text())));
}

function showError(err: unknown): void {
  setStatus(() => drawError(describeRouteError(err)));
}

/** The error's message only: the main button below already searches again. */
function drawError(view: ErrorView): void {
  // Just the message, as plain text like the field errors: it says what happened on its own.
  formStatus.append(h("p", { class: "status-error", role: "alert" }, view.message));
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
  state.result = null;
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
  // The place names belong to this request: captured now, not when the response arrives, when
  // the form may already describe another trip.
  const places = { ...state.labels };
  const controller = new AbortController();
  state.controller = controller;
  state.busy = true;
  state.serverError = { origin: null, destination: null };
  state.pick = null;
  showStatusLoading();
  refreshForm();

  // Routes already shown stay, dimmed and marked busy, until the new ones replace them; they
  // are removed if this search fails, so nothing from an earlier trip outlives an error.
  const hadResult = !!state.result;
  if (hadResult) {
    resultView.setAttribute("aria-busy", "true");
    // Out of reach for keyboard and pointer alike: an old route must not start navigation while
    // its replacement is on the way (review PR #119).
    resultView.inert = true;
  }

  try {
    const response = await submitRouteRequest(request, controller.signal);
    if (state.controller !== controller) return;
    state.busy = false;
    resultView.removeAttribute("aria-busy");
    resultView.inert = false;
    if (hadResult) void mapView.clearRoutes();
    nav.exitLine = null; // a new trip: the heatmap follows the map view again
    state.result = { response, request, places, names: {}, selected: "recommended" };
    const found = showResultView()!.textContent ?? "";
    // Focus stays on the form; screen readers hear how many routes came.
    setStatus(() => formStatus.append(h("p", { class: "visually-hidden" }, found)));
    void mapView.showRoutes(response, panelInset());
    void mapView.selectRoute("recommended");
    nameRoutes(state.result);
    refreshForm();
    // Exposure surface is requested once the route is displayed (Tabel 3.4).
    // Debounced, so it fires once after the map has finished fitting to the route.
    if (!state.heat.userTurnedOff) setHeat(true, false);
    else if (state.heat.on) scheduleSurface();
  } catch (err) {
    if (state.controller !== controller) return;
    state.busy = false;
    resultView.removeAttribute("aria-busy");
    resultView.inert = false;
    if (hadResult) dropResult(); // they belong to the previous trip; the form shows the error
    if (err instanceof ApiError && err.kind === "aborted") {
      statusNote(() => tr("Pencarian dibatalkan.", "Search cancelled."));
    } else {
      const view = describeRouteError(err);
      // An error about one point is shown on that field only (Gambar 3.2c).
      if (view.field) clearStatus();
      else showError(err);
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

tripForm.addEventListener("submit", (e) => {
  e.preventDefault();
  void submit();
});
cancelBtn.addEventListener("click", () => state.controller?.abort());

// ------------------------------------------------------------------- heatmap
/** The area the heatmap is asked for. While navigating, the camera follows the user, so its
 *  bounds would give the position away: the route's area is used instead, since its ends are
 *  what the route request sent. */
function heatArea(): BBox {
  // Just after navigation the camera may still show where the last GPS fix was, so the route's
  // area is asked for until the user moves the map by hand or searches again (review PR #119).
  if (nav.session) return routeArea(nav.session.routeLine);
  if (nav.exitLine) return routeArea(nav.exitLine);
  return mapView.getView().bbox;
}

const heatSurface = new SurfaceRefresher(
  (signal) => {
    const bbox = heatArea();
    return requestExposureSurface(bbox, surfaceZoom(mapView.getView().zoom, bbox), signal);
  },
  (status, surface) => {
    // Grey skeleton cells over the area while its data loads; gone once it lands or fails.
    void mapView.showHeatSkeleton(status === "loading" && state.heat.on ? heatArea() : null);
    if (status === "ready" && surface) {
      void mapView.setSurface(surface);
      void mapView.setHeatDimmed(false);
    } else if (status === "error") {
      // The previous heatmap stays (route is unaffected, Tabel 3.15) but is dimmed
      // and labelled as old so it cannot pass for current data.
      void mapView.setHeatDimmed(true);
    }
    legendState();
  },
  SURFACE_DEBOUNCE_MS,
);

function legendState(): void {
  const hs = state.heat;
  const surface = heatSurface.surface;
  legend.setHeat({
    visible: hs.on,
    layer: hs.layer,
    status: heatSurface.status,
    stale: surface?.dataStale ?? false,
    showingOldData: !!surface && surface.features.length > 0,
  });
}

function setHeat(on: boolean, immediate = true): void {
  const hs = state.heat;
  hs.on = on;
  heatToggle.setAttribute("aria-pressed", String(on));
  heatToggle.setAttribute("aria-label", on ? tr("Sembunyikan heatmap paparan", "Hide exposure heatmap") : tr("Tampilkan heatmap paparan", "Show exposure heatmap"));
  void mapView.showHeat(on);
  if (on) heatSurface.schedule(immediate);
  else {
    heatSurface.stop();
    void mapView.showHeatSkeleton(null);
  }
  legendState();
}

function scheduleSurface(immediate = false): void {
  if (state.heat.on) heatSurface.schedule(immediate);
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
// Not while navigating: the route's area does not change when the camera follows the user.
mapView.onViewChange = () => {
  if (!nav.session) scheduleSurface();
};

/** The bounding box of a route line, with a margin so the heatmap reaches past its ends. */
function routeArea(line: RouteLine): BBox {
  const margin = 0.003; // about 330 m
  const lats = line.points.map((p) => p.lat);
  const lons = line.points.map((p) => p.lon);
  return {
    minLon: Math.min(...lons) - margin,
    minLat: Math.min(...lats) - margin,
    maxLon: Math.max(...lons) + margin,
    maxLat: Math.max(...lats) + margin,
  };
}

// ------------------------------------------------------------------ geolocate
const insideStudyArea = (p: Point) =>
  p.lat >= STUDY_AREA.minLat && p.lat <= STUDY_AREA.maxLat && p.lon >= STUDY_AREA.minLon && p.lon <= STUDY_AREA.maxLon;

/** "Lokasi saya" on the map: moves the camera to the user's position, leaving the form as it is. */
function showMyLocation(): void {
  clearStatus();
  if (here.last) {
    mapView.flyTo(here.last);
    return;
  }
  const say = (text: () => string) => {
    showFormView();
    statusNote(text);
  };
  const problem = positionProblem();
  if (problem) return say(() => describePositionProblem(problem));
  navigator.geolocation.getCurrentPosition(
    (pos) => {
      if (nav.session) return;
      const p = { lat: pos.coords.latitude, lon: pos.coords.longitude };
      void mapView.setUser(p, null, pos.coords.accuracy);
      mapView.flyTo(p);
      startShowingLocation(); // permission was just granted: keep the marker up to date from now on
    },
    (err) => {
      if (!nav.session) say(() => describePositionProblem(problemFromCode(err.code)));
    },
    { enableHighAccuracy: true, timeout: 10_000, maximumAge: 60_000 },
  );
}

/** "Gunakan lokasi saya sebagai asal" on the form: fills in the starting point. */
function useMyLocation(): void {
  clearStatus();
  const say = (text: () => string) => {
    showFormView();
    statusNote(text);
  };
  const pickOnMap = () => tr("Pilih titik asal di peta.", "Pick the starting point on the map.");
  const problem = positionProblem();
  if (problem) return say(() => `${describePositionProblem(problem)} ${pickOnMap()}`);
  // Navigation may have started while this request was pending; it must not touch the form then.
  navigator.geolocation.getCurrentPosition(
    (pos) => {
      if (nav.session) return;
      const p = { lat: pos.coords.latitude, lon: pos.coords.longitude };
      if (!insideStudyArea(p)) {
        say(() =>
          tr(
            "Lokasi Anda berada di luar wilayah studi (Jakarta), jadi tidak dipakai sebagai titik asal.",
            "Your location is outside the study area (Jakarta), so it is not used as the starting point.",
          ),
        );
        return;
      }
      state.pick = state.points.destination ? null : "destination";
      setPoint("origin", p, { fly: true });
    },
    (err) => {
      if (!nav.session) say(() => `${describePositionProblem(problemFromCode(err.code))} ${pickOnMap()}`);
    },
    { enableHighAccuracy: true, timeout: 10_000, maximumAge: 60_000 },
  );
}

$("locate-btn").addEventListener("click", () => {
  // While navigating, the same button brings the camera back to the user.
  if (nav.session) return recenter();
  showMyLocation();
});
$("use-location").addEventListener("click", useMyLocation);

// ---------------------------------------------------------------- navigation
// TI-SE-13. Positions are handled on this device only. The one time a position leaves it
// is the origin of a new route after leaving the route (POST body, never logged or stored
// by the Backend); nothing is kept once navigation ends (PNF-06, RI-09).

/** Development only: `?simulasi=normal|keluar|lemah|tolak|tanpa-gps` replaces the GPS. */
const SIMULATION = import.meta.env.DEV ? new URLSearchParams(location.search) : null;

/** Development only: `?lokasi=monas|bundaran-hi|sudirman|blok-m|kota|<lat,lon>` reports that place as the device's position. */
const devLocationReady: Promise<void> =
  import.meta.env.DEV && SIMULATION?.get("lokasi")
    ? import("./dev-location").then((dev) => {
        const p = dev.parseDevLocation(SIMULATION.get("lokasi"));
        if (p) dev.useFakeLocation(p);
        else console.warn(`[dev] ?lokasi tidak dikenal; pakai salah satu dari ${Object.keys(dev.DEV_PLACES).join(", ")} atau "lat,lon"`);
      })
    : Promise.resolve();

const nav = {
  session: null as NavigationSession | null,
  source: null as PositionSource | null,
  view: null as NavView | null,
  role: "recommended" as RouteResult["role"],
  controller: null as AbortController | null,
  following: true,
  firstFix: true,
  /** Replaces the status message until `clearOn` happens. */
  message: null as { make: () => NavMessage; view: NavMessage; clearOn: "fix" | "on_route" | number } | null,
  wakeLock: null as WakeLockSentinel | null,
  /** The heatmap was on when navigation started: it comes back when navigation ends. */
  heatWasOn: false,
  /** The last navigated route, while its area stands in for the camera's (heatArea). */
  exitLine: null as RouteLine | null,
};

/** The user's position, shown on the map whenever the browser allows it. Navigation draws its own
 *  marker, so this watch pauses while navigating. Kept in memory only, never sent anywhere. */
const here = { source: null as DevicePosition | null, last: null as Point | null };

function startShowingLocation(): void {
  if (here.source || nav.session || positionProblem()) return;
  const source = new DevicePosition();
  here.source = source;
  source.start(
    (fix) => {
      here.last = { lat: fix.lat, lon: fix.lon };
      if (!nav.session) void mapView.setUser(here.last, null, fix.accuracyM);
    },
    // Denied or unsupported: nothing more will come. The locate button can ask again.
    (problem) => {
      if (isFatal(problem)) stopShowingLocation();
    },
  );
}

function stopShowingLocation(): void {
  here.source?.stop();
  here.source = null;
}

void devLocationReady.then(startShowingLocation);

async function positionSource(): Promise<PositionSource> {
  const scenario = SIMULATION?.get("simulasi") ?? null;
  if (import.meta.env.DEV && scenario) {
    const sim = await import("./position-sim");
    if (sim.isSimScenario(scenario)) {
      const speed = Number(SIMULATION?.get("kecepatan")) || undefined;
      return new sim.SimulatedPosition(() => nav.session!.routeLine, scenario, speed);
    }
  }
  return new DevicePosition();
}

function navRoute(response: RouteResponse): RouteResult {
  return nav.role === "comparison" && response.comparison ? response.comparison : response.recommended;
}

async function startNavigation(role: RouteResult["role"]): Promise<void> {
  const r = state.result;
  // Not while a new search is pending: its response would replace the trip under the session.
  if (!r || nav.session || state.busy) return;
  const problem = positionProblem();
  if (problem) {
    showResultView(() => ({
      title: tr("Navigasi tidak dapat dimulai", "Navigation could not start"),
      text: `${describePositionProblem(problem)} ${tr("Rute tetap ditampilkan.", "The route stays on screen.")}`,
    }))?.focus();
    return;
  }
  stopShowingLocation(); // navigation's own watch takes over the marker
  // A clear map while navigating: the heatmap goes away (the button can still bring it back).
  nav.heatWasOn = state.heat.on;
  if (state.heat.on) setHeat(false);
  nav.role = role;
  const route = navRoute(r.response);
  const session = new NavigationSession(route, r.request.destination, r.request.mode);
  mapView.setUserMode(r.request.mode);
  nav.session = session;
  nav.following = true;
  nav.firstFix = true;
  nav.message = null;
  nav.view = new NavView(navViewEl, { onEnd: () => endNavigation(), onRecenter: recenter });
  mapView.closePopup();
  navViewEl.hidden = false;
  // The navigation view lies over the map. On a desktop the panel stays beside it, showing the
  // trip read-only; on a phone it steps aside so the map fills the screen (style.css).
  $("app").classList.add("is-navigating");
  $("panel-body").inert = true;
  void mapView.showNavRoute(session.routeLine, 0);
  mapView.setPointsDraggable(false);
  setLocateLabel(true);
  renderNav();
  nav.view.title.focus({ preventScroll: true });
  void keepScreenOn(true);

  const source = await positionSource();
  if (nav.session !== session) return; // ended while the simulator loaded
  nav.source = source;
  source.start(onFix, onPositionProblem);
  mountSimPanel();
}

/** Development only: a panel over the map to leave the route and come back on demand, to test
 *  the off-route case (banner "Kembali ke rute", then a new route from the position). */
function mountSimPanel(): void {
  if (import.meta.env.DEV) {
    void import("./position-sim").then((sim) => {
      if (!nav.session || navViewEl.querySelector(".sim-panel")) return;
      const isAway = () => nav.source instanceof sim.SimulatedPosition && nav.source.isDetoured;
      navViewEl.append(sim.simPanel((away) => void simDetour(away), isAway));
    });
  }
}

/** Development only: walks the simulated position off the route (true) or back (false). On the
 *  device's GPS it first switches to the simulator at the current point on the route. */
async function simDetour(away: boolean): Promise<void> {
  if (import.meta.env.DEV) {
    const sim = await import("./position-sim");
    const session = nav.session;
    if (!session) return;
    let source = nav.source;
    if (!(source instanceof sim.SimulatedPosition)) {
      const line = session.routeLine;
      const along = Math.min(line.length, Math.max(0, line.length - session.snapshot.remainingM));
      source?.stop();
      const speed = Number(SIMULATION?.get("kecepatan")) || undefined;
      source = new sim.SimulatedPosition(() => nav.session!.routeLine, "normal", speed, 1_000, along);
      nav.source = source;
      source.start(onFix, onPositionProblem);
    }
    (source as InstanceType<typeof sim.SimulatedPosition>).setDetour(away);
  }
}

/** Ends navigation and returns to the route result. `notice` says why, when it was not the user. */
function endNavigation(notice: Notice = null): void {
  nav.exitLine = nav.session?.routeLine ?? null;
  nav.source?.stop();
  nav.source = null;
  nav.controller?.abort();
  nav.controller = null;
  nav.session = null;
  nav.view = null;
  nav.message = null;
  void keepScreenOn(false);
  // Back to the plain position marker (the last navigation position may be simulated or snapped).
  mapView.setUserMode(mode());
  void mapView.setUser(here.last);
  startShowingLocation();
  if (nav.heatWasOn && !state.heat.on) setHeat(true);
  nav.heatWasOn = false;
  $("app").classList.remove("is-navigating");
  $("panel-body").inert = false;
  mapView.resetCamera();
  mapView.setPointsDraggable(true);
  setLocateLabel(false);
  clear(navViewEl);
  const title = showResultView(notice);
  if (state.result) {
    void mapView.showRoutes(state.result.response, panelInset());
    void mapView.selectRoute(state.result.selected);
  }
  title?.focus({ preventScroll: true });
}

function onFix(fix: Fix): void {
  const session = nav.session;
  if (!session || session.isArrived) return;
  const snap = session.update(fix);
  const m = nav.message;
  if (m && (m.clearOn === "fix" || (m.clearOn === "on_route" && snap.status === "tracking"))) nav.message = null;

  void mapView.setUser(snap.shown, snap.headingDeg, fix.accuracyM);
  void mapView.showNavRoute(session.routeLine, session.progressM);
  if (nav.following && snap.shown) mapView.follow(snap.shown, snap.headingDeg, navInset(), nav.firstFix ? 17.5 : undefined);
  nav.firstFix = false;

  if (snap.status === "arrived") {
    stopTracking();
  } else if (snap.status === "tracking" && nav.controller) {
    // Back on the route before the new one arrived: it is no longer needed. A weak fix
    // says nothing about that, so it leaves the request running.
    nav.controller.abort();
  } else if (session.rerouteDue(Date.now())) {
    void reroute(session, fix);
  }
  renderNav();
}

/** On arrival, however it was reached (a fix, or a new route that ends where the user is):
 *  tracking stops at once, and the panel says so until the user closes it. */
function stopTracking(): void {
  nav.source?.stop();
  nav.source = null;
  nav.controller?.abort();
  nav.message = null; // a pending reroute error must not hide the arrival
  void keepScreenOn(false);
}

/** A new route from the current position, at most once per NAV_LIMITS.rerouteIntervalS. */
async function reroute(session: NavigationSession, from: Point): Promise<void> {
  const r = state.result;
  if (!r) return;
  if (!insideStudyArea(from)) {
    const wait = session.rerouteFailed(Date.now());
    setNavMessage(
      () => ({
        kind: "error",
        title: tr("Rute baru tidak dapat dihitung", "A new route cannot be calculated"),
        text: tr(
          `Anda berada di luar wilayah studi (Jakarta). Dicoba lagi dalam ${wait} detik.`,
          `You are outside the study area (Jakarta). Trying again in ${wait} seconds.`,
        ),
      }),
      "on_route",
    );
    return;
  }
  session.rerouteStarted(Date.now());
  const controller = new AbortController();
  nav.controller = controller;
  setNavMessage(
    () => ({
      kind: "warn",
      title: tr("Anda keluar dari rute", "You left the route"),
      text: tr("Mencari rute baru dari posisi Anda…", "Finding a new route from your position…"),
    }),
    "on_route",
  );
  try {
    const response = await submitRouteRequest({ ...r.request, origin: { lat: from.lat, lon: from.lon } }, controller.signal);
    if (nav.session !== session) return;
    const route = navRoute(response);
    session.replaceRoute(route, Date.now());
    nav.source?.routeChanged?.();
    void mapView.showNavRoute(session.routeLine, session.progressM);
    if (session.isArrived) {
      stopTracking();
    } else {
      setNavMessage(
        () => ({
          kind: "info",
          title: tr("Rute diperbarui", "Route updated"),
          text: tr("Rute baru dihitung dari posisi Anda.", "A new route was calculated from your position."),
        }),
        Date.now() + 6_000,
      );
      scheduleSurface(true); // the heatmap follows the new route's area
    }
  } catch (err) {
    if (nav.session !== session) return;
    if (err instanceof ApiError && err.kind === "aborted") {
      session.rerouteCancelled();
      nav.message = null;
    } else {
      // The title only: the messages of the route form ("Tidak ada hasil yang ditampilkan")
      // are not true here, where the previous route stays on the map.
      const wait = session.rerouteFailed(Date.now(), describeRouteError(err).waitSec);
      setNavMessage(
        () => ({
          kind: "error",
          title: tr("Rute baru belum didapat", "No new route yet"),
          text: tr(
            `${describeRouteError(err).title}. Rute sebelumnya tetap ditampilkan; dicoba lagi dalam ${wait} detik bila Anda masih di luar rute.`,
            `${describeRouteError(err).title}. The previous route stays on screen; trying again in ${wait} seconds if you are still off the route.`,
          ),
        }),
        "on_route",
      );
    }
  } finally {
    if (nav.controller === controller) nav.controller = null;
    renderNav();
  }
}

function onPositionProblem(problem: PositionProblem): void {
  if (!nav.session) return;
  if (isFatal(problem)) {
    const started = nav.session.snapshot.status !== "waiting";
    endNavigation(() => ({
      title: started ? tr("Navigasi dihentikan", "Navigation stopped") : tr("Navigasi tidak dapat dimulai", "Navigation could not start"),
      text: `${describePositionProblem(problem)} ${tr("Rute tetap ditampilkan.", "The route stays on screen.")}`,
    }));
    return;
  }
  // Unavailable or timed out: the watch keeps trying, and the next fix clears this.
  setNavMessage(
    () => ({
      kind: "warn",
      gps: true,
      title: problem === "timeout" ? tr("Sinyal GPS lemah", "Weak GPS signal") : tr("GPS tidak tersedia", "GPS unavailable"),
      text: tr("Mencoba lagi…", "Trying again…"),
    }),
    "fix",
  );
  renderNav();
}

function setNavMessage(make: () => NavMessage, clearOn: "fix" | "on_route" | number): void {
  const entry = { make, view: make(), clearOn };
  nav.message = entry;
  if (typeof clearOn === "number") {
    window.setTimeout(() => {
      if (nav.message === entry) {
        nav.message = null;
        renderNav();
      }
    }, clearOn - Date.now());
  }
}

function setLocateLabel(navigating: boolean): void {
  const label = navigating ? tr("Ikuti posisi saya", "Follow my position") : tr("Tampilkan lokasi saya", "Show my location");
  $("locate-btn").setAttribute("aria-label", label);
  $("locate-btn").title = navigating ? tr("Ikuti posisi", "Follow position") : tr("Lokasi saya", "My location");
}

function recenter(): void {
  nav.following = true;
  const snap = nav.session?.snapshot;
  if (snap?.shown) mapView.follow(snap.shown, snap.headingDeg, navInset());
  renderNav();
}

function renderNav(): void {
  const r = state.result;
  if (!nav.session || !nav.view || !r) return;
  nav.view.render({
    snapshot: nav.session.snapshot,
    destination: r.places.destination ?? fmtCoord(r.request.destination),
    message: nav.message?.view ?? null,
    following: nav.following,
  });
}

/** Keeps the screen on while navigating, where the browser allows it. */
async function keepScreenOn(on: boolean): Promise<void> {
  if (!on) {
    const lock = nav.wakeLock;
    nav.wakeLock = null;
    await lock?.release().catch(() => {});
    return;
  }
  if (nav.wakeLock || !("wakeLock" in navigator)) return;
  const session = nav.session;
  let lock: WakeLockSentinel;
  try {
    lock = await navigator.wakeLock.request("screen");
  } catch {
    return; // refused or unsupported: navigation works without it
  }
  // Navigation may have ended or arrived, or another request been granted, while this one was pending.
  if (!session || nav.session !== session || session.isArrived || nav.wakeLock) {
    await lock.release().catch(() => {});
    return;
  }
  nav.wakeLock = lock;
}
document.addEventListener("visibilitychange", () => {
  // The browser releases the lock when the page is hidden.
  if (document.visibilityState === "visible" && nav.session && !nav.session.isArrived) {
    nav.wakeLock = null;
    void keepScreenOn(true);
  }
});

mapView.onUserCamera = () => {
  // A hand-made camera move ends the post-navigation hold on the heatmap's area (heatArea).
  if (!nav.session) nav.exitLine = null;
  if (nav.session && nav.following) {
    nav.following = false;
    renderNav();
  }
};

// ------------------------------------------------------------- bottom sheet
const sheetHandle = $("sheet-handle");
function sheetLabel(): void {
  const collapsed = panel.classList.contains("collapsed");
  sheetHandle.setAttribute("aria-label", collapsed ? tr("Perbesar panel", "Expand panel") : tr("Ciutkan panel", "Collapse panel"));
}
function setSheetCollapsed(collapsed: boolean): void {
  panel.classList.toggle("collapsed", collapsed);
  sheetHandle.setAttribute("aria-expanded", String(!collapsed));
  sheetLabel();
}
sheetHandle.addEventListener("click", () => {
  if (sheetDrag.justDragged) return; // the release of a drag, not a tap
  const collapsed = panel.classList.contains("collapsed");
  if (!window.matchMedia("(max-width: 899px)").matches) return setSheetCollapsed(!collapsed);
  // A tap slides the sheet the same way a drag does: start where it is, end at the other state.
  const range = measureSheetRange();
  animateSheet(collapsed ? range : 0, !collapsed, range);
});

/** Dragging the sheet by its handle (or the header beside it) opens or closes it, as a phone's
 *  bottom sheet does: the sheet follows the finger, and on release it settles open or closed by
 *  how far it went or by a quick flick. Phones only; a tap on the handle still toggles it. */
const sheetDrag = { startY: 0, lastY: 0, lastT: 0, speed: 0, range: 0, from: 0, active: false, moved: false, justDragged: false };
const SHEET_DRAG_START_PX = 6;
const SHEET_FLICK_PX_PER_MS = 0.5;

/** How far the sheet travels between open and closed: its body and credits when open. Leaves the
 *  sheet open (class removed) so they can be measured; animateSheet settles the class. */
function measureSheetRange(): number {
  panel.classList.remove("collapsed");
  return $("panel-body").offsetHeight + $("panel-credits").offsetHeight;
}

/** Slides the sheet from `from` px down to open (0) or closed (`range`), then sets the state. */
function animateSheet(from: number, close: boolean, range: number): void {
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  panel.style.transition = "none";
  panel.style.transform = `translateY(${from}px)`;
  void panel.offsetHeight; // apply the start position before the transition starts
  panel.style.transition = reduce ? "none" : "transform var(--dur) var(--ease-out)";
  panel.style.transform = `translateY(${close ? range : 0}px)`;
  const settle = () => {
    panel.style.transition = "none";
    panel.style.transform = "";
    setSheetCollapsed(close);
    requestAnimationFrame(() => (panel.style.transition = ""));
  };
  // A timer rather than transitionend: when the sheet is already where it settles (dragged all the
  // way), no transition runs and transitionend would never come.
  if (reduce) settle();
  else window.setTimeout(settle, 230);
}

function sheetOffset(): number {
  return Number.parseFloat(panel.style.transform.replace(/[^\d.-]/g, "")) || 0;
}

function onSheetPointerDown(e: PointerEvent): void {
  if (!window.matchMedia("(max-width: 899px)").matches || !e.isPrimary || e.button !== 0) return;
  // The header's own buttons (language) stay buttons.
  if (e.currentTarget !== sheetHandle && (e.target as Element).closest("button, a, input")) return;
  const collapsed = panel.classList.contains("collapsed");
  sheetDrag.range = measureSheetRange();
  sheetDrag.from = collapsed ? sheetDrag.range : 0;
  if (collapsed) panel.style.transform = `translateY(${sheetDrag.from}px)`;
  Object.assign(sheetDrag, { startY: e.clientY, lastY: e.clientY, lastT: e.timeStamp, speed: 0, active: true, moved: false });
  panel.style.transition = "none";
  try {
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId); // keep the drag when the finger leaves the grip
  } catch {
    // the pointer is no longer active: the drag still works while it stays on the grip
  }
}

function onSheetPointerMove(e: PointerEvent): void {
  if (!sheetDrag.active) return;
  const dy = e.clientY - sheetDrag.startY;
  if (!sheetDrag.moved && Math.abs(dy) < SHEET_DRAG_START_PX) return;
  sheetDrag.moved = true;
  const dt = e.timeStamp - sheetDrag.lastT;
  if (dt > 0) sheetDrag.speed = (e.clientY - sheetDrag.lastY) / dt;
  sheetDrag.lastY = e.clientY;
  sheetDrag.lastT = e.timeStamp;
  const offset = Math.min(Math.max(sheetDrag.from + dy, 0), sheetDrag.range);
  panel.style.transform = `translateY(${offset}px)`;
}

function onSheetPointerUp(): void {
  if (!sheetDrag.active) return;
  sheetDrag.active = false;
  const wasCollapsed = sheetDrag.from > 0;
  if (!sheetDrag.moved) {
    // A tap: put things back as they were and let the click handler toggle.
    panel.style.transition = "";
    panel.style.transform = "";
    if (wasCollapsed) panel.classList.add("collapsed");
    return;
  }
  sheetDrag.justDragged = true;
  window.setTimeout(() => (sheetDrag.justDragged = false), 0);
  const offset = sheetOffset();
  const close =
    sheetDrag.speed > SHEET_FLICK_PX_PER_MS ? true : sheetDrag.speed < -SHEET_FLICK_PX_PER_MS ? false : offset > sheetDrag.range / 2;
  animateSheet(offset, close, sheetDrag.range);
}

for (const grip of [sheetHandle, panel.querySelector<HTMLElement>(".brand")!]) {
  grip.addEventListener("pointerdown", onSheetPointerDown);
  grip.addEventListener("pointermove", onSheetPointerMove);
  grip.addEventListener("pointerup", onSheetPointerUp);
  grip.addEventListener("pointercancel", onSheetPointerUp);
}

// ------------------------------------------------------------- side panel
// Desktop: a tab on the panel's edge slides it out so the map fills the window, and back.
const panelToggle = $<HTMLButtonElement>("panel-toggle");
panelToggle.append(icon("back", 18)); // chevron; style.css turns it around when the panel is out
function panelToggleLabel(): void {
  const collapsed = $("app").classList.contains("panel-collapsed");
  const label = collapsed ? tr("Tampilkan panel", "Show panel") : tr("Sembunyikan panel", "Hide panel");
  panelToggle.setAttribute("aria-label", label);
  panelToggle.title = label;
}
panelToggle.addEventListener("click", () => {
  const collapsed = $("app").classList.toggle("panel-collapsed");
  panelToggle.setAttribute("aria-expanded", String(!collapsed));
  panel.inert = collapsed; // out of view: out of the tab order too
  panelToggleLabel();
});
panelToggleLabel();

// ------------------------------------------------------------------ language
for (const b of Array.from(document.querySelectorAll<HTMLButtonElement>(".lang-btn"))) {
  b.addEventListener("click", () => setLang(b.dataset.lang as Lang));
}

/** Redraws every text on screen in the new language; the trip, result and navigation stay as they are. */
onLangChange(() => {
  applyStaticText();
  sheetLabel();
  panelToggleLabel();
  setLocateLabel(!!nav.session);
  heatToggle.setAttribute(
    "aria-label",
    state.heat.on ? tr("Sembunyikan heatmap paparan", "Hide exposure heatmap") : tr("Tampilkan heatmap paparan", "Show exposure heatmap"),
  );
  updatePriority();
  refreshForm();
  statusRender && setStatus(statusRender);
  legend.relabel();
  legendState();
  mapView.closePopup();
  for (const w of ["origin", "destination"] as const) searches[w].stop();
  if (!resultView.hidden && state.result) showResultView(state.resultNotice);
  if (nav.session && state.result) {
    if (nav.message) nav.message.view = nav.message.make();
    nav.view = new NavView(navViewEl, { onEnd: () => endNavigation(), onRecenter: recenter });
    renderNav();
    mountSimPanel(); // the new view replaced the old one's contents
  }
});

sheetLabel();
refreshForm();
legendState();
