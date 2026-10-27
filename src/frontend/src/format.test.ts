import { describe, expect, it } from "vitest";
import { confidenceCategory, fmtDistance, fmtMinutes, fmtNumber, fmtPercent, fmtWib, isOlderThanStaleLimit } from "./format";

describe("confidenceCategory (UT-FE-09a)", () => {
  it.each([
    [0.39, "rendah"],
    [0.4, "sedang"],
    [0.69, "sedang"],
    [0.7, "tinggi"],
    [0, "rendah"],
    [1, "tinggi"],
  ])("%s → %s", (score, expected) => {
    expect(confidenceCategory(score)).toBe(expected);
  });

  it("has no category without a score", () => {
    expect(confidenceCategory(null)).toBeNull();
    expect(confidenceCategory(undefined)).toBeNull();
    expect(confidenceCategory(Number.NaN)).toBeNull();
  });
});

describe("number formatting", () => {
  it("uses a decimal comma", () => {
    expect(fmtNumber(34.8, 1)).toBe("34,8");
    expect(fmtNumber(0.5, 2)).toBe("0,50");
  });
  it("writes units out", () => {
    expect(fmtMinutes(28.4)).toBe("28 menit");
    expect(fmtDistance(2215)).toBe("2,2 km");
    expect(fmtDistance(850)).toBe("850 m");
  });
  it("shows percent without sign (the caller adds it)", () => {
    expect(fmtPercent(-31.9)).toBe("31,9%");
    expect(fmtPercent(25)).toBe("25,0%");
  });
});

describe("fmtWib", () => {
  it("converts UTC to Western Indonesia Time", () => {
    expect(fmtWib("2026-09-21T05:00:00Z")).toBe("12.00 WIB");
    expect(fmtWib("2026-09-21T17:30:00Z")).toBe("00.30 WIB");
  });
  it("returns null for missing or invalid input", () => {
    expect(fmtWib(null)).toBeNull();
    expect(fmtWib("bukan tanggal")).toBeNull();
  });
});

describe("isOlderThanStaleLimit", () => {
  const now = Date.parse("2026-09-21T12:00:00Z");
  it("is stale beyond 3 hours", () => {
    expect(isOlderThanStaleLimit("2026-09-21T08:59:00Z", now)).toBe(true);
    expect(isOlderThanStaleLimit("2026-09-21T09:30:00Z", now)).toBe(false);
    expect(isOlderThanStaleLimit(null, now)).toBe(false);
  });
});
