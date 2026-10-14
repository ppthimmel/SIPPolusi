import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  ApiError,
  bboxAreaKm2,
  parseExposureSurface,
  parseRouteResponse,
  requestExposureSurface,
  submitRouteRequest,
  surfaceZoom,
} from "./api";
import { buildRouteRequest } from "./validation";

const request = buildRouteRequest({ lat: -6.2088, lon: 106.8456 }, { lat: -6.225, lon: 106.8 }, "walk", 0.5);

function feature(role: string, over: Record<string, unknown> = {}, coords: unknown = [[106.8456, -6.2088], [106.8, -6.225]]) {
  return {
    type: "Feature",
    geometry: { type: "LineString", coordinates: coords },
    properties: {
      role,
      route_source: role === "recommended" ? "policy" : "baseline",
      travel_time_min: 28.4,
      distance_m: 2215,
      exposure_index_min: 34.8,
      confidence_score: 0.71,
      ...over,
    },
  };
}

/** The sample response of subbab 3.5.2, with the index-based exposure field. */
const sample = () => ({
  type: "FeatureCollection",
  features: [feature("recommended"), feature("comparison", { travel_time_min: 24.1, distance_m: 1930, exposure_index_min: 51.2, confidence_score: 0.68 })],
  metadata: {
    request_id: "7f3c2a9e",
    time_window_start: "2026-09-21T05:00:00Z",
    data_stale: false,
    missing_edge_count: 3,
    fallback_reason: null,
    model_version: { downscaling: "12", route_policy: "4" },
    comparison: { delta_travel_time_min: 4.3, delta_exposure_pct: -31.9 },
    disclaimer: "Estimasi …",
  },
});

const json = (body: unknown, status = 200, headers: Record<string, string> = {}) =>
  new Response(typeof body === "string" ? body : JSON.stringify(body), { status, headers });

describe("parseRouteResponse (UT-FE-04)", () => {
  it("reads the contract's sample response", () => {
    const r = parseRouteResponse(sample());
    expect(r.recommended.travelTimeMin).toBe(28.4);
    expect(r.comparison?.exposure).toBe(51.2);
    expect(r.exposureUnit).toBe("index_min");
    expect(r.metadata).toMatchObject({
      requestId: "7f3c2a9e",
      dataStale: false,
      missingEdgeCount: 3,
      downscalingVersion: "12",
      routePolicyVersion: "4",
      deltaTravelTimeMin: 4.3,
      deltaExposurePct: -31.9,
    });
  });

  it("keeps coordinates as [lon, lat], first point at the origin", () => {
    const r = parseRouteResponse(sample());
    expect(r.recommended.coordinates[0]).toEqual([106.8456, -6.2088]);
  });

  it("rejects swapped [lat, lon] coordinates instead of drawing them in the ocean", () => {
    const body = sample();
    body.features[0] = feature("recommended", {}, [[-6.2088, 206.8456], [-6.225, 106.8]]);
    expect(() => parseRouteResponse(body)).toThrowError(ApiError);
  });

  it("falls back to the µg·min/m³ field and says so", () => {
    const body = sample();
    for (const f of body.features) {
      const p = f.properties as Record<string, unknown>;
      p.exposure_ug_min_m3 = p.exposure_index_min;
      delete p.exposure_index_min;
    }
    expect(parseRouteResponse(body).exposureUnit).toBe("ug_min_m3");
  });

  it("accepts a single model_version string and a missing comparison route", () => {
    const body = sample();
    body.features.pop();
    (body.metadata as Record<string, unknown>).model_version = "4";
    const r = parseRouteResponse(body);
    expect(r.comparison).toBeNull();
    expect(r.metadata.routePolicyVersion).toBe("4");
  });

  it("derives data_stale from the time window when the flag is missing", () => {
    const body = sample();
    delete (body.metadata as Record<string, unknown>).data_stale;
    (body.metadata as Record<string, unknown>).time_window_start = "2020-01-01T00:00:00Z";
    expect(parseRouteResponse(body).metadata.dataStale).toBe(true);
  });

  it("maps a response without a recommended route to no_route_found", () => {
    const body = { ...sample(), features: [] };
    try {
      parseRouteResponse(body);
      expect.unreachable();
    } catch (e) {
      expect(e).toBeInstanceOf(ApiError);
      expect((e as ApiError).code).toBe("no_route_found");
    }
  });

  it.each([
    ["the current placeholder body", { route: null, preference_applied: true, message: "not implemented" }],
    ["not an object", "x"],
    ["a feature without metrics", { type: "FeatureCollection", features: [feature("recommended", { travel_time_min: "28" })] }],
    ["a route with one point", { type: "FeatureCollection", features: [feature("recommended", {}, [[106.8, -6.2]])] }],
  ])("rejects %s as invalid_response", (_name, body) => {
    try {
      parseRouteResponse(body);
      expect.unreachable();
    } catch (e) {
      expect((e as ApiError).kind).toBe("invalid_response");
    }
  });
});

