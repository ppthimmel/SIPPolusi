import { MIN_TRIP_DISTANCE_M, STUDY_AREA } from "./config";
import type { Mode, Point, Preference, RouteRequest } from "./types";

/** Parses "lat, lon", "lat lon" or "lat; lon" (the last allows decimal commas).
 *  Returns null when the text is not exactly two finite numbers. */
export function parseCoordinateText(text: string): Point | null {
  const trimmed = text.trim();
  if (!trimmed) return null;
  const parts = trimmed.includes(";")
    ? trimmed.split(";").map((s) => s.trim().replace(",", "."))
    : trimmed.split(/\s*,\s*|\s+/);
  if (parts.length !== 2) return null;
  const [lat, lon] = parts.map((s) => (s === "" ? NaN : Number(s)));
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) return null;
  return { lat, lon };
}

/** Error text for a point the user entered, or null when it is usable. */
export function validatePoint(p: Point | null, label: "asal" | "tujuan"): string | null {
  if (!p) return `Pilih titik ${label} di peta atau ketik koordinat "lat, lon".`;
  if (Math.abs(p.lat) > 90 || Math.abs(p.lon) > 180) {
    return `Koordinat titik ${label} di luar rentang yang valid.`;
  }
  const a = STUDY_AREA;
  if (p.lat < a.minLat || p.lat > a.maxLat || p.lon < a.minLon || p.lon > a.maxLon) {
    return `Titik ${label} berada di luar wilayah studi (Jakarta).`;
  }
  return null;
}

export function distanceMeters(a: Point, b: Point): number {
  const R = 6_371_000;
  const toRad = (d: number) => (d * Math.PI) / 180;
  const dLat = toRad(b.lat - a.lat);
  const dLon = toRad(b.lon - a.lon);
  const h =
    Math.sin(dLat / 2) ** 2 + Math.cos(toRad(a.lat)) * Math.cos(toRad(b.lat)) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.min(1, Math.sqrt(h)));
}

export interface TripErrors {
  origin: string | null;
  destination: string | null;
  mode: string | null;
}

export function validateTrip(origin: Point | null, destination: Point | null, mode: string): TripErrors {
  const errors: TripErrors = {
    origin: validatePoint(origin, "asal"),
    destination: validatePoint(destination, "tujuan"),
    mode: mode === "walk" || mode === "bike" ? null : "Pilih moda perjalanan.",
  };
  if (!errors.origin && !errors.destination && origin && destination) {
    if (distanceMeters(origin, destination) < MIN_TRIP_DISTANCE_M) {
      errors.destination = "Titik tujuan terlalu dekat dengan titik asal.";
    }
  }
  return errors;
}

export function isTripValid(errors: TripErrors): boolean {
  return !errors.origin && !errors.destination && !errors.mode;
}

/** Slider position runs from "Lebih cepat" (0) to "Lebih bersih" (1). Position
 *  maps to beta, alpha = 1 - beta, so alpha + beta = 1 always holds. */
export function preferenceFromSlider(position: number): Preference {
  const p = Math.min(1, Math.max(0, Number.isFinite(position) ? position : 0.5));
  const beta = Math.round(p * 100) / 100;
  const alpha = Math.round((1 - beta) * 100) / 100;
  return { alpha, beta };
}

export function buildRouteRequest(origin: Point, destination: Point, mode: Mode, position: number): RouteRequest {
  return {
    origin: { lat: origin.lat, lon: origin.lon },
    destination: { lat: destination.lat, lon: destination.lon },
    mode,
    pollutant: "pm25",
    preference: preferenceFromSlider(position),
  };
}
