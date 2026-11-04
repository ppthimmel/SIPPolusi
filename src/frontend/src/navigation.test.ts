import { describe, expect, it } from "vitest";

import { parseRouteResponse } from "./api";
import routePolicy from "./fixtures/ti-se-08/route_policy.json";
import { maneuvers, pointAt, routeLine } from "./nav-geometry";
import { NAV_LIMITS, NavigationSession, type Fix } from "./navigation";

/** [lon, lat] of a point `east` and `north` metres from a reference in central Jakarta. */
function at(east: number, north: number): [number, number] {
  return [106.82 + east / (111_195 * Math.cos((-6.2 * Math.PI) / 180)), -6.2 + north / 111_195];
}

// An L-shaped route: 400 m north, then 300 m east; 10 minutes as the Backend estimated.
const ROUTE = { coordinates: [at(0, 0), at(0, 400), at(300, 400)], travelTimeMin: 10 };
const DESTINATION = { lat: at(300, 400)[1], lon: at(300, 400)[0] };

function fix(east: number, north: number, time: number, accuracyM = 5, extra: Partial<Fix> = {}): Fix {
  const [lon, lat] = at(east, north);
  return { lat, lon, accuracyM, headingDeg: null, speedMps: null, time, ...extra };
}

const session = () => new NavigationSession(ROUTE, DESTINATION);

describe("progress along the route", () => {
  it("waits for the first position", () => {
    expect(session().snapshot.status).toBe("waiting");
  });

  it("counts down distance and time, and points at the next turn", () => {
    const s = session();
    const a = s.update(fix(3, 100, 0));
    expect(a.status).toBe("tracking");
    expect(a.remainingM).toBeCloseTo(600, -1);
    expect(a.remainingMin).toBeCloseTo((10 * 600) / 700, 0);
    expect(a.next?.maneuver.kind).toBe("right");
    expect(a.next?.distanceM).toBeCloseTo(300, -1);

    const b = s.update(fix(-2, 390, 60_000));
    expect(b.remainingM).toBeLessThan(a.remainingM);
    expect(b.next?.distanceM).toBeCloseTo(10, -1);

    const c = s.update(fix(100, 402, 120_000));
    expect(c.next?.maneuver.kind).toBe("arrive");
    expect(c.next?.distanceM).toBeCloseTo(200, -1);
  });

  it("keeps a turn as the instruction until it has been passed", () => {
    const s = session();
    s.update(fix(0, 380, 0));
    const before = s.update(fix(0, 397, 5_000));
    expect(before.next?.maneuver.kind).toBe("right");
    expect(before.next?.distanceM).toBeCloseTo(3, 0);
    const at = s.update(fix(3, 400, 6_000));
    expect(at.next?.maneuver.kind).toBe("right");
    expect(at.next?.distanceM).toBe(0);
    expect(s.update(fix(20, 400, 9_000)).next?.maneuver.kind).toBe("arrive");
  });

  it("does not jump ahead to a later pass of the route nearby", () => {
    // 300 m north, putar balik, then 250 m back south on the other carriageway, 15 m east.
    const uturn = { coordinates: [at(0, 0), at(0, 300), at(15, 300), at(15, 50)], travelTimeMin: 10 };
    const s = new NavigationSession(uturn, { lat: at(15, 50)[1], lon: at(15, 50)[0] });
    s.update(fix(0, 100, 0));
    const drift = s.update(fix(9, 101, 1_000)); // nearer the return leg than the outbound one
    expect(drift.status).toBe("tracking");
    expect(drift.remainingM).toBeCloseTo(565 - 101, -1);
    expect(drift.next?.distanceM).toBeCloseTo(300 - 101, -1);
    // The next fix on the outbound leg is still on the route: no false off-route.
    expect(s.update(fix(0, 105, 2_000)).offRouteM).toBeLessThan(1);
  });

  // 300 m out, putar balik, then 800 m back on the other carriageway, 15 m away (review of ddab651).
  const outAndBack = { coordinates: [at(0, 0), at(0, 300), at(15, 300), at(15, -500)], travelTimeMin: 15 };
  const outAndBackEnd = { lat: at(15, -500)[1], lon: at(15, -500)[0] };

  it("does not skip the putar balik when the first fix drifts toward the return carriageway", () => {
    const s = new NavigationSession(outAndBack, outAndBackEnd);
    const first = s.update(fix(9, 10, 0));
    expect(first.remainingM).toBeCloseTo(1115 - 10, -1);
    expect(first.next?.maneuver.kind).not.toBe("arrive");
  });

  it("does not skip it after a gap either", () => {
    const s = new NavigationSession(outAndBack, outAndBackEnd);
    s.update(fix(0, 100, 0));
    const later = s.update(fix(9, 150, 60_000));
    expect(later.remainingM).toBeCloseTo(1115 - 150, -1);
    expect(later.next?.maneuver.kind).not.toBe("arrive");
  });

  it("finds a user who starts mid-route", () => {
    const first = session().update(fix(3, 200, 0));
    expect(first.status).toBe("tracking");
    expect(first.remainingM).toBeCloseTo(500, -1);
  });

  it("lets progress catch up after a gap in the fixes", () => {
    const s = session();
    s.update(fix(0, 50, 0));
    const later = s.update(fix(0, 350, 40_000)); // 300 m on, after 40 s without a fix
    expect(later.status).toBe("tracking");
    expect(later.remainingM).toBeCloseTo(350, -1);
  });

  it("shows the position snapped onto the route while on it", () => {
    const snap = session().update(fix(12, 100, 0));
    expect(snap.offRouteM).toBeCloseTo(12, 0);
    expect(snap.shown!.lon).toBeCloseTo(at(0, 100)[0], 6);
  });

  it("uses the device heading when moving, else the direction of travel", () => {
    const s = session();
    expect(s.update(fix(0, 50, 0, 5, { headingDeg: 10, speedMps: 1.4 })).headingDeg).toBe(10);
    // Standing still: the device's heading is unreliable, so the route's direction is used.
    expect(s.update(fix(0, 50, 1_000, 5, { headingDeg: 200, speedMps: 0 })).headingDeg).toBeCloseTo(0, 0);
    expect(s.update(fix(0, 80, 2_000)).headingDeg).toBeCloseTo(0, 0);
  });
});

