import {
  DETAIL_ZOOM,
  MAX_DETAIL_AREA_KM2,
  ROUTE_TIMEOUT_MS,
  SURFACE_TIMEOUT_MS,
} from "./config";
import { isOlderThanStaleLimit } from "./format";
import type {
  BBox,
  ExposureSurface,
  ExposureUnit,
  FallbackReason,
  RouteMetadata,
  RouteRequest,
  RouteResponse,
  RouteResult,
  RouteSource,
  SurfaceFeature,
} from "./types";

export type ApiErrorKind = "http" | "timeout" | "network" | "invalid_response" | "aborted";

/** Every failure the API layer can produce. `code` is the contract's error code
 *  (Tabel 3.16) when the server sent one. */
export class ApiError extends Error {
  readonly kind: ApiErrorKind;
  readonly status: number | null;
  readonly code: string | null;
  readonly requestId: string | null;
  readonly retryAfterSec: number | null;

  constructor(
    kind: ApiErrorKind,
    message: string,
    opts: { status?: number; code?: string; requestId?: string; retryAfterSec?: number } = {},
  ) {
    super(message);
    this.name = "ApiError";
    this.kind = kind;
    this.status = opts.status ?? null;
    this.code = opts.code ?? null;
    this.requestId = opts.requestId ?? null;
    this.retryAfterSec = opts.retryAfterSec ?? null;
  }
}

function parseRetryAfter(header: string | null): number | undefined {
  if (!header) return undefined;
  const n = Number(header);
  return Number.isFinite(n) && n >= 0 ? Math.ceil(n) : undefined;
}

async function fetchJson(
  url: string,
  init: RequestInit,
  timeoutMs: number,
  signal?: AbortSignal,
): Promise<unknown> {
  const controller = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);
  const onExternalAbort = () => controller.abort();
  if (signal) {
    if (signal.aborted) controller.abort();
    else signal.addEventListener("abort", onExternalAbort, { once: true });
  }

  try {
    let response: Response;
    try {
      response = await fetch(url, { ...init, signal: controller.signal });
    } catch {
      if (timedOut) throw new ApiError("timeout", "Server tidak merespons dalam batas waktu.");
      if (signal?.aborted) throw new ApiError("aborted", "Permintaan dibatalkan.");
      throw new ApiError("network", "Tidak dapat menghubungi server.");
    }

    let body: unknown;
    try {
      body = await response.json();
    } catch {
      if (timedOut) throw new ApiError("timeout", "Server tidak merespons dalam batas waktu.");
      if (signal?.aborted) throw new ApiError("aborted", "Permintaan dibatalkan.");
      body = undefined;
    }

    if (!response.ok) {
      const err = (body as { error?: { code?: unknown; message?: unknown; request_id?: unknown } } | undefined)?.error;
      throw new ApiError("http", typeof err?.message === "string" ? err.message : `HTTP ${response.status}`, {
        status: response.status,
        code: typeof err?.code === "string" ? err.code : undefined,
        requestId:
          typeof err?.request_id === "string" ? err.request_id : (response.headers.get("X-Request-ID") ?? undefined),
        retryAfterSec: parseRetryAfter(response.headers.get("Retry-After")),
      });
    }
    if (body === undefined) {
      throw new ApiError("invalid_response", "Respons server bukan JSON yang valid.");
    }
    return body;
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", onExternalAbort);
  }
}

// ---------------------------------------------------------------- routes

export async function submitRouteRequest(request: RouteRequest, signal?: AbortSignal): Promise<RouteResponse> {
  const body = await fetchJson(
    "/v1/routes",
    {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/geo+json, application/json" },
      body: JSON.stringify(request),
    },
    ROUTE_TIMEOUT_MS,
    signal,
  );
  return parseRouteResponse(body);
}

function invalid(what: string): ApiError {
  return new ApiError("invalid_response", `Respons server tidak sesuai kontrak (${what}).`);
}

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const str = (v: unknown): string | null => (typeof v === "string" && v !== "" ? v : null);

function parseCoordinates(geometry: unknown): [number, number][] | null {
  if (!isObj(geometry) || geometry.type !== "LineString" || !Array.isArray(geometry.coordinates)) return null;
  const out: [number, number][] = [];
  for (const c of geometry.coordinates) {
    if (!Array.isArray(c)) return null;
    const lon = num(c[0]);
    const lat = num(c[1]);
    // GeoJSON is [lon, lat]; a swapped pair would put Jakarta in the ocean.
    if (lon === null || lat === null || Math.abs(lat) > 90 || Math.abs(lon) > 180) return null;
    out.push([lon, lat]);
  }
  return out.length >= 2 ? out : null;
}

function parseRouteFeature(feature: unknown): { result: RouteResult; unit: ExposureUnit } | null {
  if (!isObj(feature) || !isObj(feature.properties)) return null;
  const p = feature.properties;
  const role = p.role === "recommended" || p.role === "comparison" ? p.role : null;
  const coordinates = parseCoordinates(feature.geometry);
  const travelTimeMin = num(p.travel_time_min);
  const distanceM = num(p.distance_m);
  if (!role || !coordinates || travelTimeMin === null || distanceM === null) return null;

  const index = num(p.exposure_index_min);
  const legacy = num(p.exposure_ug_min_m3);
  const exposure = index ?? legacy;
  if (exposure === null) return null;

  const routeSource: RouteSource = p.route_source === "baseline" ? "baseline" : "policy";
  return {
    unit: index !== null ? "index_min" : "ug_min_m3",
    result: {
      role,
      routeSource,
      preferenceApplied: typeof p.preference_applied === "boolean" ? p.preference_applied : null,
      travelTimeMin,
      distanceM,
      exposure,
      confidence: num(p.confidence_score),
      coordinates,
    },
  };
}

