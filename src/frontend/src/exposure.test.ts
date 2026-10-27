import { describe, expect, it } from "vitest";
import { exposureClass, layerRatio } from "./exposure";

describe("exposureClass (UT-FE-07a)", () => {
  it.each([
    [0.3, "Sangat rendah"],
    [0.5, "Rendah"],
    [0.8, "Rendah"],
    [1.0, "Sedang"],
    [1.5, "Sedang"],
    [2.0, "Tinggi"],
    [2.5, "Tinggi"],
    [3.0, "Sangat tinggi"],
    [3.5, "Sangat tinggi"],
  ])("%s → %s", (ratio, label) => {
    expect(exposureClass(ratio)).toBe(label);
  });
});

describe("layerRatio (UT-FE-07b)", () => {
  const f = { exposureIndex: 1.7, pm25: 30, no2: 50 };
  it("colours PM2.5 and NO2 by ratio to their WHO guideline", () => {
    expect(layerRatio("pm25", f)).toBe(2);
    expect(layerRatio("no2", f)).toBe(2);
    expect(layerRatio("combined", f)).toBe(1.7);
  });
  it("returns null when the pollutant is missing", () => {
    expect(layerRatio("no2", { exposureIndex: 1, pm25: 10, no2: null })).toBeNull();
    expect(layerRatio("combined", { exposureIndex: null, pm25: 10, no2: 10 })).toBeNull();
  });
});
