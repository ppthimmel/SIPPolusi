import maplibregl from "maplibre-gl";
import {
  BASEMAP_ATTRIBUTION,
  BASEMAP_TILES,
  CONFIDENCE_LOW,
  MAP_CENTER,
  MAP_ZOOM,
  STUDY_AREA,
} from "./config";
import { EXPOSURE_STOPS, NO_DATA_COLOR, layerRatio } from "./exposure";
import type { BBox, ExposureSurface, HeatLayer, Point, RouteResponse, SurfaceFeature } from "./types";

const ROUTE_SOURCE = "routes";
const HEAT_SOURCE = "heat";
const HATCH_IMAGE = "hatch";

const ROUTE_GREEN = "#0b6b50";
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

/** Diagonal stripes drawn on a canvas; marks areas where confidence is low. */
function hatchImage(): ImageData {
  const size = 12;
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = size;
  const ctx = canvas.getContext("2d")!;
  ctx.strokeStyle = "rgba(60, 40, 20, 0.75)";
  ctx.lineWidth = 1.6;
  ctx.beginPath();
  for (const offset of [-size, 0, size]) {
    ctx.moveTo(offset, size);
    ctx.lineTo(offset + size, 0);
  }
  ctx.stroke();
  return ctx.getImageData(0, 0, size, size);
}

const lowConfidence: maplibregl.ExpressionSpecification = [
  "<",
  ["coalesce", ["get", "confidence"], 0],
  CONFIDENCE_LOW,
] as unknown as maplibregl.ExpressionSpecification;

export interface SegmentClick {
  point: Point;
  feature: SurfaceFeature | null;
}

export class MapView {
  readonly map: maplibregl.Map;
  private origin: maplibregl.Marker | null = null;
  private destination: maplibregl.Marker | null = null;
  private ready: Promise<void>;
  private surface: SurfaceFeature[] = [];
  private heatLayer: HeatLayer = "combined";
  private popup: maplibregl.Popup | null = null;

  onPointMoved: (which: "origin" | "destination", p: Point) => void = () => {};
  onClick: (click: SegmentClick, event: maplibregl.MapMouseEvent) => void = () => {};
  onViewChange: () => void = () => {};

