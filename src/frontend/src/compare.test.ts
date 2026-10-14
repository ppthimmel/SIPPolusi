import { describe, expect, it } from "vitest";
import { compareRoutes } from "./compare";
import type { RouteMetadata, RouteResponse, RouteResult } from "./types";

const line: [number, number][] = [
  [106.8, -6.2],
  [106.81, -6.21],
];
const other: [number, number][] = [
  [106.8, -6.2],
  [106.82, -6.22],
];
const route = (over: Partial<RouteResult>): RouteResult => ({
  role: "recommended",
  routeSource: "policy",
  preferenceApplied: true,
  travelTimeMin: 24,
  distanceM: 2000,
  exposure: 30,
  confidence: 0.7,
  coordinates: line,
  ...over,
});
const meta: RouteMetadata = {
  requestId: null,
  timeWindowStart: null,
  dataStale: false,
  missingEdgeCount: 0,
  fallbackReason: null,
  downscalingVersion: null,
  routePolicyVersion: null,
  deltaTravelTimeMin: null,
  deltaExposurePct: null,
  disclaimer: null,
};
const response = (rec: RouteResult, cmp: RouteResult | null, m: Partial<RouteMetadata> = {}): RouteResponse => ({
  recommended: rec,
  comparison: cmp,
  exposureUnit: "index_min",
  metadata: { ...meta, ...m },
});

describe("compareRoutes (UT-FE-08)", () => {
  it("derives +4 minutes and −25.0% from the two routes", () => {
    const c = compareRoutes(response(route({}), route({ role: "comparison", travelTimeMin: 20, exposure: 40, coordinates: other })));
    expect(c.kind).toBe("lower");
    expect(c.deltaTimeMin).toBe(4);
    expect(c.deltaExposurePct).toBeCloseTo(-25, 9);
  });
  it("prefers the deltas computed by the Backend", () => {
    const c = compareRoutes(
      response(route({}), route({ role: "comparison", travelTimeMin: 20, exposure: 40, coordinates: other }), {
        deltaTravelTimeMin: 4.3,
        deltaExposurePct: -31.9,
      }),
    );
    expect(c).toEqual({ kind: "lower", deltaTimeMin: 4.3, deltaExposurePct: -31.9 });
  });
  it("reports identical routes, as in a fallback response", () => {
    const c = compareRoutes(response(route({}), route({ role: "comparison" })));
    expect(c).toEqual({ kind: "identical", deltaTimeMin: 0, deltaExposurePct: 0 });
  });
  it("flags higher exposure instead of calling it a reduction", () => {
    const c = compareRoutes(response(route({ exposure: 50 }), route({ role: "comparison", exposure: 40, coordinates: other })));
    expect(c.kind).toBe("higher");
  });
  it("handles a missing comparison route and a zero baseline exposure", () => {
    expect(compareRoutes(response(route({}), null)).kind).toBe("none");
    expect(compareRoutes(response(route({}), route({ role: "comparison", exposure: 0, coordinates: other }))).kind).toBe("unknown");
  });
});
