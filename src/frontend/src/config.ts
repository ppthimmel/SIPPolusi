// Constants shared by the Frontend. Numbers that come from the design document
// (Bab III) are referenced by subbab so they can be re-checked against it.

/** Study area the Backend serves (outside_study_area, Tabel 3.16). Jakarta. */
export const STUDY_AREA = {
  minLon: 106.68,
  minLat: -6.38,
  maxLon: 106.98,
  maxLat: -6.08,
} as const;

export const MAP_CENTER: [number, number] = [106.8456, -6.2088];
export const MAP_ZOOM = 12;

/** Client-side timeouts (Tabel 3.15): route 12 s, heatmap 5 s. VITE_ROUTE_TIMEOUT_MS and
 *  VITE_SURFACE_TIMEOUT_MS override them at build time; keep the route timeout above the
 *  gateway's GATEWAY_UPSTREAM_TIMEOUT_S so the gateway's own error reaches the page. */
export const ROUTE_TIMEOUT_MS = timeoutMs(import.meta.env?.VITE_ROUTE_TIMEOUT_MS, 12_000);
export const SURFACE_TIMEOUT_MS = timeoutMs(import.meta.env?.VITE_SURFACE_TIMEOUT_MS, 5_000);

/** A positive whole number of milliseconds from a build variable, else the default. */
export function timeoutMs(value: string | undefined, fallback: number): number {
  const n = Number(value);
  return value && Number.isInteger(n) && n > 0 ? n : fallback;
}
/** Place search (TI-SE-12). The timeout stays above the Backend's API_GEOCODING_TIMEOUT_S, plus
 *  its second call for a place outside the area, so the page reads the Backend's own answer. */
export const GEOCODE_TIMEOUT_MS = 7_000;
export const GEOCODE_DEBOUNCE_MS = 300;
export const GEOCODE_MIN_CHARS = 3;

/** Debounce for pan/zoom before GET /v1/exposure-surface (Tabel 3.4). */
export const SURFACE_DEBOUNCE_MS = 500;

/** Below this zoom the Backend aggregates segments into 250 m – 1 km cells; at or
 *  above it, bbox may cover at most MAX_DETAIL_AREA_KM2 (get_exposure_surface). */
export const DETAIL_ZOOM = 14;
export const MAX_DETAIL_AREA_KM2 = 25;

/** Air-quality time window older than this is "belum diperbarui" (data_stale). */
export const STALE_AFTER_HOURS = 3;

/** WHO 2021 24-hour guideline values in µg/m³ (subbab 3.1, 3.5.1). */
export const WHO_GUIDELINE = { pm25: 15, no2: 25 } as const;

/** Confidence badge thresholds (subbab 3.1): <0.4 rendah, <0.7 sedang, else tinggi. */
export const CONFIDENCE_LOW = 0.4;
export const CONFIDENCE_HIGH = 0.7;

/** Two points closer than this are treated as the same place. */
export const MIN_TRIP_DISTANCE_M = 30;

/** Basemap raster tiles. Override with VITE_BASEMAP_TILES for production tiles. */
export const BASEMAP_TILES: string =
  import.meta.env?.VITE_BASEMAP_TILES || "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
export const BASEMAP_ATTRIBUTION = "© OpenStreetMap contributors";

