import { describe, expect, it } from "vitest";

import { ROUTE_TIMEOUT_MS, SURFACE_TIMEOUT_MS, envSwitch, timeoutMs } from "./config";

describe("envSwitch (VITE_NAV_TEST_PANEL, VITE_GPS_SPOOF)", () => {
  it("reads true/false and on/off in any case", () => {
    for (const v of ["true", "on", "1", "yes", "TRUE", " On "]) expect(envSwitch(v, false)).toBe(true);
    for (const v of ["false", "off", "0", "no", "OFF"]) expect(envSwitch(v, true)).toBe(false);
  });

  it("falls back to the default when empty or unknown", () => {
    expect(envSwitch(undefined, true)).toBe(true);
    expect(envSwitch("", false)).toBe(false);
    expect(envSwitch("maybe", true)).toBe(true);
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