describe("weak signal", () => {
  it("shows the position but never judges off-route on it", () => {
    const s = session();
    s.update(fix(0, 100, 0));
    const snap = s.update(fix(200, 100, 60_000, 120));
    expect(snap.status).toBe("weak_signal");
    expect(s.update(fix(200, 100, 90_000, 120)).status).toBe("weak_signal");
    expect(s.rerouteDue(90_000)).toBe(false);
  });

  it("does not arrive on an inaccurate fix", () => {
    const s = session();
    expect(s.update(fix(300, 400, 0, 200)).status).toBe("weak_signal");
  });
});

describe("leaving the route", () => {
  it("needs both the distance and the time; one stray fix is not enough", () => {
    const s = session();
    s.update(fix(0, 100, 0));
    expect(s.update(fix(60, 110, 1_000)).status).toBe("tracking");
    expect(s.update(fix(0, 120, 2_000)).status).toBe("tracking");
    expect(s.update(fix(60, 130, 3_000)).status).toBe("tracking");
    expect(s.update(fix(65, 135, 3_000 + NAV_LIMITS.offRouteS * 1000)).status).toBe("off_route");
    expect(s.rerouteDue(13_000)).toBe(true);
  });

  it("widens the threshold to the fix's accuracy", () => {
    const s = session();
    s.update(fix(0, 100, 0));
    expect(s.update(fix(45, 100, 1_000, 48)).status).toBe("tracking");
    expect(s.update(fix(45, 100, 20_000, 48)).status).toBe("tracking");
  });

  it("asks for a new route at most once per interval, and backs off after failures", () => {
    const s = session();
    s.update(fix(0, 100, 0));
    s.update(fix(80, 100, 1_000));
    let now = 12_000;
    s.update(fix(80, 100, now));
    expect(s.rerouteDue(now)).toBe(true);
    s.rerouteStarted(now);
    expect(s.rerouteDue(now)).toBe(false); // one request at a time

    expect(s.rerouteFailed(now)).toBe(30);
    expect(s.rerouteDue(now + 29_000)).toBe(false);
    now += 30_000;
    s.update(fix(80, 100, now));
    expect(s.rerouteDue(now)).toBe(true);
    s.rerouteStarted(now);
    expect(s.rerouteFailed(now)).toBe(60);
    s.rerouteStarted(now);
    expect(s.rerouteFailed(now)).toBe(120);
    s.rerouteStarted(now);
    expect(s.rerouteFailed(now)).toBe(120); // capped
    s.rerouteStarted(now);
    expect(s.rerouteFailed(now, 300)).toBe(300); // Retry-After wins when longer
  });

  it("an abandoned request still uses up the interval", () => {
    // Accuracy flapping around the weak-signal limit must not turn into a request every other fix.
    const s = session();
    s.update(fix(0, 100, 0));
    s.update(fix(80, 100, 1_000));
    s.update(fix(80, 100, 12_000));
    s.rerouteStarted(12_000);
    s.rerouteCancelled();
    s.update(fix(80, 100, 13_000));
    expect(s.snapshot.status).toBe("off_route");
    expect(s.rerouteDue(13_000)).toBe(false);
    expect(s.rerouteDue(12_000 + NAV_LIMITS.rerouteIntervalS * 1000)).toBe(true);
  });

  it("follows a new route from the current position", () => {
    const s = session();
    s.update(fix(0, 100, 0));
    s.update(fix(80, 100, 1_000));
    s.update(fix(80, 100, 12_000));
    s.rerouteStarted(12_000);
    s.replaceRoute({ coordinates: [at(80, 100), at(80, 400), at(300, 400)], travelTimeMin: 8 }, 12_500);
    const snap = s.snapshot;
    expect(snap.status).toBe("tracking");
    expect(snap.remainingM).toBeCloseTo(520, -1);
    // Even if it is left again at once, the next request waits a full interval.
    s.update(fix(200, 100, 13_000));
    s.update(fix(200, 100, 30_000));
    expect(s.snapshot.status).toBe("off_route");
    expect(s.rerouteDue(30_000)).toBe(false);
    expect(s.rerouteDue(12_500 + NAV_LIMITS.rerouteIntervalS * 1000)).toBe(true);
  });

  it("returning to the route clears it", () => {
    const s = session();
    s.update(fix(0, 100, 0));
    s.update(fix(80, 100, 1_000));
    expect(s.update(fix(80, 100, 12_000)).status).toBe("off_route");
    expect(s.update(fix(5, 120, 13_000)).status).toBe("tracking");
    expect(s.rerouteDue(13_000)).toBe(false);
  });
});

