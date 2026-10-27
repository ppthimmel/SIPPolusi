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

/** Client-side timeouts (Tabel 3.15). */
export const ROUTE_TIMEOUT_MS = 12_000;
export const SURFACE_TIMEOUT_MS = 5_000;
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

/** Always-visible disclaimer (PNF-07). The Backend sends its own text in
 *  metadata.disclaimer; this one is shown regardless of the response. */
export const DISCLAIMER =
  "Nilai paparan adalah estimasi model dari data satelit dan sensor darat, bukan pengukuran langsung. " +
  "Indeks paparan menggabungkan PM2.5 dan NO2 terhadap pedoman WHO. " +
  "Hasil ini alat bantu keputusan perjalanan, bukan rekomendasi medis atau diagnosis kesehatan; " +
  "tetap perhatikan kondisi jalan.";
