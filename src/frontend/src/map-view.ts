import maplibregl from "maplibre-gl";
import {
  BASEMAP_ATTRIBUTION,
  BASEMAP_STYLE,
  BASEMAP_TILES,
  DETAIL_ZOOM,
  MAP_CENTER,
  MAP_ZOOM,
  STUDY_AREA,
} from "./config";
import { EXPOSURE_STOPS, NO_DATA_COLOR, layerRatio } from "./exposure";
import { circle, splitAt, type RouteLine } from "./nav-geometry";
import landmarks from "./landmarks.json";
import jakartaBoundary from "./jakarta-boundary.json";
import type { BBox, ExposureSurface, HeatLayer, Mode, Point, RouteResponse, SurfaceFeature } from "./types";
import { tr } from "./i18n";
import { icon } from "./dom";

const ROUTE_SOURCE = "routes";
const HEAT_SOURCE = "heat";
const ACCURACY_SOURCE = "position-accuracy";
const LANDMARK_SOURCE = "landmarks";
const SKELETON_SOURCE = "heat-skeleton";
const MASK_SOURCE = "outside-study-area";
/** Grey of the loading skeleton and of the area outside the study area (--surface-2 / --outline). */
const SKELETON_COLOR = "#cfcac2";
const OUTSIDE_COLOR = "#ece8e2";
/** Basemap source layers that give the map its shape; outside the study area only these are drawn. */
const SHAPE_SOURCE_LAYERS = new Set(["water", "waterway"]);
const SHAPE_SUFFIX = "-shape";
/** The app's own layers, which are not basemap and never reach outside the study area. */
const APP_LAYERS = new Set([
  "heat-skeleton", "heat-fill", "heat-line",
  "route-casing", "route-comparison", "route-recommended", "route-nav-casing", "route-passed", "route-nav",
  "position-accuracy", "outside-fill", "outside-edge",
]);

const ROUTE_GREEN = "#2b6f2f"; // --green in style.css
/** The part of the route already travelled while navigating. */
const ROUTE_PASSED = "#a3a8a3";
/** Route widths in px: while navigating the line is thicker, as in a directions app. */
const NAV_WIDTH = ["interpolate", ["linear"], ["zoom"], 12, 6, 16, 10, 19, 16] as unknown as maplibregl.ExpressionSpecification;
const NAV_CASING = ["interpolate", ["linear"], ["zoom"], 12, 9, 16, 14, 19, 22] as unknown as maplibregl.ExpressionSpecification;
/** How long the heatmap takes to fade in or out. */
const HEAT_FADE_MS = 250;
/** Camera tilt while navigating, in degrees (0 = straight down). */
const NAV_PITCH = 55;
const ROUTE_GRAY = "#3b4658";

function colorExpression(prop: string): maplibregl.ExpressionSpecification {
  const stops = EXPOSURE_STOPS.flatMap(([v, c]) => [v, c]);
  return [
    "case",
    ["==", ["typeof", ["get", prop]], "number"],
    ["interpolate", ["linear"], ["get", prop], ...stops],
    NO_DATA_COLOR,
  ] as unknown as maplibregl.ExpressionSpecification;
}

/** The vector basemap, or raster tiles when VITE_BASEMAP_TILES is set. */
function basemapStyle(): string | maplibregl.StyleSpecification {
  if (!BASEMAP_TILES) return BASEMAP_STYLE;
  return {
    version: 8,
    sources: {
      basemap: { type: "raster", tiles: [BASEMAP_TILES], tileSize: 256, maxzoom: 19, attribution: BASEMAP_ATTRIBUTION },
    },
    layers: [
      {
        id: "basemap",
        type: "raster",
        source: "basemap",
        // Muted basemap so the route and heatmap colours carry the meaning.
        paint: { "raster-saturation": -0.75, "raster-brightness-max": 0.95, "raster-contrast": -0.1 },
      },
    ],
  };
}

export interface SegmentClick {
  point: Point;
  feature: SurfaceFeature | null;
}

export class MapView {
  readonly map: maplibregl.Map;
  private origin: maplibregl.Marker | null = null;
  private destination: maplibregl.Marker | null = null;
  private pointsDraggable = true;
  private ready: Promise<void>;
  private surface: SurfaceFeature[] = [];
  private heatLayer: HeatLayer = "combined";
  private popup: maplibregl.Popup | null = null;
  private user: maplibregl.Marker | null = null;

  onPointMoved: (which: "origin" | "destination", p: Point) => void = () => {};
  onClick: (click: SegmentClick, event: maplibregl.MapMouseEvent) => void = () => {};
  onViewChange: () => void = () => {};
  /** The user moved the map by hand (drag, pinch, wheel), as opposed to the camera following them. */
  onUserCamera: () => void = () => {};

