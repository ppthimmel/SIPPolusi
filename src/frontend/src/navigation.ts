// Navigation along one route (TI-SE-13): progress, remaining distance and time, the next
// turn, weak signal, off-route and arrival. Everything here runs on the device; positions
// are never stored. The caller asks the Backend for a new route when `rerouteDue` says so,
// and that request is the only time a position leaves the device (PNF-06, RI-09).
import {
  bearingAt,
  bearingDeg,
  distanceM,
  maneuvers,
  routeLine,
  snapCandidates,
  turnAngle,
  type Candidate,
  type Maneuver,
  type Snap,
  type RouteLine,
} from "./nav-geometry";
import type { Mode, Point } from "./types";

/** One position from the Geolocation API (or the development simulator). */
export interface Fix extends Point {
  /** 95% radius in metres, as GeolocationCoordinates.accuracy. */
  accuracyM: number;
  /** Degrees clockwise from north, when the device reports one. */
  headingDeg: number | null;
  speedMps: number | null;
  /** Milliseconds, as GeolocationPosition.timestamp. */
  time: number;
}

/** What navigation needs of a route: its line and the Backend's estimate for it. */
export interface NavRoute {
  coordinates: [number, number][];
  travelTimeMin: number;
}

export const NAV_LIMITS = {
  /** A fix less accurate than this is shown, but not used to judge off-route or arrival. */
  weakAccuracyM: 50,
  /** Off the route by more than this (or by the fix's accuracy, if larger)… */
  offRouteM: 40,
  /** …for this long counts as having left the route. One stray fix never does. */
  offRouteS: 10,
  /** Within this of the destination is arrival. Cycling also needs the route to be nearly done
   *  (see NavigationSession), walking does not. */
  arriveM: 25,
  /** At least this long between two requests for a new route. */
  rerouteIntervalS: 30,
  /** After failed requests the interval doubles, up to this. */
  rerouteMaxIntervalS: 120,
  /** How far back along the route a position may snap. */
  backtrackM: 50,
  /** How far ahead it may snap. A position not found on the route within that is looked for
   *  again up to aheadM + maxSpeedMps for each second since the last fix on the route (no limit
   *  before the first one), so a user who moved on during a gap, or starts mid-route, is found. */
  aheadM: 60,
  maxSpeedMps: 10,
  /** Where the route passes a place twice (the carriageways either side of a putar balik), the
   *  pass whose direction is within this of the user's direction of travel is the one taken. */
  directionToleranceDeg: 90,
  /** Movement shorter than this gives no usable direction (GPS noise). */
  minMoveM: 10,
  /** A turn stays the next instruction until the position is this far past it. */
  passedM: 5,
};
export type NavLimits = typeof NAV_LIMITS;

export type NavStatus = "waiting" | "tracking" | "weak_signal" | "off_route" | "arrived";

export interface NavSnapshot {
  status: NavStatus;
  fix: Fix | null;
  /** The position on the route, for the camera and the marker; the fix itself when off-route. */
  shown: Point | null;
  headingDeg: number | null;
  /** Distance from the position to the route. */
  offRouteM: number | null;
  remainingM: number;
  remainingMin: number;
  next: { maneuver: Maneuver; distanceM: number } | null;
}

export class NavigationSession {
  private line!: RouteLine;
  private turns!: Maneuver[];
  /** Seconds per metre of the current route, from the Backend's own estimate. */
  private pace = 0;
  private progress = 0;
  /** Time of the fix that last moved `progress`; null until the first fix on this route. */
  private progressAt: number | null = null;
  /** Distance from the route's end to the destination (the route ends at the nearest road node). */
  private endGapM = 0;
  private last: Fix | null = null;
  /** An accurate fix at least minMoveM back; the direction of travel is measured from it, so it is
   *  known at walking pace too, where consecutive fixes are only a few metres apart. */
  private anchor: Fix | null = null;
  private offSince: number | null = null;
  private arrived = false;
  private reroutePending = false;
  private nextRerouteAt = 0;
  private failures = 0;
  snapshot: NavSnapshot;

  /** `mode` decides arrival: on foot the user can cross to a destination beside the route, by bike
   *  the route may still have a U-turn to make, so arrival also needs the route to be nearly done. */
  constructor(
    route: NavRoute,
    readonly destination: Point,
    readonly mode: Mode = "walk",
    private readonly limits: NavLimits = NAV_LIMITS,
  ) {
    this.setRoute(route);
    this.snapshot = this.build("waiting", null, null, null, null);
  }

  get routeLine(): RouteLine {
    return this.line;
  }

  get isArrived(): boolean {
    return this.arrived;
  }

  /** A new route from the current position (after a reroute). */
  replaceRoute(route: NavRoute, now: number): void {
    this.setRoute(route);
    this.offSince = null;
    this.reroutePending = false;
    this.failures = 0;
    this.nextRerouteAt = now + this.limits.rerouteIntervalS * 1000;
    this.progressAt = now; // the new route starts where the user is
    if (this.last) this.update(this.last);
  }

  private setRoute(route: NavRoute): void {
    this.line = routeLine(route.coordinates);
    this.turns = maneuvers(this.line);
    this.pace = this.line.length > 0 ? (route.travelTimeMin * 60) / this.line.length : 0;
    this.progress = 0;
    this.progressAt = null;
    const end = this.line.points[this.line.points.length - 1];
    this.endGapM = end ? distanceM(end, this.destination) : 0;
  }

