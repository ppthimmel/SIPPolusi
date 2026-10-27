import { describe, expect, it } from "vitest";
import { ApiError, parseExposureSurface, parseRouteResponse } from "./api";
import { describeRouteError } from "./errors";
import errors from "./fixtures/ti-se-08/errors.json";
import routeFallback from "./fixtures/ti-se-08/route_fallback.json";
import routePolicy from "./fixtures/ti-se-08/route_policy.json";
import surfaceEdges from "./fixtures/ti-se-08/surface_edges_z15.json";
import surfaceGrid from "./fixtures/ti-se-08/surface_grid_z13.json";

// Real responses of the Backend's public API (TI-SE-08), see fixtures/ti-se-08/README.md.

describe("contract with the Backend (TI-SE-08 responses)", () => {
  it("reads a policy route with units, versions and data time", () => {
    const r = parseRouteResponse(routePolicy);
    expect(r.exposureUnit).toBe("index_min");
    expect(r.recommended).toMatchObject({ role: "recommended", routeSource: "policy", preferenceApplied: true });
    expect(r.comparison).toMatchObject({ role: "comparison", routeSource: "baseline", preferenceApplied: false });
    expect(r.recommended.coordinates[0]).toEqual([106.8, -6.2]); // [lon, lat]
    expect(r.metadata).toMatchObject({
      requestId: "contoh-route-policy",
      fallbackReason: null,
      downscalingVersion: "idw-p2-k8",
      routePolicyVersion: "mock-1",
      graphVersion: "v1",
      dataStale: false,
    });
    expect(r.metadata.timeWindowStart).toMatch(/^\d{4}-\d\d-\d\dT\d\d:00:00Z$/);
    expect(r.metadata.disclaimer).toBeTruthy();
  });

  it("reads a Backend fallback with its reason", () => {
    const r = parseRouteResponse(routeFallback);
    expect(r.recommended).toMatchObject({ routeSource: "baseline", preferenceApplied: false });
    expect(r.metadata.fallbackReason).toBe("model_error");
    expect(r.metadata.routePolicyVersion).toBe("astar-baseline");
  });

  it("reads per-edge and grid exposure surfaces", () => {
    const edges = parseExposureSurface(surfaceEdges);
    expect(edges.features).toHaveLength(24);
    expect(edges.features.every((f) => f.geometry.type === "LineString")).toBe(true);
    expect(edges.features.find((f) => f.no2 === null)).toBeDefined(); // missing NO2 stays null, not 0
    const grid = parseExposureSurface(surfaceGrid);
    expect(grid.features.length).toBeGreaterThan(0);
    expect(grid.features.every((f) => f.geometry.type === "Polygon" && f.exposureIndex !== null)).toBe(true);
    expect(grid.timeWindowStart).toBe(edges.timeWindowStart);
  });

  it("maps every documented error to its own message", () => {
    for (const [key, { status, body }] of Object.entries(errors)) {
      const e = body.error as { code: string; message: string; request_id: string };
      const view = describeRouteError(new ApiError("http", e.message, { status, code: e.code, requestId: e.request_id }));
      expect(view.requestId, key).toBe(e.request_id);
      expect(view.title, key).not.toBe("Permintaan ditolak"); // the generic fallback text
    }
    const snap = errors["422_point_not_on_network"].body.error;
    const view = describeRouteError(new ApiError("http", snap.message, { status: 422, code: snap.code }));
    expect(view.field).toBe("destination"); // "Titik tujuan: …" outlines the destination field
  });
});
