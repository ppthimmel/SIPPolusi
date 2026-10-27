import type { RouteResponse, RouteResult } from "./types";

export type ComparisonKind =
  /** No comparison route in the response. */
  | "none"
  /** Recommended route is the comparison route (fallback, or the policy found nothing better). */
  | "identical"
  | "lower"
  | "higher"
  | "same"
  /** Exposure difference cannot be computed (comparison exposure is 0). */
  | "unknown";

export interface Comparison {
  kind: ComparisonKind;
  /** recommended − comparison, minutes. Positive = recommended takes longer. */
  deltaTimeMin: number;
  /** recommended vs comparison, percent. Negative = lower exposure. */
  deltaExposurePct: number | null;
}

function sameRoute(a: RouteResult, b: RouteResult): boolean {
  return (
    a.travelTimeMin === b.travelTimeMin &&
    a.exposure === b.exposure &&
    a.distanceM === b.distanceM &&
    a.coordinates.length === b.coordinates.length &&
    a.coordinates.every((c, i) => c[0] === b.coordinates[i][0] && c[1] === b.coordinates[i][1])
  );
}

/** Difference between the recommended and the fastest (comparison) route
 *  (render_route_comparison). Backend-computed deltas win; otherwise they are
 *  derived from the two routes' own numbers. */
export function compareRoutes(resp: RouteResponse): Comparison {
  const { recommended: rec, comparison: cmp, metadata } = resp;
  if (!cmp) return { kind: "none", deltaTimeMin: 0, deltaExposurePct: null };
  if (sameRoute(rec, cmp)) return { kind: "identical", deltaTimeMin: 0, deltaExposurePct: 0 };

  const deltaTimeMin = metadata.deltaTravelTimeMin ?? rec.travelTimeMin - cmp.travelTimeMin;
  const pct =
    metadata.deltaExposurePct ?? (cmp.exposure > 0 ? ((rec.exposure - cmp.exposure) / cmp.exposure) * 100 : null);
  if (pct === null) return { kind: "unknown", deltaTimeMin, deltaExposurePct: null };
  // Within rounding of the displayed one decimal, call it the same.
  const kind = pct <= -0.05 ? "lower" : pct >= 0.05 ? "higher" : "same";
  return { kind, deltaTimeMin, deltaExposurePct: pct };
}