describe("arrival", () => {
  // 300 m north, putar balik, then 250 m back on the other carriageway, 15 m east: the destination
  // is beside the outbound lane, with 515 m of route still to go there.
  const uturn = { coordinates: [at(0, 0), at(0, 300), at(15, 300), at(15, 50)], travelTimeMin: 10 };
  const end = { lat: at(15, 50)[1], lon: at(15, 50)[0] };

  it("by bike, passing the destination on the outbound lane is not arrival", () => {
    const s = new NavigationSession(uturn, end, "bike");
    s.update(fix(0, 0, 0));
    const passing = s.update(fix(0, 50, 30_000));
    expect(passing.status).toBe("tracking");
    expect(passing.remainingM).toBeCloseTo(515, -1);
    // Riding on through the putar balik and back down the other carriageway.
    const ride: [number, number][] = [[0, 100], [0, 150], [0, 200], [0, 250], [0, 295], [8, 300], [15, 280],
                                      [15, 230], [15, 180], [15, 130], [15, 90]];
    ride.forEach(([e, n], i) => expect(s.update(fix(e, n, 40_000 + i * 10_000)).status).toBe("tracking"));
    expect(s.update(fix(15, 55, 160_000)).status).toBe("arrived");
  });

  it("on foot, it is: the user can cross the street", () => {
    const s = new NavigationSession(uturn, end, "walk");
    s.update(fix(0, 0, 0));
    expect(s.update(fix(0, 50, 30_000)).status).toBe("arrived");
  });

  it("by bike, a destination away from the road is reached off the route near its end", () => {
    // The route ends at the nearest road node, 63 m from the destination.
    const s = new NavigationSession({ coordinates: [at(0, 0), at(0, 400)], travelTimeMin: 6 },
      { lat: at(60, 380)[1], lon: at(60, 380)[0] }, "bike");
    s.update(fix(0, 360, 0));
    expect(s.update(fix(50, 380, 20_000)).status).toBe("arrived");
  });

  it("ends at the destination and stops updating", () => {
    const s = session();
    s.update(fix(0, 100, 0));
    const done = s.update(fix(290, 401, 60_000));
    expect(done.status).toBe("arrived");
    expect(done.remainingM).toBe(0);
    expect(done.next).toBeNull();
    expect(s.isArrived).toBe(true);
    expect(s.update(fix(0, 0, 70_000)).status).toBe("arrived");
  });
});

describe("a recorded route from the Backend (TI-SE-08 fixture)", () => {
  const { recommended, request } = (() => {
    const response = parseRouteResponse(routePolicy);
    const coords = response.recommended.coordinates;
    const [lon, lat] = coords[coords.length - 1];
    return { recommended: response.recommended, request: { destination: { lat, lon } } };
  })();

  it("gives a handful of turns, not one per vertex", () => {
    const m = maneuvers(routeLine(recommended.coordinates));
    expect(m[m.length - 1].kind).toBe("arrive");
    expect(m.length).toBeLessThanOrEqual(recommended.coordinates.length - 1);
  });

  it("walked from end to end, counts down and arrives without leaving the route", () => {
    const s = new NavigationSession(recommended, request.destination);
    const line = routeLine(recommended.coordinates);
    let last = Infinity;
    const statuses = new Set<string>();
    for (let along = 0, t = 0; along <= line.length + 10; along += 10, t += 7_000) {
      const p = pointAt(line, along);
      const snap = s.update({ ...p, accuracyM: 8, headingDeg: null, speedMps: null, time: t });
      statuses.add(snap.status);
      expect(snap.remainingM).toBeLessThanOrEqual(last + 0.01);
      last = snap.remainingM;
    }
    expect(statuses).toEqual(new Set(["tracking", "arrived"]));
    expect(s.snapshot.remainingMin).toBe(0);
  });
});
