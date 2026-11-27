import { describe, expect, it } from "vitest";
import { placeInStudyArea } from "./place-combobox";
import { confidenceText } from "./results-view";

describe("placeInStudyArea", () => {
  it("accepts places in Jakarta and refuses those outside it", () => {
    expect(placeInStudyArea({ lat: -6.1754, lon: 106.8272 })).toBe(true); // Monas
    expect(placeInStudyArea({ lat: -6.2383, lon: 106.9756 })).toBe(true); // East edge, inside
    expect(placeInStudyArea({ lat: -6.2349, lon: 107.0001 })).toBe(false); // Bekasi
    expect(placeInStudyArea({ lat: -6.4025, lon: 106.7942 })).toBe(false); // Depok
  });
});

describe("confidenceText", () => {
  it("gives the score with its category", () => {
    expect(confidenceText(0.31)).toBe("Keyakinan 0,31 (rendah)");
    expect(confidenceText(0.55)).toBe("Keyakinan 0,55 (sedang)");
    expect(confidenceText(0.8)).toBe("Keyakinan 0,80 (tinggi)");
    expect(confidenceText(null)).toBeNull();
  });
});
