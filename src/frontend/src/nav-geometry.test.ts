import { describe, expect, it } from "vitest";

import {
  bearingAt,
  bearingDeg,
  classifyTurn,
  distanceM,
  maneuvers,
  pointAt,
  routeLine,
  snapToLine,
  turnAngle,
} from "./nav-geometry";

/** [lon, lat] of a point `east` and `north` metres from a reference in central Jakarta. */
function at(east: number, north: number): [number, number] {
  const lat0 = -6.2;
  const lon0 = 106.82;
  return [lon0 + east / (111_195 * Math.cos((lat0 * Math.PI) / 180)), lat0 + north / 111_195];
}
const pt = ([lon, lat]: [number, number]) => ({ lat, lon });

describe("distance and bearing", () => {
  it("measures metres and compass directions", () => {
    expect(distanceM(pt(at(0, 0)), pt(at(300, 400)))).toBeCloseTo(500, -1);
    expect(bearingDeg(pt(at(0, 0)), pt(at(0, 100)))).toBeCloseTo(0, 0);
    expect(bearingDeg(pt(at(0, 0)), pt(at(100, 0)))).toBeCloseTo(90, 0);
    expect(bearingDeg(pt(at(0, 0)), pt(at(0, -100)))).toBeCloseTo(180, 0);
  });

  it("signs turns: right positive, left negative, across north", () => {
    expect(turnAngle(0, 90)).toBe(90);
    expect(turnAngle(90, 0)).toBe(-90);
    expect(turnAngle(350, 10)).toBe(20);
    expect(turnAngle(10, 350)).toBe(-20);
    expect(turnAngle(0, 180)).toBe(180);
  });
});

describe("routeLine", () => {
  it("accumulates distance and drops repeated vertices", () => {
    const line = routeLine([at(0, 0), at(0, 0), at(0, 100), at(100, 100)]);
    expect(line.points).toHaveLength(3);
    expect(line.cum[1]).toBeCloseTo(100, 0);
    expect(line.length).toBeCloseTo(200, 0);
  });

  it("finds the point and direction at a distance along it", () => {
    const line = routeLine([at(0, 0), at(0, 100), at(100, 100)]);
    expect(distanceM(pointAt(line, 150), pt(at(50, 100)))).toBeLessThan(0.5);
    expect(pointAt(line, -5)).toEqual(line.points[0]);
    expect(pointAt(line, 1e6)).toEqual(line.points[2]);
    expect(bearingAt(line, 50)).toBeCloseTo(0, 0);
    expect(bearingAt(line, 150)).toBeCloseTo(90, 0);
  });
});

describe("snapToLine", () => {
  const line = routeLine([at(0, 0), at(0, 200), at(200, 200)]);

  it("projects a position onto the nearest segment", () => {
    const s = snapToLine(line, pt(at(15, 80)));
    expect(s.along).toBeCloseTo(80, 0);
    expect(s.offsetM).toBeCloseTo(15, 0);
  });

  it("ignores the part of the route already behind", () => {
    // Out and back along the same street: the return leg must not snap to the outbound one.
    const back = routeLine([at(0, 0), at(0, 200), at(0, 0)]);
    expect(snapToLine(back, pt(at(0, 50))).along).toBeCloseTo(50, 0);
    expect(snapToLine(back, pt(at(0, 50)), 250).along).toBeCloseTo(350, 0);
  });

  it("ignores the part of the route too far ahead", () => {
    // After a putar balik the route returns on the other carriageway, 15 m away: a fix drifting
    // toward it must stay on the outbound leg.
    const uturn = routeLine([at(0, 0), at(0, 300), at(15, 300), at(15, 0)]);
    expect(snapToLine(uturn, pt(at(9, 100))).along).toBeGreaterThan(400);
    const s = snapToLine(uturn, pt(at(9, 100)), 50, 170);
    expect(s.along).toBeCloseTo(100, 0);
    expect(s.offsetM).toBeCloseTo(9, 0);
    // Beyond the window the position is held at its far end, not dropped.
    expect(snapToLine(uturn, pt(at(0, 250)), 0, 170).along).toBeCloseTo(170, 0);
  });
});

describe("maneuvers", () => {
  it("announces a right and a left turn, then the arrival", () => {
    const line = routeLine([at(0, 0), at(0, 200), at(200, 200), at(200, 400)]);
    const m = maneuvers(line);
    expect(m.map((x) => x.kind)).toEqual(["right", "left", "arrive"]);
    expect(m[0].along).toBeCloseTo(200, 0);
    expect(m[1].along).toBeCloseTo(400, 0);
    expect(m[2].along).toBeCloseTo(line.length, 3);
  });

  it("reads a curve drawn with many short segments as one turn", () => {
    const coords: [number, number][] = [at(0, 0), at(0, 100)];
    for (let i = 1; i <= 6; i++) {
      const a = (i / 6) * (Math.PI / 2);
      coords.push(at(10 - 10 * Math.cos(a), 100 + 10 * Math.sin(a)));
    }
    coords.push(at(200, 110));
    const kinds = maneuvers(routeLine(coords)).map((x) => x.kind);
    expect(kinds).toEqual(["right", "arrive"]);
  });

  it("does not announce a small jog in the geometry", () => {
    const line = routeLine([at(0, 0), at(0, 100), at(3, 103), at(3, 300)]);
    expect(maneuvers(line).map((x) => x.kind)).toEqual(["arrive"]);
  });

  it("classifies by angle", () => {
    expect(classifyTurn(10)).toBeNull();
    expect(classifyTurn(-45)).toBe("slight_left");
    expect(classifyTurn(90)).toBe("right");
    expect(classifyTurn(-150)).toBe("sharp_left");
    expect(classifyTurn(178)).toBe("uturn");
  });
});
