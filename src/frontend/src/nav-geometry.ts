// Route geometry for navigation (TI-SE-13): distances along the route line, snapping a
// position onto it, and turn instructions derived from the line's shape alone. The
// route response carries no street names, so instructions name the turn and distance.
import type { Point } from "./types";

const EARTH_RADIUS_M = 6_371_008.8;
const rad = (deg: number) => (deg * Math.PI) / 180;
const deg = (r: number) => (r * 180) / Math.PI;

/** Great-circle distance in metres. */
export function distanceM(a: Point, b: Point): number {
  const dLat = rad(b.lat - a.lat);
  const dLon = rad(b.lon - a.lon);
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(rad(a.lat)) * Math.cos(rad(b.lat)) * Math.sin(dLon / 2) ** 2;
  return 2 * EARTH_RADIUS_M * Math.asin(Math.min(1, Math.sqrt(h)));
}

/** Initial bearing from a to b, degrees clockwise from north in [0, 360). */
export function bearingDeg(a: Point, b: Point): number {
  const y = Math.sin(rad(b.lon - a.lon)) * Math.cos(rad(b.lat));
  const x = Math.cos(rad(a.lat)) * Math.sin(rad(b.lat)) - Math.sin(rad(a.lat)) * Math.cos(rad(b.lat)) * Math.cos(rad(b.lon - a.lon));
  return (deg(Math.atan2(y, x)) + 360) % 360;
}

/** Signed change of direction from `from` to `to`, in (-180, 180]; positive turns right. */
export function turnAngle(from: number, to: number): number {
  const d = (((to - from) % 360) + 360) % 360;
  return d > 180 ? d - 360 : d;
}

/** A route line with the distance from its start to each vertex. */
export interface RouteLine {
  points: Point[];
  /** cum[i] is the distance along the line to points[i]; cum[0] = 0. */
  cum: number[];
  length: number;
}

/** From [lon, lat] pairs (the API's order). Repeated vertices are dropped. */
export function routeLine(coordinates: readonly [number, number][]): RouteLine {
  const points: Point[] = [];
  for (const [lon, lat] of coordinates) {
    const p = { lat, lon };
    if (!points.length || distanceM(points[points.length - 1], p) > 0.5) points.push(p);
  }
  const cum = [0];
  for (let i = 1; i < points.length; i++) cum.push(cum[i - 1] + distanceM(points[i - 1], points[i]));
  return { points, cum, length: cum[cum.length - 1] ?? 0 };
}

/** Metres east and north of `ref`. Over a few kilometres the error is far below GPS accuracy. */
function local(p: Point, ref: Point): [number, number] {
  return [rad(p.lon - ref.lon) * EARTH_RADIUS_M * Math.cos(rad(ref.lat)), rad(p.lat - ref.lat) * EARTH_RADIUS_M];
}

export interface Snap {
  /** Distance along the line to the snapped point. */
  along: number;
  /** Distance from the position to the line. */
  offsetM: number;
  point: Point;
}

/** Nearest point of the line to `p` between `minAlong` and `maxAlong`. The window keeps a
 *  route that passes the same street twice (or returns on the other carriageway after a
 *  U-turn) from snapping onto the other pass, back or ahead. */
export function snapToLine(line: RouteLine, p: Point, minAlong = 0, maxAlong = Infinity): Snap {
  const { points, cum } = line;
  if (points.length === 1) return { along: 0, offsetM: distanceM(points[0], p), point: points[0] };
  let best: Snap = { along: 0, offsetM: Infinity, point: points[0] };
  for (let i = 0; i < points.length - 1; i++) {
    if (cum[i + 1] < minAlong) continue;
    if (cum[i] > maxAlong) break;
    const a = points[i];
    const [bx, by] = local(points[i + 1], a);
    const [px, py] = local(p, a);
    const len2 = bx * bx + by * by;
    let t = len2 ? (px * bx + py * by) / len2 : 0;
    t = Math.min(1, Math.max(0, t));
    const along = cum[i] + t * (cum[i + 1] - cum[i]);
    if (along < minAlong) t = (minAlong - cum[i]) / Math.max(cum[i + 1] - cum[i], 1e-9);
    else if (along > maxAlong) t = (maxAlong - cum[i]) / Math.max(cum[i + 1] - cum[i], 1e-9);
    const point = { lat: a.lat + t * (points[i + 1].lat - a.lat), lon: a.lon + t * (points[i + 1].lon - a.lon) };
    const offsetM = distanceM(point, p);
    if (offsetM < best.offsetM) best = { along: cum[i] + t * (cum[i + 1] - cum[i]), offsetM, point };
  }
  return best;
}