  update(fix: Fix): NavSnapshot {
    const L = this.limits;
    if (this.arrived) return this.snapshot;
    const previous = this.last;
    this.last = fix;
    const weak = !(fix.accuracyM <= L.weakAccuracyM);
    const threshold = Math.max(L.offRouteM, fix.accuracyM);
    const snap = this.locate(fix, threshold);
    const onRoute = snap.offsetM <= threshold;

    if (!weak) {
      if (!this.anchor || distanceM(this.anchor, fix) >= L.minMoveM) this.anchor = fix;
      if (onRoute) {
        this.offSince = null;
        this.progress = snap.along;
        this.progressAt = fix.time;
      } else if (this.offSince === null) {
        this.offSince = fix.time;
      }
      const remainingM = this.line.length - this.progress;
      const nearDestination = distanceM(fix, this.destination) <= L.arriveM &&
        (this.mode === "walk" || remainingM <= this.endGapM + L.arriveM);
      if (nearDestination || (onRoute && this.line.length - snap.along <= L.arriveM)) {
        this.arrived = true;
        this.progress = this.line.length;
        return (this.snapshot = this.build("arrived", fix, this.destination, null, 0));
      }
    }

    const off = this.offSince !== null && fix.time - this.offSince >= L.offRouteS * 1000;
    const status: NavStatus = weak ? "weak_signal" : off ? "off_route" : "tracking";
    const shown = onRoute ? snap.point : fix;
    return (this.snapshot = this.build(status, fix, shown, this.heading(fix, previous), snap.offsetM));
  }

  /** Where on the route the user is. Two parallel passes of the route (both carriageways after a
   *  putar balik) are equally near, so the direction of travel decides between them:
   *
   *  1. near the last position (progress - backtrackM to progress + aheadM);
   *  2. if not found there, anywhere the user could have reached since the last fix on the route
   *     (after a gap, or on a first fix mid-route);
   *  3. if neither has a pass in the direction of travel, the nearest point near the last position.
   *
   *  Without a direction (first fix, standing still) the nearest point is used near the last position,
   *  and the earliest pass farther on: the route is ridden in order, and a wrong guess is corrected
   *  by the direction of the next fixes. */
  private locate(fix: Fix, threshold: number): Snap {
    const L = this.limits;
    const back = this.progress - L.backtrackM;
    const direction = this.travelDirection(fix);
    const usable = (all: Candidate[]) => all.filter((c) => c.offsetM <= threshold && !c.clamped);
    const nearest = (all: Candidate[]) => all.reduce((a, b) => (b.offsetM < a.offsetM ? b : a));
    const pick = (all: Candidate[], near: boolean): Candidate | null => {
      const found = usable(all);
      if (!found.length) return null;
      if (direction !== null) {
        const along = found.filter((c) => Math.abs(turnAngle(c.bearing, direction)) <= L.directionToleranceDeg);
        return along.length ? nearest(along) : null;
      }
      return near ? nearest(found) : found[0];
    };
    const near = snapCandidates(this.line, fix, back, this.progress + L.aheadM);
    const reach = this.progressAt === null ? Infinity
      : this.progress + L.aheadM + L.maxSpeedMps * Math.max(0, (fix.time - this.progressAt) / 1000);
    return pick(near, true) ?? pick(snapCandidates(this.line, fix, back, reach), false) ?? nearest(near);
  }

  /** Degrees from north the user is moving in, or null when that is not known. */
  private travelDirection(fix: Fix): number | null {
    if (fix.headingDeg !== null && Number.isFinite(fix.headingDeg) && (fix.speedMps ?? 0) >= 1) return fix.headingDeg;
    const from = this.anchor;
    if (from && distanceM(from, fix) >= this.limits.minMoveM) return bearingDeg(from, fix);
    return null;
  }

  /** True when the caller should request a new route now. */
  rerouteDue(now: number): boolean {
    return this.snapshot.status === "off_route" && !this.reroutePending && now >= this.nextRerouteAt;
  }

  /** A request was sent. The interval counts from here, so a request that is later
   *  abandoned still uses up its slot (the gateway allows 20 route requests a minute). */
  rerouteStarted(now: number): void {
    this.reroutePending = true;
    this.nextRerouteAt = now + this.limits.rerouteIntervalS * 1000;
  }

  /** A request failed: wait longer before the next one, at least `waitSec` (Retry-After). */
  rerouteFailed(now: number, waitSec: number | null = null): number {
    const L = this.limits;
    this.reroutePending = false;
    this.failures++;
    const backoff = Math.min(L.rerouteMaxIntervalS, L.rerouteIntervalS * 2 ** (this.failures - 1));
    const wait = Math.max(backoff, waitSec ?? 0);
    this.nextRerouteAt = now + wait * 1000;
    return wait;
  }

  /** A request was abandoned without an answer (the position came back to the route).
   *  The interval set by rerouteStarted stays. */
  rerouteCancelled(): void {
    this.reroutePending = false;
  }

  private heading(fix: Fix, previous: Fix | null): number | null {
    if (fix.headingDeg !== null && Number.isFinite(fix.headingDeg) && (fix.speedMps ?? 0) >= 0.5) return fix.headingDeg;
    if (previous && distanceM(previous, fix) >= 5) return bearingDeg(previous, fix);
    return this.line.length > 0 ? bearingAt(this.line, this.progress) : null;
  }

  private build(status: NavStatus, fix: Fix | null, shown: Point | null, heading: number | null, offRouteM: number | null): NavSnapshot {
    const remainingM = Math.max(0, this.line.length - this.progress);
    const ahead = this.turns.find((m) => m.along > this.progress - this.limits.passedM) ?? null;
    return {
      status,
      fix,
      shown,
      headingDeg: heading,
      offRouteM,
      remainingM,
      remainingMin: (remainingM * this.pace) / 60,
      next: ahead && status !== "arrived" ? { maneuver: ahead, distanceM: Math.max(0, ahead.along - this.progress) } : null,
    };
  }
}