describe("submitRouteRequest (UT-FE-03)", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("posts the request body and parses a 200", async () => {
    const fetchMock = vi.fn().mockResolvedValue(json(sample()));
    vi.stubGlobal("fetch", fetchMock);
    const r = await submitRouteRequest(request);
    expect(r.recommended.distanceM).toBe(2215);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/v1/routes");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual(request);
  });

  it("surfaces 429 with the Retry-After seconds", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(json({ error: { code: "rate_limited", message: "x", request_id: "r1" } }, 429, { "Retry-After": "30" })),
    );
    const err = await submitRouteRequest(request).catch((e) => e);
    expect(err).toMatchObject({ kind: "http", status: 429, code: "rate_limited", retryAfterSec: 30, requestId: "r1" });
  });

  it("carries the error code and message of a 422", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(json({ error: { code: "point_not_on_network", message: "Titik tujuan berjarak …", request_id: "r2" } }, 422)),
    );
    const err = await submitRouteRequest(request).catch((e) => e);
    expect(err).toMatchObject({ status: 422, code: "point_not_on_network", message: "Titik tujuan berjarak …" });
  });

  it("handles a 503 with JSON error and a 502 with an HTML body", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(json({ error: { code: "cache_unavailable", message: "m", request_id: "r" } }, 503)));
    expect(await submitRouteRequest(request).catch((e) => e)).toMatchObject({ status: 503, code: "cache_unavailable" });

    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(json("<html>502 Bad Gateway</html>", 502)));
    expect(await submitRouteRequest(request).catch((e) => e)).toMatchObject({ kind: "http", status: 502, code: null });
  });

  it("treats a 200 with a non-JSON body as invalid_response", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json("ini bukan json")));
    expect(await submitRouteRequest(request).catch((e) => e)).toMatchObject({ kind: "invalid_response" });
  });

  it("reports a network failure", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    expect(await submitRouteRequest(request).catch((e) => e)).toMatchObject({ kind: "network" });
  });

  const hangingFetch = () =>
    vi.fn((_url: string, init: RequestInit) => new Promise((_resolve, reject) => {
      init.signal!.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
    }));

  it("gives up after 12 seconds and aborts the request (UT-FE-03d)", async () => {
    const fetchMock = hangingFetch();
    vi.stubGlobal("fetch", fetchMock);
    const pending = submitRouteRequest(request).catch((e) => e);
    await vi.advanceTimersByTimeAsync(11_999);
    expect(fetchMock.mock.calls[0][1].signal!.aborted).toBe(false);
    await vi.advanceTimersByTimeAsync(2);
    expect(await pending).toMatchObject({ kind: "timeout" });
    expect(fetchMock.mock.calls[0][1].signal!.aborted).toBe(true);
  });

  it("distinguishes a user cancel from a timeout", async () => {
    vi.stubGlobal("fetch", hangingFetch());
    const ctrl = new AbortController();
    const pending = submitRouteRequest(request, ctrl.signal).catch((e) => e);
    ctrl.abort();
    expect(await pending).toMatchObject({ kind: "aborted" });
  });
});

describe("exposure surface", () => {
  afterEach(() => vi.unstubAllGlobals());

  const surface = () => ({
    type: "FeatureCollection",
    features: [
      { type: "Feature", geometry: { type: "LineString", coordinates: [[106.8, -6.2], [106.801, -6.2]] }, properties: { name: "Jl. Jend. Sudirman", exposure_index: 3.07, pm25_ugm3: 71, no2_ugm3: 36, confidence_score: 0.74 } },
      { type: "Feature", geometry: { type: "Point", coordinates: [106.8, -6.2] }, properties: {} },
      { type: "Feature", geometry: null, properties: {} },
      { type: "Feature", geometry: { type: "LineString", coordinates: [[106.8, -6.2], [106.8, -6.201]] }, properties: { exposure_index: "tinggi", no2_ugm3: null } },
    ],
    metadata: { time_window_start: "2026-09-21T05:00:00Z", data_stale: true },
  });

  it("keeps drawable features and nulls out unusable values (UT-FE-07)", () => {
    const s = parseExposureSurface(surface());
    expect(s.features).toHaveLength(2);
    expect(s.features[0]).toMatchObject({ name: "Jl. Jend. Sudirman", exposureIndex: 3.07, pm25: 71, no2: 36, confidence: 0.74 });
    expect(s.features[1]).toMatchObject({ exposureIndex: null, no2: null, confidence: null });
    expect(s.dataStale).toBe(true);
  });

  it("accepts an empty FeatureCollection without error (UT-FE-07d)", () => {
    expect(parseExposureSurface({ type: "FeatureCollection", features: [] }).features).toEqual([]);
  });

  it("sends bbox and zoom on the query string (UT-FE-05b)", async () => {
    const fetchMock = vi.fn().mockResolvedValue(json(surface()));
    vi.stubGlobal("fetch", fetchMock);
    await requestExposureSurface({ minLon: 106.8, minLat: -6.22, maxLon: 106.83, maxLat: -6.19 }, 15);
    const url = new URL(fetchMock.mock.calls[0][0], "http://x");
    expect(url.pathname).toBe("/v1/exposure-surface");
    expect(url.searchParams.get("bbox")).toBe("106.80000,-6.22000,106.83000,-6.19000");
    expect(url.searchParams.get("zoom")).toBe("15");
  });

  it("rejects a malformed surface instead of drawing it", () => {
    expect(() => parseExposureSurface({ features: [] })).toThrowError(ApiError);
  });

  it("requests the aggregated zoom when a detailed bbox would exceed 25 km²", () => {
    const small = { minLon: 106.8, minLat: -6.22, maxLon: 106.83, maxLat: -6.19 }; // ~11 km²
    const big = { minLon: 106.75, minLat: -6.25, maxLon: 106.85, maxLat: -6.15 }; // ~120 km²
    expect(bboxAreaKm2(small)).toBeLessThan(25);
    expect(bboxAreaKm2(big)).toBeGreaterThan(25);
    expect(surfaceZoom(15.4, small)).toBe(15);
    expect(surfaceZoom(15.4, big)).toBe(13);
    expect(surfaceZoom(12.7, big)).toBe(12);
  });
});
