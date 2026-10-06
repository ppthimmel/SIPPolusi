// Frontend view of the API contract (design document subbab 3.5.2, IRouteQuery and
// IExposureSurface). The wire format is normalised into these types by api.ts so
// the rest of the UI never touches raw API data.

export type Mode = "walk" | "bike";
export type Pollutant = "pm25" | "no2";
export type RouteSource = "policy" | "baseline";
export type FallbackReason = "policy_timeout" | "policy_error" | "invalid_route";
export type HeatLayer = "combined" | "pm25" | "no2";

export interface Point {
  lat: number;
  lon: number;
}

export interface Preference {
  /** Weight on travel time. */
  alpha: number;
  /** Weight on exposure; always 1 - alpha. */
  beta: number;
}

export interface RouteRequest {
  origin: Point;
  destination: Point;
  mode: Mode;
  pollutant: Pollutant;
  preference: Preference;
}

/** "index_min" = indeks·menit (the design's unit); "ug_min_m3" is what the
 *  contract's sample response carries when no index is sent. */
export type ExposureUnit = "index_min" | "ug_min_m3";

export interface RouteResult {
  role: "recommended" | "comparison";
  routeSource: RouteSource;
  /** null when the Backend did not say. */
  preferenceApplied: boolean | null;
  travelTimeMin: number;
  distanceM: number;
  exposure: number;
  /** null when the Backend sent no score. */
  confidence: number | null;
  /** [lon, lat] pairs. */
  coordinates: [number, number][];
}

export interface RouteMetadata {
  requestId: string | null;
  timeWindowStart: string | null;
  dataStale: boolean;
  missingEdgeCount: number;
  fallbackReason: FallbackReason | null;
  downscalingVersion: string | null;
  routePolicyVersion: string | null;
  deltaTravelTimeMin: number | null;
  deltaExposurePct: number | null;
  disclaimer: string | null;
}

export interface RouteResponse {
  recommended: RouteResult;
  /** null when the Backend returned only one route. */
  comparison: RouteResult | null;
  exposureUnit: ExposureUnit;
  metadata: RouteMetadata;
}

/** One road segment (zoom >= 14) or aggregated grid cell (zoom < 14). */
export interface SurfaceFeature {
  geometry: GeoJSON.Geometry;
  name: string | null;
  exposureIndex: number | null;
  pm25: number | null;
  no2: number | null;
  confidence: number | null;
}

export interface ExposureSurface {
  features: SurfaceFeature[];
  timeWindowStart: string | null;
  dataStale: boolean;
}

export interface BBox {
  minLon: number;
  minLat: number;
  maxLon: number;
  maxLat: number;
}
