import { describe, expect, it } from "vitest";

import html from "../index.html?raw";
import { GEOCODE_NOTE, ROUTE_TIMEOUT_MS, SURFACE_TIMEOUT_MS, timeoutMs } from "./config";

describe("notice about the geocoding provider (PNF-06)", () => {
  it("names everything that is sent", () => {
    expect(GEOCODE_NOTE).toMatch(/diketik/);
    expect(GEOCODE_NOTE).toMatch(/klik peta/);
    expect(GEOCODE_NOTE).toMatch(/lokasi Anda/);
    expect(GEOCODE_NOTE).toMatch(/OpenStreetMap/);
  });

  it("sits on the form, not in the list a map click never opens", () => {
    const notice = html.indexOf('id="place-notice"');
    expect(notice).toBeGreaterThan(html.lastIndexOf('id="menu-destination"'));
    expect(html.slice(0, notice).match(/<div class="place-menu"/g)).toHaveLength(2);
    expect(html.slice(html.lastIndexOf('id="menu-destination"'), notice)).toMatch(/<\/div>/);
  });
});

describe("timeoutMs", () => {
  it("keeps the Tabel 3.15 defaults without build variables", () => {
    expect(ROUTE_TIMEOUT_MS).toBe(12_000);
    expect(SURFACE_TIMEOUT_MS).toBe(5_000);
  });

  it("uses a positive whole number of milliseconds", () => {
    expect(timeoutMs("45000", 12_000)).toBe(45_000);
  });

  it.each([undefined, "", "0", "-5", "1.5", "45s", "abc"])("falls back on %j", (value) => {
    expect(timeoutMs(value, 12_000)).toBe(12_000);
  });
});