  constructor(container: HTMLElement) {
    this.map = new maplibregl.Map({
      container,
      style: {
        version: 8,
        sources: {
          basemap: {
            type: "raster",
            tiles: [BASEMAP_TILES],
            tileSize: 256,
            maxzoom: 19,
            attribution: BASEMAP_ATTRIBUTION,
          },
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
      },
      center: MAP_CENTER,
      zoom: MAP_ZOOM,
      maxBounds: [
        [STUDY_AREA.minLon - 0.3, STUDY_AREA.minLat - 0.3],
        [STUDY_AREA.maxLon + 0.3, STUDY_AREA.maxLat + 0.3],
      ],
      attributionControl: { compact: true },
    });
    this.map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
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
  }

  private initLayers(): void {
    const m = this.map;
    m.addImage(HATCH_IMAGE, hatchImage(), { pixelRatio: 2 });

    m.addSource(HEAT_SOURCE, { type: "geojson", data: emptyCollection() });
    m.addSource(ROUTE_SOURCE, { type: "geojson", data: emptyCollection() });

    const isLine: maplibregl.ExpressionSpecification = ["in", ["geometry-type"], ["literal", ["LineString", "MultiLineString"]]] as unknown as maplibregl.ExpressionSpecification;
    const isArea: maplibregl.ExpressionSpecification = ["in", ["geometry-type"], ["literal", ["Polygon", "MultiPolygon"]]] as unknown as maplibregl.ExpressionSpecification;
    const lineWidth = ["interpolate", ["linear"], ["zoom"], 11, 2, 15, 5, 18, 10] as unknown as maplibregl.ExpressionSpecification;

    m.addLayer({
      id: "heat-fill",
      type: "fill",
      source: HEAT_SOURCE,
      filter: isArea,
      paint: { "fill-color": colorExpression("ratio"), "fill-opacity": 0.6 },
    });
    m.addLayer({
      id: "heat-fill-hatch",
      type: "fill",
      source: HEAT_SOURCE,
      filter: ["all", isArea, lowConfidence] as unknown as maplibregl.FilterSpecification,
      paint: { "fill-pattern": HATCH_IMAGE, "fill-opacity": 0.8 },
    });
    m.addLayer({
      id: "heat-line",
      type: "line",
      source: HEAT_SOURCE,
      filter: isLine,
      layout: { "line-cap": "butt" },
      paint: { "line-color": colorExpression("ratio"), "line-width": lineWidth, "line-opacity": 0.92 },
    });
    m.addLayer({
      id: "heat-line-hatch",
      type: "line",
      source: HEAT_SOURCE,
      filter: ["all", isLine, lowConfidence] as unknown as maplibregl.FilterSpecification,
      paint: {
        "line-color": "rgba(255,255,255,0.85)",
        "line-width": ["interpolate", ["linear"], ["zoom"], 11, 1, 15, 2, 18, 4] as unknown as maplibregl.ExpressionSpecification,
        "line-dasharray": [1.5, 1.5],
      },
    });

    // Routes: casing first so the lines stay legible over the heatmap.
    m.addLayer({
      id: "route-casing",
      type: "line",
      source: ROUTE_SOURCE,
      layout: { "line-join": "round", "line-cap": "round" },
      paint: { "line-color": "#ffffff", "line-width": ["case", ["==", ["get", "role"], "recommended"], 10, 8] },
    });
    m.addLayer({
      id: "route-comparison",
      type: "line",
      source: ROUTE_SOURCE,
      filter: ["==", ["get", "role"], "comparison"],
      layout: { "line-join": "round", "line-cap": "butt" },
      // Shape (dashed vs solid) separates the routes, not colour alone (PF-09, PNF-08).
      paint: { "line-color": ROUTE_GRAY, "line-width": 4, "line-dasharray": [1.2, 1.4] },
    });
    m.addLayer({
      id: "route-recommended",
      type: "line",
      source: ROUTE_SOURCE,
      filter: ["==", ["get", "role"], "recommended"],
      layout: { "line-join": "round", "line-cap": "round" },
      paint: { "line-color": ROUTE_GREEN, "line-width": 6 },
    });
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
    el.setAttribute("aria-label", which === "origin" ? "Titik asal" : "Titik tujuan");
    const marker = new maplibregl.Marker({ element: el, draggable: true, anchor: which === "origin" ? "center" : "bottom" })
      .setLngLat([p.lon, p.lat])
      .addTo(this.map);
    marker.on("dragend", () => {
      const ll = marker.getLngLat();
      this.onPointMoved(which, { lat: ll.lat, lon: ll.lng });
    });
    if (which === "origin") this.origin = marker;
    else this.destination = marker;
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
    this.map.fitBounds(bounds, {
      padding: { top: 70, bottom: Math.max(60, inset.bottom), left: 50, right: 70 },
      maxZoom: 16,
      duration: 600,
    });
  }

  async clearRoutes(): Promise<void> {
    await this.ready;
    this.routeSource().setData(emptyCollection());
  }

  // --------------------------------------------------------------- heatmap

  async setSurface(surface: ExposureSurface | null): Promise<void> {
    await this.ready;
    this.surface = surface?.features ?? [];
    this.renderHeat();
  }

  async setHeatLayer(layer: HeatLayer): Promise<void> {
    this.heatLayer = layer;
    await this.ready;
    this.renderHeat();
  }

  /** Dims the heatmap when it no longer matches the latest request. */
  async setHeatDimmed(dimmed: boolean): Promise<void> {
    await this.ready;
    const m = this.map;
    m.setPaintProperty("heat-fill", "fill-opacity", dimmed ? 0.25 : 0.6);
    m.setPaintProperty("heat-line", "line-opacity", dimmed ? 0.35 : 0.92);
  }

  /** Colours always come from the ratio to the WHO guideline, so switching the
   *  layer re-colours data already in memory and sends no request. */
  private renderHeat(): void {
    // `ratio` is the WHO-guideline ratio of whichever layer is shown, so the
    // paint expressions never change; `i` indexes back into this.surface on click.
    const features: GeoJSON.Feature[] = this.surface.map((f, i) => ({
      type: "Feature",
      geometry: f.geometry,
      properties: { ratio: layerRatio(this.heatLayer, f), confidence: f.confidence, i },
    }));
    (this.map.getSource(HEAT_SOURCE) as maplibregl.GeoJSONSource).setData({ type: "FeatureCollection", features });
  }

  async showHeat(visible: boolean): Promise<void> {
    await this.ready;
    for (const id of ["heat-fill", "heat-fill-hatch", "heat-line", "heat-line-hatch"]) {
      this.map.setLayoutProperty(id, "visibility", visible ? "visible" : "none");
    }
    if (!visible) this.closePopup();
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