  constructor(container: HTMLElement) {
    this.map = new maplibregl.Map({
      container,
      style: basemapStyle(),
      center: MAP_CENTER,
      zoom: MAP_ZOOM,
      maxBounds: [
        [STUDY_AREA.minLon - 0.3, STUDY_AREA.minLat - 0.3],
        [STUDY_AREA.maxLon + 0.3, STUDY_AREA.maxLat + 0.3],
      ],
      attributionControl: false, // credits are in the side panel (renderCredits in main.ts)
    });
    // The container can be laid out after the map is created (fonts, bottom sheet,
    // hidden tab); MapLibre would then keep its 400×300 default canvas.
    new ResizeObserver(() => this.map.resize()).observe(container);

    this.ready = new Promise((resolve) => {
      this.map.on("load", () => {
        this.initLayers();
        resolve();
      });
    });
    this.map.on("moveend", () => this.onViewChange());
    this.map.on("click", (e) => this.handleClick(e));
    // Camera moves started by easeTo/flyTo carry no originalEvent; those by a hand do.
    this.map.on("movestart", (e) => {
      if ((e as { originalEvent?: Event }).originalEvent) this.onUserCamera();
    });
  }

  private initLayers(): void {
    const m = this.map;
    // The heatmap, routes and position sit above every basemap road, bridge and building, but
    // under its labels and place icons (street names, landmarks), as in a directions app. Not
    // simply before the first symbol layer: in the vector style that is the one-way arrows, which
    // come before the bridges and buildings, so a flyover would cut the route. A raster basemap
    // has no labels: on top.
    const style = m.getStyle().layers;
    const lastShape = style.map((l) => l.type !== "symbol").lastIndexOf(true);
    const labels = style[lastShape + 1]?.id;
    const addLayer = (layer: maplibregl.AddLayerObject) => m.addLayer(layer, labels);

    m.addSource(HEAT_SOURCE, { type: "geojson", data: emptyCollection() });
    m.addSource(SKELETON_SOURCE, { type: "geojson", data: emptyCollection() });
    m.addSource(ROUTE_SOURCE, { type: "geojson", data: emptyCollection() });
    m.addSource(ACCURACY_SOURCE, { type: "geojson", data: emptyCollection() });

    const isLine: maplibregl.ExpressionSpecification = ["in", ["geometry-type"], ["literal", ["LineString", "MultiLineString"]]] as unknown as maplibregl.ExpressionSpecification;
    const isArea: maplibregl.ExpressionSpecification = ["in", ["geometry-type"], ["literal", ["Polygon", "MultiPolygon"]]] as unknown as maplibregl.ExpressionSpecification;
    const lineWidth = ["interpolate", ["linear"], ["zoom"], 11, 2, 15, 5, 18, 10] as unknown as maplibregl.ExpressionSpecification;

    // Loading skeleton: grey cells under the heatmap, pulsing while its data is on the way.
    addLayer({
      id: "heat-skeleton",
      type: "fill",
      source: SKELETON_SOURCE,
      paint: { "fill-color": SKELETON_COLOR, "fill-opacity": 0 },
    });
    addLayer({
      id: "heat-fill",
      type: "fill",
      source: HEAT_SOURCE,
      filter: isArea,
      paint: { "fill-color": colorExpression("ratio"), "fill-opacity": 0.6 },
    });
    addLayer({
      id: "heat-line",
      type: "line",
      source: HEAT_SOURCE,
      filter: isLine,
      layout: { "line-cap": "butt" },
      paint: { "line-color": colorExpression("ratio"), "line-width": lineWidth, "line-opacity": 0.92 },
    });

    // Routes: casing first so the lines stay legible over the heatmap.
    addLayer({
      id: "route-casing",
      type: "line",
      source: ROUTE_SOURCE,
      layout: { "line-join": "round", "line-cap": "round" },
      filter: ["in", ["get", "role"], ["literal", ["recommended", "comparison"]]],
      paint: { "line-color": "#ffffff", "line-width": ["case", ["==", ["get", "role"], "recommended"], 10, 8] },
    });
    addLayer({
      id: "route-comparison",
      type: "line",
      source: ROUTE_SOURCE,
      filter: ["==", ["get", "role"], "comparison"],
      layout: { "line-join": "round", "line-cap": "butt" },
      // Shape (dashed vs solid) separates the routes, not colour alone (PF-09, PNF-08).
      paint: { "line-color": ROUTE_GRAY, "line-width": 4, "line-dasharray": [1.2, 1.4] },
    });
    addLayer({
      id: "route-recommended",
      type: "line",
      source: ROUTE_SOURCE,
      filter: ["==", ["get", "role"], "recommended"],
      layout: { "line-join": "round", "line-cap": "round" },
      paint: { "line-color": ROUTE_GREEN, "line-width": 6 },
    });
    // While navigating: the way already travelled in grey, the way ahead in a thicker green, both
    // on a white edge. (A zoom-based width cannot sit inside a per-role switch, so own layers.)
    addLayer({
      id: "route-nav-casing",
      type: "line",
      source: ROUTE_SOURCE,
      filter: ["in", ["get", "role"], ["literal", ["nav", "passed"]]],
      layout: { "line-join": "round", "line-cap": "round" },
      paint: { "line-color": "#ffffff", "line-width": NAV_CASING },
    });
    addLayer({
      id: "route-passed",
      type: "line",
      source: ROUTE_SOURCE,
      filter: ["==", ["get", "role"], "passed"],
      layout: { "line-join": "round", "line-cap": "round" },
      paint: { "line-color": ROUTE_PASSED, "line-width": NAV_WIDTH },
    });
    addLayer({
      id: "route-nav",
      type: "line",
      source: ROUTE_SOURCE,
      filter: ["==", ["get", "role"], "nav"],
      layout: { "line-join": "round", "line-cap": "round" },
      paint: { "line-color": ROUTE_GREEN, "line-width": NAV_WIDTH },
    });

    // How far off the shown position may be (TI-SE-13), above the routes.
    addLayer({
      id: "position-accuracy",
      type: "fill",
      source: ACCURACY_SOURCE,
      paint: { "fill-color": "#1a73e8", "fill-opacity": 0.12, "fill-outline-color": "#1a73e8" },
    });

    this.addLandmarks();
    this.addOutsideMask();
  }

