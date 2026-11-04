import { describe, expect, it, vi } from "vitest";

import { fmtAhead, fmtArrival, instruction } from "./nav-view";
import { DevicePosition, describePositionProblem, isFatal, positionProblem, problemFromCode, toFix } from "./position";

function fakeGeolocation() {
  const watch = vi.fn().mockReturnValue(7);
  const clear = vi.fn();
  return { geo: { watchPosition: watch, clearWatch: clear, getCurrentPosition: vi.fn() } as unknown as Geolocation, watch, clear };
}

describe("position source", () => {
  it("tells why location cannot be used before asking", () => {
    expect(positionProblem(undefined, true)).toBe("unsupported");
    expect(positionProblem(fakeGeolocation().geo, false)).toBe("insecure");
    expect(positionProblem(fakeGeolocation().geo, true)).toBeNull();
  });

  it("maps the Geolocation error codes", () => {
    expect(problemFromCode(1)).toBe("denied");
    expect(problemFromCode(2)).toBe("unavailable");
    expect(problemFromCode(3)).toBe("timeout");
    expect(isFatal("denied")).toBe(true);
    expect(isFatal("timeout")).toBe(false);
    for (const p of ["unsupported", "insecure", "denied", "unavailable", "timeout"] as const) {
      expect(describePositionProblem(p)).toMatch(/\.$/);
    }
  });

  it("watches with high accuracy and stops the watch", () => {
    const { geo, watch, clear } = fakeGeolocation();
    const source = new DevicePosition(geo, true);
    const onFix = vi.fn();
    const onProblem = vi.fn();
    source.start(onFix, onProblem);
    expect(watch.mock.calls[0][2]).toMatchObject({ enableHighAccuracy: true });

    const success = watch.mock.calls[0][0];
    success({ coords: { latitude: -6.2, longitude: 106.8, accuracy: 9, heading: NaN, speed: null }, timestamp: 5 });
    expect(onFix).toHaveBeenCalledWith({ lat: -6.2, lon: 106.8, accuracyM: 9, headingDeg: null, speedMps: null, time: 5 });
    watch.mock.calls[0][1]({ code: 1 });
    expect(onProblem).toHaveBeenCalledWith("denied");

    source.stop();
    expect(clear).toHaveBeenCalledWith(7);
  });

  it("reports a missing GPS without watching", () => {
    const onProblem = vi.fn();
    new DevicePosition(undefined, true).start(vi.fn(), onProblem);
    expect(onProblem).toHaveBeenCalledWith("unsupported");
  });

  it("treats an unknown accuracy as unusable", () => {
    const f = toFix({ coords: { latitude: 0, longitude: 0, accuracy: NaN, heading: 90, speed: 2 }, timestamp: 1 } as unknown as GeolocationPosition);
    expect(f.accuracyM).toBe(Infinity);
    expect(f.headingDeg).toBe(90);
  });
});

describe("instruction text", () => {
  const turn = (kind: "left" | "right" | "uturn" | "arrive", distanceM: number) => ({ maneuver: { kind, along: 0, angle: 0 }, distanceM });

  it("names the turn and when it comes", () => {
    expect(instruction(turn("left", 123))).toMatchObject({ action: "Belok kiri", when: "dalam 120 m" });
    expect(instruction(turn("right", 8))).toMatchObject({ action: "Belok kanan", when: "sekarang" });
    expect(instruction(turn("uturn", 1460))).toMatchObject({ action: "Putar balik", when: "dalam 1,5 km" });
    expect(instruction(turn("arrive", 400)).action).toBe("Ikuti jalan ini sampai tujuan");
    expect(instruction(turn("arrive", 30))).toMatchObject({ action: "Tujuan di depan", when: "30 m lagi" });
  });

  it("rounds distances the way they are spoken", () => {
    expect(fmtAhead(3)).toBe("5 m");
    expect(fmtAhead(37)).toBe("35 m");
    expect(fmtAhead(244)).toBe("240 m");
    expect(fmtAhead(2_350)).toBe("2,4 km");
  });

  it("gives the arrival time on the local clock", () => {
    expect(fmtArrival(25, new Date(2026, 10, 3, 13, 50))).toBe("14.15");
  });
});
