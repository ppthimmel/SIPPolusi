import { describe, expect, it } from "vitest";
import { buildRouteRequest, isTripValid, parseCoordinateText, preferenceFromSlider, validatePoint, validateTrip } from "./validation";

const a = { lat: -6.2088, lon: 106.8456 };
const b = { lat: -6.225, lon: 106.8 };

describe("parseCoordinateText", () => {
  it.each([
    ["-6.2088, 106.8456"],
    ["-6.2088,106.8456"],
    ["-6.2088 106.8456"],
    ["-6,2088; 106,8456"],
  ])("%s", (text) => {
    expect(parseCoordinateText(text)).toEqual(a);
  });
  it.each(["", "  ", "abc", "1", "1, 2, 3", "-6.2 , x", ";", "1;"])("rejects %j", (text) => {
    expect(parseCoordinateText(text)).toBeNull();
  });
});

describe("validatePoint / validateTrip (UT-FE-01)", () => {
  it("accepts points inside the study area", () => {
    expect(validatePoint(a, "asal")).toBeNull();
  });
  it("asks for a missing point", () => {
    expect(validatePoint(null, "tujuan")).toMatch(/tujuan/);
  });
  it("rejects invalid ranges and points outside Jakarta", () => {
    expect(validatePoint({ lat: 95, lon: 106.8 }, "asal")).toMatch(/rentang/);
    expect(validatePoint({ lat: -7.8, lon: 110.36 }, "tujuan")).toMatch(/wilayah studi/);
  });
  it("is valid only with both points and a known mode", () => {
    expect(isTripValid(validateTrip(a, b, "walk"))).toBe(true);
    expect(isTripValid(validateTrip(a, null, "walk"))).toBe(false);
    expect(isTripValid(validateTrip(null, b, "walk"))).toBe(false);
    expect(isTripValid(validateTrip(a, b, "car"))).toBe(false);
  });
  it("rejects identical origin and destination", () => {
    expect(validateTrip(a, { lat: a.lat + 0.00005, lon: a.lon }, "bike").destination).toMatch(/terlalu dekat/);
  });
});

describe("preferenceFromSlider (UT-FE-02)", () => {
  it("keeps alpha + beta = 1 for every slider step", () => {
    for (let i = 0; i <= 20; i++) {
      const { alpha, beta } = preferenceFromSlider(i / 20);
      expect(Math.abs(alpha + beta - 1)).toBeLessThan(1e-9);
      expect(alpha).toBeGreaterThanOrEqual(0);
      expect(beta).toBeLessThanOrEqual(1);
    }
  });
  it("maps the slider ends and centre", () => {
    expect(preferenceFromSlider(0)).toEqual({ alpha: 1, beta: 0 });
    expect(preferenceFromSlider(0.5)).toEqual({ alpha: 0.5, beta: 0.5 });
    expect(preferenceFromSlider(1)).toEqual({ alpha: 0, beta: 1 });
  });
  it("clamps out-of-range input", () => {
    expect(preferenceFromSlider(-0.1)).toEqual({ alpha: 1, beta: 0 });
    expect(preferenceFromSlider(1.2)).toEqual({ alpha: 0, beta: 1 });
    expect(preferenceFromSlider(Number.NaN)).toEqual({ alpha: 0.5, beta: 0.5 });
  });
});

describe("buildRouteRequest", () => {
  it("matches the RouteRequest schema of IRouteQuery", () => {
    expect(buildRouteRequest(a, b, "walk", 0.5)).toEqual({
      origin: { lat: -6.2088, lon: 106.8456 },
      destination: { lat: -6.225, lon: 106.8 },
      mode: "walk",
      pollutant: "pm25",
      preference: { alpha: 0.5, beta: 0.5 },
    });
  });
});