  /** Greys out everything outside Jakarta's administrative border (jakarta-boundary.json, from
   *  geoBoundaries / OpenStreetMap) while keeping the map's shape there: the sea, rivers and lakes
   *  stay, but the roads, buildings, labels and place icons go, with a dashed edge along the border.
   *  The Backend still serves the whole STUDY_AREA rectangle; this is only how the map looks.
   *
   *  MapLibre cannot clip a layer to an area, so it is built in layers. Bottom to top: the basemap's
   *  land, roads and buildings; the heatmap cells; an opaque grey mask with Jakarta cut out; copies
   *  of the water layers (so the water shows everywhere, and the cells stop at the water's edge as
   *  well); copies of the bridge layers from zoom 14, inside Jakarta only, so a flyover over a canal
   *  is not cut; the dashed border; the heatmap's road lines and the routes; and the labels and
   *  place icons, inside Jakarta only. On a raster basemap there is nothing to copy: plain grey. */
  private addOutsideMask(): void {
    const m = this.map;
    const border = jakartaBoundary as GeoJSON.MultiPolygon;
    const world = [[-180, -85], [180, -85], [180, 85], [-180, 85], [-180, -85]];
    // One polygon: the world with a hole for each part of Jakarta (holes wind the other way).
    const holes = border.coordinates.map((poly) => [...poly[0]].reverse());
    const inside = ["within", border] as unknown as maplibregl.ExpressionSpecification;
    const restrict = (filter: unknown, keepAreas = false): maplibregl.FilterSpecification => {
      // `within` only answers for points and lines; area features (rare among labels) keep showing.
      const test = keepAreas
        ? ["any", ["match", ["geometry-type"], ["Polygon", "MultiPolygon"], true, false], inside]
        : inside;
      return (filter === undefined ? test : ["all", toExpression(filter), test]) as maplibregl.FilterSpecification;
    };

    const basemap = m.getStyle().layers.filter((l) => !APP_LAYERS.has(l.id) && !l.id.startsWith("landmark-"));
    const sourceLayer = (l: maplibregl.LayerSpecification) => (l as { "source-layer"?: string })["source-layer"];
    // Above the heatmap cells, under its road lines and the routes.
    const before = m.getLayer("heat-line") ? "heat-line" : undefined;

    m.addSource(MASK_SOURCE, {
      type: "geojson",
      data: { type: "Feature", properties: {}, geometry: { type: "Polygon", coordinates: [world, ...holes] } },
    });
    m.addLayer({ id: "outside-fill", type: "fill", source: MASK_SOURCE, paint: { "fill-color": OUTSIDE_COLOR, "fill-opacity": 1 } }, before);

    // The map's shape: sea, lakes, rivers, canals.
    for (const l of basemap) {
      if (l.type === "symbol" || /tunnel/i.test(l.id) || !SHAPE_SOURCE_LAYERS.has(sourceLayer(l) ?? "")) continue;
      m.addLayer({ ...l, id: `${l.id}${SHAPE_SUFFIX}` } as maplibregl.AddLayerObject, before);
    }
    // Bridges over that water, inside Jakarta, from the zoom where the heatmap turns into road lines
    // (below it a flyover would stick out of the heatmap cells).
    for (const l of basemap) {
      if (l.type === "symbol" || sourceLayer(l) !== "transportation" || !/bridge/i.test(l.id)) continue;
      const copy = {
        ...l,
        id: `${l.id}${SHAPE_SUFFIX}`,
        minzoom: Math.max(l.minzoom ?? 0, DETAIL_ZOOM),
        filter: restrict((l as { filter?: unknown }).filter),
      };
      m.addLayer(copy as maplibregl.AddLayerObject, before);
    }

    m.addLayer(
      {
        id: "outside-edge",
        type: "line",
        source: MASK_SOURCE,
        paint: { "line-color": "#8a857e", "line-width": 1.5, "line-dasharray": [3, 2] },
      },
      before,
    );

    // The heatmap's road lines, labels and place icons sit above the mask: keep them inside.
    if (m.getLayer("heat-line")) m.setFilter("heat-line", restrict(m.getFilter("heat-line") ?? undefined));
    for (const l of m.getStyle().layers) {
      if (l.type !== "symbol" || l.id.endsWith(SHAPE_SUFFIX)) continue;
      m.setFilter(l.id, restrict((l as { filter?: unknown }).filter, true));
    }
  }

