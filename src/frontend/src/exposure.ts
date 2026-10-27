import { CONFIDENCE_LOW, WHO_GUIDELINE } from "./config";
import type { HeatLayer, SurfaceFeature } from "./types";

/** Colour stops of the one-directional cream → dark-red ramp (subbab 3.1). The
 *  stops sit on the class boundaries 0,5 / 1,0 / 2,0 / 3,0. */
export const EXPOSURE_STOPS: ReadonlyArray<readonly [number, string]> = [
  [0, "#fdf0d5"],
  [0.5, "#f8d196"],
  [1, "#ee9d55"],
  [2, "#cc5a2c"],
  [3, "#6c150c"],
];

export const NO_DATA_COLOR = "#bdb8aa";

export const EXPOSURE_CLASSES: ReadonlyArray<{ max: number; label: string }> = [
  { max: 0.5, label: "Sangat rendah" },
  { max: 1, label: "Rendah" },
  { max: 2, label: "Sedang" },
  { max: 3, label: "Tinggi" },
  { max: Infinity, label: "Sangat tinggi" },
];

export const HEAT_LAYER_LABELS: Record<HeatLayer, string> = {
  combined: "Gabungan",
  pm25: "PM2.5",
  no2: "NO2",
};

export function exposureClass(ratio: number): string {
  return (EXPOSURE_CLASSES.find((c) => ratio < c.max) ?? EXPOSURE_CLASSES[EXPOSURE_CLASSES.length - 1]).label;
}

/** Colour ratio for a layer: always relative to the WHO guideline, so the three
 *  layers share one colour scale (render_heatmap, Tabel 3.4). */
/** Hatching marks low-confidence areas apart from the rest. When every feature
 *  drawn is low (the IDW fallback is capped below 0,4 by design, Tabel 3.8), the
 *  stripes would cover the whole map without telling anything apart, so the
 *  legend says it once instead (TI-SE-10, D-06). A feature without a score
 *  counts as low, as on the map. */
export function allLowConfidence(features: ReadonlyArray<Pick<SurfaceFeature, "confidence">>): boolean {
  return features.length > 0 && features.every((f) => (f.confidence ?? 0) < CONFIDENCE_LOW);
}

export function layerRatio(layer: HeatLayer, f: Pick<SurfaceFeature, "exposureIndex" | "pm25" | "no2">): number | null {
  switch (layer) {
    case "combined":
      return f.exposureIndex;
    case "pm25":
      return f.pm25 == null ? null : f.pm25 / WHO_GUIDELINE.pm25;
    case "no2":
      return f.no2 == null ? null : f.no2 / WHO_GUIDELINE.no2;
  }
}