const FALLBACK_REASONS: readonly FallbackReason[] = ["policy_timeout", "policy_error", "invalid_route"];

function parseModelVersion(v: unknown): { downscaling: string | null; routePolicy: string | null } {
  if (isObj(v)) {
    return { downscaling: str(v.downscaling), routePolicy: str(v.route_policy) };
  }
  // The internal IRouteSolve contract sends one string; show it as the policy version.
  return { downscaling: null, routePolicy: str(v) ?? (num(v) !== null ? String(v) : null) };
}

/** Turns the GeoJSON FeatureCollection of IRouteQuery into a RouteResponse.
 *  Throws "no_route_found" when no recommended route came back, and
 *  "invalid_response" when the payload does not follow the contract. */
export function parseRouteResponse(body: unknown): RouteResponse {
  if (!isObj(body) || body.type !== "FeatureCollection" || !Array.isArray(body.features)) {
    throw invalid("bukan FeatureCollection");
  }
  const meta = isObj(body.metadata) ? body.metadata : {};

  let recommended: RouteResult | null = null;
  let comparison: RouteResult | null = null;
  let unit: ExposureUnit = "index_min";
  for (const f of body.features) {
    const parsed = parseRouteFeature(f);
    if (!parsed) throw invalid("fitur rute tidak lengkap");
    unit = parsed.unit;
    if (parsed.result.role === "recommended") recommended ??= parsed.result;
    else comparison ??= parsed.result;
  }
  if (!recommended) {
    throw new ApiError("http", "Server tidak mengembalikan rute rekomendasi.", {
      status: 200,
      code: "no_route_found",
      requestId: str(meta.request_id) ?? undefined,
    });
  }

  const timeWindowStart = str(meta.time_window_start);
  const delta = isObj(meta.comparison) ? meta.comparison : {};
  const fallback = FALLBACK_REASONS.find((r) => r === meta.fallback_reason) ?? null;
  const version = parseModelVersion(meta.model_version);

  const metadata: RouteMetadata = {
    requestId: str(meta.request_id),
    timeWindowStart,
    dataStale: typeof meta.data_stale === "boolean" ? meta.data_stale : isOlderThanStaleLimit(timeWindowStart),
    missingEdgeCount: Math.max(0, num(meta.missing_edge_count) ?? 0),
    fallbackReason: fallback,
    downscalingVersion: version.downscaling,
    routePolicyVersion: version.routePolicy,
    deltaTravelTimeMin: num(delta.delta_travel_time_min),
    deltaExposurePct: num(delta.delta_exposure_pct),
    disclaimer: str(meta.disclaimer),
  };
  return { recommended, comparison, exposureUnit: unit, metadata };
}

// ------------------------------------------------------- exposure surface

export function bboxAreaKm2(b: BBox): number {
  const midLat = ((b.minLat + b.maxLat) / 2) * (Math.PI / 180);
  const heightKm = (b.maxLat - b.minLat) * 111.32;
  const widthKm = (b.maxLon - b.minLon) * 111.32 * Math.cos(midLat);
  return Math.max(0, heightKm * widthKm);
}

/** Zoom level to send. A detailed (>= 14) request over more than 25 km² is a 400
 *  invalid_request, so the aggregated level is requested instead. */
export function surfaceZoom(mapZoom: number, bbox: BBox): number {
  const z = Math.floor(mapZoom);
  return z >= DETAIL_ZOOM && bboxAreaKm2(bbox) > MAX_DETAIL_AREA_KM2 ? DETAIL_ZOOM - 1 : z;
}

export async function requestExposureSurface(
  bbox: BBox,
  zoom: number,
  signal?: AbortSignal,
): Promise<ExposureSurface> {
  const q = new URLSearchParams({
    bbox: [bbox.minLon, bbox.minLat, bbox.maxLon, bbox.maxLat].map((n) => n.toFixed(5)).join(","),
    zoom: String(zoom),
  });
  const body = await fetchJson(`/v1/exposure-surface?${q}`, { headers: { Accept: "application/geo+json" } }, SURFACE_TIMEOUT_MS, signal);
  return parseExposureSurface(body);
}

export function parseExposureSurface(body: unknown): ExposureSurface {
  if (!isObj(body) || body.type !== "FeatureCollection" || !Array.isArray(body.features)) {
    throw invalid("bukan FeatureCollection");
  }
  const features: SurfaceFeature[] = [];
  for (const f of body.features) {
    if (!isObj(f) || !isObj(f.geometry) || typeof f.geometry.type !== "string") continue;
    const type = f.geometry.type;
    if (!["LineString", "MultiLineString", "Polygon", "MultiPolygon"].includes(type)) continue;
    const p = isObj(f.properties) ? f.properties : {};
    features.push({
      geometry: f.geometry as unknown as GeoJSON.Geometry,
      name: str(p.name),
      exposureIndex: num(p.exposure_index),
      pm25: num(p.pm25_ugm3),
      no2: num(p.no2_ugm3),
      confidence: num(p.confidence_score),
    });
  }
  const meta = isObj(body.metadata) ? body.metadata : {};
  const timeWindowStart = str(meta.time_window_start);
  return {
    features,
    timeWindowStart,
    dataStale: typeof meta.data_stale === "boolean" ? meta.data_stale : isOlderThanStaleLimit(timeWindowStart),
  };
}