  /** Landmarks at city zoom, as in a directions app. The vector style shows place icons only from
   *  zoom 15 and its tiles carry places only from zoom 14, so: a bundled list of well-known places
   *  (landmarks.json) for zoom 11–14, then the tiles' notable places for zoom 14–15. Styled like
   *  the basemap's own place labels; nothing is added on a raster basemap. */
  private addLandmarks(): void {
    const m = this.map;
    const base = m.getStyle().layers.find((l) => l.id === "poi_r1") as maplibregl.SymbolLayerSpecification | undefined;
    if (!base) return;
    const like = (id: string, extra: Partial<maplibregl.SymbolLayerSpecification>) => {
      const layer = { ...base, id, layout: { ...base.layout, "icon-image": ["get", "icon"] }, ...extra } as maplibregl.SymbolLayerSpecification;
      if (layer.source === LANDMARK_SOURCE) {
        delete layer["source-layer"]; // GeoJSON has no source layers
        layer.layout = { ...layer.layout, "symbol-sort-key": ["get", "rank"] }; // most famous wins a collision
      }
      m.addLayer(layer);
    };

    m.addSource(LANDMARK_SOURCE, { type: "geojson", data: landmarks as GeoJSON.FeatureCollection });
    const fromBundle = { source: LANDMARK_SOURCE, maxzoom: 14 };
    like("landmark-major", { ...fromBundle, minzoom: 11, filter: ["==", ["get", "major"], true] });
    like("landmark-minor", { ...fromBundle, minzoom: 13, filter: ["!=", ["get", "major"], true] });

    // Zoom 14–15: notable places from the tiles (not clinics, offices, shops in general).
    like("landmark-tiles", {
      minzoom: 14,
      maxzoom: 15,
      layout: { ...base.layout },
      filter: [
        "all",
        ["match", ["geometry-type"], ["Point", "MultiPoint"], true, false],
        [
          "any",
          ["match", ["get", "class"], ["attraction", "monument", "museum", "stadium", "castle", "zoo", "college", "theatre"], true, false],
          ["match", ["get", "subclass"], ["hospital", "mall", "department_store", "university"], true, false],
        ],
      ] as maplibregl.FilterSpecification,
    });
  }

  /** Puts the zoom buttons into `stack` (the app's own map buttons), so all of them share one
   *  column, spacing and stacking order instead of MapLibre's separate corner. */
  mountZoomButtons(stack: HTMLElement): void {
    stack.append(new maplibregl.NavigationControl({ showCompass: false }).onAdd(this.map));
  }

  // --------------------------------------------------------------- markers

  setPoint(which: "origin" | "destination", p: Point | null): void {
    const existing = which === "origin" ? this.origin : this.destination;
    if (!p) {
      existing?.remove();
      if (which === "origin") this.origin = null;
      else this.destination = null;
      return;
    }
    if (existing) {
      existing.setLngLat([p.lon, p.lat]);
      return;
    }
    const el = document.createElement("div");
    el.className = `marker marker-${which}`;
    el.setAttribute("role", "img");
    el.setAttribute("aria-label", which === "origin" ? tr("Titik asal", "Starting point") : tr("Titik tujuan", "Destination"));
    const marker = new maplibregl.Marker({ element: el, draggable: this.pointsDraggable, anchor: which === "origin" ? "center" : "bottom" })
      .setLngLat([p.lon, p.lat])
      .addTo(this.map);
    marker.on("dragend", () => {
      const ll = marker.getLngLat();
      this.onPointMoved(which, { lat: ll.lat, lon: ll.lng });
    });
    if (which === "origin") this.origin = marker;
    else this.destination = marker;
  }