/** The point at `along` metres from the start, clamped to the line. */
export function pointAt(line: RouteLine, along: number): Point {
  const { points, cum, length } = line;
  if (points.length === 1 || along <= 0) return points[0];
  if (along >= length) return points[points.length - 1];
  let i = 1;
  while (cum[i] < along) i++;
  const t = (along - cum[i - 1]) / (cum[i] - cum[i - 1]);
  const a = points[i - 1];
  const b = points[i];
  return { lat: a.lat + t * (b.lat - a.lat), lon: a.lon + t * (b.lon - a.lon) };
}

/** Direction of travel at `along`. */
export function bearingAt(line: RouteLine, along: number): number {
  const { length } = line;
  const from = Math.min(Math.max(0, along), Math.max(0, length - 1));
  return bearingDeg(pointAt(line, from), pointAt(line, Math.min(length, from + 1)));
}

export type TurnKind = "slight_left" | "slight_right" | "left" | "right" | "sharp_left" | "sharp_right" | "uturn";

export interface Maneuver {
  along: number;
  kind: TurnKind | "arrive";
  /** Signed turn angle in degrees; 0 for arrival. */
  angle: number;
}

/** Directions are compared this far before and after a vertex, so a curve drawn with many
 *  short segments, or a small jog in the geometry, reads as the turn a person would make. */
const LOOK_M = 20;
/** Of turns closer together than this, only the sharpest is announced. */
const MERGE_M = 25;
/** A change of direction below this is "keep going". */
const MIN_TURN_DEG = 30;

export function classifyTurn(angle: number): TurnKind | null {
  const a = Math.abs(angle);
  if (a < MIN_TURN_DEG) return null;
  if (a >= 165) return "uturn";
  const side = angle > 0 ? "right" : "left";
  if (a < 60) return `slight_${side}`;
  if (a < 135) return side;
  return `sharp_${side}`;
}

/** Turns along the line, in order, ending with the arrival. */
export function maneuvers(line: RouteLine): Maneuver[] {
  const { points, cum, length } = line;
  const found: Maneuver[] = [];
  for (let i = 1; i < points.length - 1; i++) {
    const at = cum[i];
    const before = pointAt(line, Math.max(0, at - LOOK_M));
    const after = pointAt(line, Math.min(length, at + LOOK_M));
    if (distanceM(before, points[i]) < 1 || distanceM(points[i], after) < 1) continue;
    const angle = turnAngle(bearingDeg(before, points[i]), bearingDeg(points[i], after));
    const kind = classifyTurn(angle);
    if (!kind) continue;
    const last = found[found.length - 1];
    if (last && at - last.along < MERGE_M) {
      if (Math.abs(angle) > Math.abs(last.angle)) found[found.length - 1] = { along: at, kind, angle };
      continue;
    }
    found.push({ along: at, kind, angle });
  }
  found.push({ along: length, kind: "arrive", angle: 0 });
  return found;
}

/** Points on a circle of `radiusM` around `center`, closed, as [lon, lat] pairs. */
export function circle(center: Point, radiusM: number, steps = 40): [number, number][] {
  const ring: [number, number][] = [];
  const dLat = deg(radiusM / EARTH_RADIUS_M);
  const dLon = dLat / Math.cos(rad(center.lat));
  for (let i = 0; i <= steps; i++) {
    const a = (2 * Math.PI * i) / steps;
    ring.push([center.lon + dLon * Math.sin(a), center.lat + dLat * Math.cos(a)]);
  }
  return ring;
}
