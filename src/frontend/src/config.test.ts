import { describe, expect, it } from "vitest";

import { ROUTE_TIMEOUT_MS, SURFACE_TIMEOUT_MS, timeoutMs } from "./config";

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