  /** Off while navigating: the session keeps its destination, so a moved pin would misstate it. */
  setPointsDraggable(on: boolean): void {
    this.pointsDraggable = on;
    this.origin?.setDraggable(on);
    this.destination?.setDraggable(on);
  }

  // ---------------------------------------------------------------- routes

  /** `inset` is the part of the map covered by the panel, so the fitted route
   *  ends up in the visible area (the bottom sheet on phones, the left panel on desktop). */
  async showRoutes(response: RouteResponse, inset: { bottom: number }): Promise<void> {
    await this.ready;
    const features: GeoJSON.Feature[] = [response.recommended, response.comparison]
      .filter((r): r is NonNullable<typeof r> => r !== null)
      .map((r) => ({
        type: "Feature",
        geometry: { type: "LineString", coordinates: r.coordinates },
        properties: { role: r.role },
      }));
    this.routeSource().setData({ type: "FeatureCollection", features });

    const bounds = new maplibregl.LngLatBounds();
    for (const f of features) for (const c of (f.geometry as GeoJSON.LineString).coordinates) bounds.extend(c as [number, number]);
    // Always flat and north-up: after navigation (tilted, turned) this replaces the reset camera
    // move, which a fit that kept the current tilt would interrupt.
    this.map.fitBounds(bounds, {
      padding: { top: 70, bottom: Math.max(60, inset.bottom), left: 50, right: 70 },
      maxZoom: 16,
      bearing: 0,
      pitch: 0,
      duration: 600,
    });
  }

  /** Emphasises the route chosen in the list; the other one stays visible but faded. */
  async selectRoute(role: "recommended" | "comparison"): Promise<void> {
    await this.ready;
    this.map.setPaintProperty("route-recommended", "line-opacity", role === "recommended" ? 1 : 0.4);
    this.map.setPaintProperty("route-comparison", "line-opacity", role === "comparison" ? 1 : 0.4);
    this.map.setPaintProperty("route-comparison", "line-width", role === "comparison" ? 6 : 4);
  }

  async clearRoutes(): Promise<void> {
    await this.ready;
    this.routeSource().setData(emptyCollection());
  }

  // ------------------------------------------------------------ navigation

  /** Only the route being followed, drawn as the recommended line (TI-SE-13). */
  /** The route being navigated, cut where the user is: grey behind, green ahead. */
  async showNavRoute(line: RouteLine, alongM: number): Promise<void> {
    await this.ready;
    const { passed, ahead } = splitAt(line, alongM);
    const feature = (role: string, coordinates: [number, number][]): GeoJSON.Feature => ({
      type: "Feature",
      geometry: { type: "LineString", coordinates },
      properties: { role },
    });
    this.routeSource().setData({
      type: "FeatureCollection",
      features: [...(alongM > 0 ? [feature("passed", passed)] : []), feature("nav", ahead)],
    });
  }

  private userMode: Mode = "walk";
  private userHeading: number | null = null;

  /** The travel mode drawn inside the position marker: a walker or a cyclist. */
  setUserMode(mode: Mode): void {
    if (mode === this.userMode && this.user) return;
    this.userMode = mode;
    const glyph = this.user?.getElement().querySelector(".marker-user-glyph");
    if (glyph) glyph.replaceChildren(icon(mode, 16));
  }

  /** The user's position, its heading (null: no arrow) and accuracy; null removes them. The
   *  marker is a blue disc with the travel mode's figure (setUserMode); only the direction wedge
   *  turns, so the figure stays upright. */
  async setUser(p: Point | null, headingDeg: number | null = null, accuracyM: number | null = null): Promise<void> {
    if (!p) {
      this.user?.remove();
      this.user = null;
    } else {
      if (!this.user) {
        const el = document.createElement("div");
        el.className = "marker-user";
        el.setAttribute("role", "img");
        el.setAttribute("aria-label", tr("Posisi Anda", "Your position"));
        const dir = Object.assign(document.createElement("div"), { className: "marker-user-dir" });
        dir.append(Object.assign(document.createElement("div"), { className: "marker-user-heading" }));
        const glyph = Object.assign(document.createElement("div"), { className: "marker-user-glyph" });
        glyph.append(icon(this.userMode, 16));
        el.append(dir, glyph);
        // pitchAlignment "viewport": it stays upright instead of lying flat when the map is tilted.
        this.user = new maplibregl.Marker({ element: el, pitchAlignment: "viewport" }).setLngLat([p.lon, p.lat]).addTo(this.map);
        // The wedge points along the heading on the map, so it turns back as the map turns.
        this.map.on("rotate", () => this.turnUserWedge());
      }
      this.user.setLngLat([p.lon, p.lat]);
      this.userHeading = headingDeg;
      this.user.getElement().classList.toggle("has-heading", headingDeg !== null);
      this.turnUserWedge();
    }
    await this.ready;
    const ring = p && accuracyM && Number.isFinite(accuracyM) && accuracyM > 10 ? circle(p, accuracyM) : null;
    (this.map.getSource(ACCURACY_SOURCE) as maplibregl.GeoJSONSource).setData(
      ring
        ? { type: "FeatureCollection", features: [{ type: "Feature", geometry: { type: "Polygon", coordinates: [ring] }, properties: {} }] }
        : emptyCollection(),
    );
  }

