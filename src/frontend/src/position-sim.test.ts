import { afterEach, describe, expect, it, vi } from "vitest";
import { distanceM, routeLine, snapToLine } from "./nav-geometry";
import { offRouteStep } from "./nav-view";
import type { Fix } from "./navigation";
import { DETOUR_M, SimulatedPosition } from "./position-sim";

// A straight route of about 1.1 km going east.
const line = routeLine([
  [106.8, -6.2],
  [106.81, -6.2],
]);

describe("SimulatedPosition manual detour (test panel)", () => {
  afterEach(() => vi.useRealTimers());

  it("walks off the route up to DETOUR_M, waits there, and walks back", () => {
    vi.useFakeTimers();
    const fixes: Fix[] = [];
    const sim = new SimulatedPosition(() => line, "normal", 20, 1_000, 300);
    sim.start((f) => fixes.push(f), () => {});
    const off = () => snapToLine(line, fixes[fixes.length - 1]).offsetM;

    expect(off()).toBeLessThan(1); // starts on the route, 300 m along
    sim.setDetour(true);
    vi.advanceTimersByTime(10_000);
    expect(sim.isDetoured).toBe(true);
    expect(off()).toBeCloseTo(DETOUR_M, 0); // stopped at the detour distance
    const along = snapToLine(line, fixes[fixes.length - 1]).along;
    vi.advanceTimersByTime(3_000);
    expect(snapToLine(line, fixes[fixes.length - 1]).along).toBeCloseTo(along, 0); // waits, not moving on

    sim.setDetour(false);
    vi.advanceTimersByTime(6_000);
    expect(off()).toBeLessThan(1); // back on the route
    expect(sim.isDetoured).toBe(false);
    sim.stop();
  });

  it("starts the new route from the detour's end once it arrives", () => {
    vi.useFakeTimers();
    const fixes: Fix[] = [];
    const sim = new SimulatedPosition(() => line, "normal", 20, 1_000, 300);
    sim.start((f) => fixes.push(f), () => {});
    sim.setDetour(true);
    vi.advanceTimersByTime(6_000);
    sim.routeChanged();
    expect(sim.isDetoured).toBe(false);
    vi.advanceTimersByTime(1_000);
    // Following the (here unchanged) route again from its start.
    expect(distanceM(fixes[fixes.length - 1], { lat: -6.2, lon: 106.8 })).toBeLessThan(25);
    sim.stop();
  });
});

describe("offRouteStep", () => {
  it("says to return to the route, with the distance in 10 m steps", () => {
    expect(offRouteStep(84)).toEqual({ action: "Kembali ke rute", when: "±80 m dari rute", icon: "uturn" });
    expect(offRouteStep(3).when).toBe("±10 m dari rute");
    expect(offRouteStep(null).when).toBe("");
  });
});