  /** Turns the direction wedge to the heading as seen on screen (heading minus the map's bearing). */
  private turnUserWedge(): void {
    if (!this.user || this.userHeading === null) return;
    const dir = this.user.getElement().querySelector<HTMLElement>(".marker-user-dir");
    dir?.style.setProperty("--heading", `${this.userHeading - this.map.getBearing()}deg`);
  }

  /** Keeps `p` in the visible part of the map (above the bottom sheet on phones). */
  /** Navigation camera, as in a directions app: tilted to look ahead, turned so the way ahead
   *  points up the screen, and the user low in the visible map (above the bottom sheet on phones)
   *  so more of the road ahead shows. Without a heading the map keeps its current turn. */
  follow(p: Point, headingDeg: number | null, inset: { top?: number; bottom: number }, zoom?: number): void {
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const top = inset.top ?? 0;
    const visible = Math.max(0, this.map.getContainer().clientHeight - top - inset.bottom);
    this.map.easeTo({
      center: [p.lon, p.lat],
      zoom: zoom ?? this.map.getZoom(),
      bearing: headingDeg ?? this.map.getBearing(),
      pitch: NAV_PITCH,
      // The top padding moves the centre down: the user ends up about 70 % down the visible map.
      padding: { top: top + Math.round(visible * 0.45), left: 0, right: 0, bottom: inset.bottom },
      duration: reduce ? 0 : 600,
    });
  }

  /** Back to the flat, north-up map after navigation. */
  resetCamera(): void {
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    this.map.easeTo({ bearing: 0, pitch: 0, padding: { top: 0, left: 0, right: 0, bottom: 0 }, duration: reduce ? 0 : 600 });
  }

  /** Clears the padding follow() leaves on the camera. */

  // --------------------------------------------------------------- heatmap

  async setSurface(surface: ExposureSurface | null): Promise<void> {
    await this.ready;
    const first = this.surface.length === 0;
    this.surface = surface?.features ?? [];
    // The first cells fade in over the skeleton instead of popping in; later updates replace
    // cells in place, where a fade would make the whole heatmap blink.
    if (first && this.heatShown) this.setHeatOpacity(0, true);
    this.renderHeat();
    if (first && this.heatShown) requestAnimationFrame(() => this.heatShown && this.setHeatOpacity(1));
  }

  private skeletonFrame = 0;
  private skeletonClear = 0;

  /** Shows pulsing grey cells over `area` (clipped to the study area) where no heatmap cell is
   *  drawn yet, while its data loads; null fades them out. */
  async showHeatSkeleton(area: BBox | null): Promise<void> {
    await this.ready;
    cancelAnimationFrame(this.skeletonFrame);
    window.clearTimeout(this.skeletonClear);
    const source = this.map.getSource(SKELETON_SOURCE) as maplibregl.GeoJSONSource;
    if (!area) {
      this.map.setPaintProperty("heat-skeleton", "fill-opacity-transition", { duration: HEAT_FADE_MS, delay: 0 });
      this.map.setPaintProperty("heat-skeleton", "fill-opacity", 0);
      this.skeletonClear = window.setTimeout(() => source.setData(emptyCollection()), HEAT_FADE_MS);
      return;
    }
    source.setData({ type: "FeatureCollection", features: skeletonCells(area, this.surface) });
    this.map.setPaintProperty("heat-skeleton", "fill-opacity-transition", { duration: 0, delay: 0 });
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      this.map.setPaintProperty("heat-skeleton", "fill-opacity", 0.35);
      return;
    }
    // A slow pulse between faint and light grey, about 30 frames a second.
    let last = 0;
    const pulse = (t: number) => {
      if (t - last > 33) {
        last = t;
        this.map.setPaintProperty("heat-skeleton", "fill-opacity", 0.2 + 0.25 * (0.5 + 0.5 * Math.sin(t / 380)));
      }
      this.skeletonFrame = requestAnimationFrame(pulse);
    };
    this.skeletonFrame = requestAnimationFrame(pulse);
  }

  async setHeatLayer(layer: HeatLayer): Promise<void> {
    this.heatLayer = layer;
    await this.ready;
    this.renderHeat();
  }

  /** Dims the heatmap when it no longer matches the latest request. */
  async setHeatDimmed(dimmed: boolean): Promise<void> {
    await this.ready;
    this.heatDimmed = dimmed;
    if (this.heatShown) this.setHeatOpacity(1);
  }

  /** Each heat layer's opacity property and its full and dimmed values. */
  private static readonly HEAT_OPACITY: ReadonlyArray<[id: string, prop: string, full: number, dimmed: number]> = [
    ["heat-fill", "fill-opacity", 0.6, 0.25],
    ["heat-line", "line-opacity", 0.92, 0.35],
  ];
  private heatDimmed = false;
  private heatShown = false;
  private heatFade = 0;

  /** Sets the heat layers to `share` (0–1) of their normal opacity, easing over HEAT_FADE_MS. */
  private setHeatOpacity(share: number, instant = false): void {
    const duration = instant || window.matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : HEAT_FADE_MS;
    for (const [id, prop, full, dimmed] of MapView.HEAT_OPACITY) {
      this.map.setPaintProperty(id, `${prop}-transition`, { duration, delay: 0 });
      this.map.setPaintProperty(id, prop, share * (this.heatDimmed ? dimmed : full));
    }
  }

  /** Colours always come from the ratio to the WHO guideline, so switching the
   *  layer re-colours data already in memory and sends no request. */
  private renderHeat(): void {
    // `ratio` is the WHO-guideline ratio of whichever layer is shown, so the
    // paint expressions never change; `i` indexes back into this.surface on click.
    const features: GeoJSON.Feature[] = this.surface.map((f, i) => ({
      type: "Feature",
      geometry: f.geometry,
      properties: {
        ratio: layerRatio(this.heatLayer, f),
        i,
      },
    }));
    (this.map.getSource(HEAT_SOURCE) as maplibregl.GeoJSONSource).setData({ type: "FeatureCollection", features });
  }

  /** Fades the heatmap in or out; hidden layers are switched off once faded, so they cost nothing. */
  async showHeat(visible: boolean): Promise<void> {
    await this.ready;
    if (visible === this.heatShown) return;
    this.heatShown = visible;
    window.clearTimeout(this.heatFade);
    const setVisibility = (v: "visible" | "none") => {
      for (const [id] of MapView.HEAT_OPACITY) this.map.setLayoutProperty(id, "visibility", v);
    };
    if (visible) {
      this.setHeatOpacity(0, true);
      setVisibility("visible");
      requestAnimationFrame(() => this.heatShown && this.setHeatOpacity(1));
    } else {
      this.closePopup();
      this.setHeatOpacity(0);
      this.heatFade = window.setTimeout(() => !this.heatShown && setVisibility("none"), HEAT_FADE_MS);
    }
  }

  // ----------------------------------------------------------------- misc

  private handleClick(e: maplibregl.MapMouseEvent): void {
    const point = { lat: e.lngLat.lat, lon: e.lngLat.lng };
    let feature: SurfaceFeature | null = null;
    if (this.map.getLayer("heat-line")) {
      const pad = 8;
      const hits = this.map.queryRenderedFeatures(
        [
          [e.point.x - pad, e.point.y - pad],
          [e.point.x + pad, e.point.y + pad],
        ],
        { layers: ["heat-line", "heat-fill"] },
      );
      const i = hits[0]?.properties?.i;
      if (typeof i === "number") feature = this.surface[i] ?? null;
    }
    this.onClick({ point, feature }, e);
  }

  showPopup(at: Point, content: HTMLElement): void {
    this.closePopup();
    this.popup = new maplibregl.Popup({ closeButton: true, maxWidth: "280px", offset: 10 })
      .setLngLat([at.lon, at.lat])
      .setDOMContent(content)
      .addTo(this.map);
  }

  closePopup(): void {
    this.popup?.remove();
    this.popup = null;
  }

  getView(): { bbox: BBox; zoom: number } {
    const b = this.map.getBounds();
    return {
      bbox: { minLon: b.getWest(), minLat: b.getSouth(), maxLon: b.getEast(), maxLat: b.getNorth() },
      zoom: this.map.getZoom(),
    };
  }

  flyTo(p: Point, zoom = 15): void {
    this.map.flyTo({ center: [p.lon, p.lat], zoom: Math.max(this.map.getZoom(), zoom) });
  }

  setPickCursor(on: boolean): void {
    this.map.getCanvas().style.cursor = on ? "crosshair" : "";
  }

  private routeSource(): maplibregl.GeoJSONSource {
    return this.map.getSource(ROUTE_SOURCE) as maplibregl.GeoJSONSource;
  }
}

function emptyCollection(): GeoJSON.FeatureCollection {
  return { type: "FeatureCollection", features: [] };
}

/** Bounding boxes of the drawn heatmap features, to leave those places out of the skeleton. */
function featureBoxes(features: ReadonlyArray<SurfaceFeature>): BBox[] {
  return features.map((f) => {
    const box = { minLon: Infinity, minLat: Infinity, maxLon: -Infinity, maxLat: -Infinity };
    const visit = (c: unknown): void => {
      if (Array.isArray(c) && typeof c[0] === "number") {
        box.minLon = Math.min(box.minLon, c[0] as number);
        box.maxLon = Math.max(box.maxLon, c[0] as number);
        box.minLat = Math.min(box.minLat, c[1] as number);
        box.maxLat = Math.max(box.maxLat, c[1] as number);
      } else if (Array.isArray(c)) c.forEach(visit);
    };
    visit((f.geometry as { coordinates?: unknown }).coordinates);
    return box;
  });
}

/** A grid of rounded-looking cells (small gaps between them) over `area` ∩ the study area, about
 *  24 across the area's width, snapped to the grid so it does not shift as the map moves; cells
 *  whose centre already has a heatmap feature are left out. */
export function skeletonCells(area: BBox, drawn: ReadonlyArray<SurfaceFeature>): GeoJSON.Feature[] {
  const a = STUDY_AREA;
  const minLon = Math.max(area.minLon, a.minLon);
  const maxLon = Math.min(area.maxLon, a.maxLon);
  const minLat = Math.max(area.minLat, a.minLat);
  const maxLat = Math.min(area.maxLat, a.maxLat);
  if (minLon >= maxLon || minLat >= maxLat) return [];
  const step = Math.max(0.003, (maxLon - minLon) / 24);
  const gap = step * 0.1;
  const boxes = featureBoxes(drawn);
  const covered = (lon: number, lat: number) => boxes.some((b) => lon >= b.minLon && lon <= b.maxLon && lat >= b.minLat && lat <= b.maxLat);
  const cells: GeoJSON.Feature[] = [];
  for (let x = Math.floor(minLon / step) * step; x < maxLon && cells.length < 1500; x += step) {
    for (let y = Math.floor(minLat / step) * step; y < maxLat && cells.length < 1500; y += step) {
      const x0 = Math.max(x, a.minLon) + gap;
      const y0 = Math.max(y, a.minLat) + gap;
      const x1 = Math.min(x + step, a.maxLon) - gap;
      const y1 = Math.min(y + step, a.maxLat) - gap;
      if (x1 <= x0 || y1 <= y0 || covered((x0 + x1) / 2, (y0 + y1) / 2)) continue;
      cells.push({
        type: "Feature",
        properties: {},
        geometry: { type: "Polygon", coordinates: [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]] },
      });
    }
  }
  return cells;
}

/** A style filter as an expression. The basemap's style may still use the legacy filter syntax
 *  (["==", "class", "x"]), which cannot be put inside an expression's "all"; this converts it. */
function toExpression(f: unknown): unknown {
  if (isExpressionFilter(f)) return f;
  const [op, key, ...rest] = f as [string, unknown, ...unknown[]];
  const value = (k: unknown) => (k === "$type" ? ["geometry-type"] : k === "$id" ? ["id"] : ["get", k]);
  const eq = (k: unknown, v: unknown): unknown =>
    k === "$type" ? ["match", ["geometry-type"], [v as string, `Multi${v}`], true, false] : ["==", value(k), v];
  switch (op) {
    case "all": case "any":
      return [op, ...(f as unknown[]).slice(1).map(toExpression)];
    case "none":
      return ["!", ["any", ...(f as unknown[]).slice(1).map(toExpression)]];
    case "==": return eq(key, rest[0]);
    case "!=": return ["!", eq(key, rest[0])];
    case "<": case "<=": case ">": case ">=":
      return [op, value(key), rest[0]];
    case "in": return ["any", ...rest.map((v) => eq(key, v))];
    case "!in": return ["!", ["any", ...rest.map((v) => eq(key, v))]];
    case "has": return key === "$id" ? ["!=", ["id"], null] : ["has", key];
    case "!has": return key === "$id" ? ["==", ["id"], null] : ["!", ["has", key]];
    default: return f;
  }
}

/** Whether a filter is already an expression (after isExpressionFilter in the MapLibre style spec). */
function isExpressionFilter(f: unknown): boolean {
  if (f === true || f === false) return true;
  if (!Array.isArray(f) || f.length === 0) return false;
  switch (f[0]) {
    case "has": return f.length >= 2 && f[1] !== "$id" && f[1] !== "$type";
    case "in": return f.length >= 3 && (typeof f[1] !== "string" || Array.isArray(f[2]));
    case "!in": case "!has": case "none": return false;
    case "==": case "!=": case ">": case ">=": case "<": case "<=":
      return f.length !== 3 || Array.isArray(f[1]) || Array.isArray(f[2]);
    case "any": case "all":
      return f.slice(1).every((g) => typeof g === "boolean" || isExpressionFilter(g));
    default: return true;
  }
}
