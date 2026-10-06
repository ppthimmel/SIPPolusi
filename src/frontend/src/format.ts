import { CONFIDENCE_HIGH, CONFIDENCE_LOW, STALE_AFTER_HOURS } from "./config";
import type { ExposureUnit, Point } from "./types";

const nfCache = new Map<number, Intl.NumberFormat>();

/** Indonesian number format: decimal comma, fixed digits (34,8). */
export function fmtNumber(value: number, digits = 1): string {
  let nf = nfCache.get(digits);
  if (!nf) {
    nf = new Intl.NumberFormat("id-ID", {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    });
    nfCache.set(digits, nf);
  }
  return nf.format(value);
}

export function fmtMinutes(minutes: number): string {
  return `${fmtNumber(Math.round(minutes), 0)} menit`;
}

export function fmtDistance(meters: number): string {
  return meters < 1000 ? `${fmtNumber(Math.round(meters), 0)} m` : `${fmtNumber(meters / 1000, 1)} km`;
}

export function fmtExposure(value: number): string {
  return fmtNumber(value, 1);
}

export function exposureUnitLabel(unit: ExposureUnit): string {
  return unit === "index_min" ? "indeks·menit" : "µg/m³·menit";
}

export function fmtPercent(value: number): string {
  return `${fmtNumber(Math.abs(value), 1)}%`;
}

export function fmtCoord(p: Point): string {
  return `${p.lat.toFixed(5)}, ${p.lon.toFixed(5)}`;
}

/** "12.00 WIB" for an ISO 8601 timestamp, or null when it cannot be parsed. */
export function fmtWib(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  const time = new Intl.DateTimeFormat("id-ID", {
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
    timeZone: "Asia/Jakarta",
  }).format(d);
  return `${time.replace(":", ".")} WIB`;
}

export type ConfidenceCategory = "rendah" | "sedang" | "tinggi";

export function confidenceCategory(score: number | null | undefined): ConfidenceCategory | null {
  if (score == null || !Number.isFinite(score)) return null;
  if (score < CONFIDENCE_LOW) return "rendah";
  if (score < CONFIDENCE_HIGH) return "sedang";
  return "tinggi";
}

/** True when the time window is older than STALE_AFTER_HOURS. Used only as a
 *  fallback; the Backend's data_stale flag is authoritative when present. */
export function isOlderThanStaleLimit(iso: string | null, now = Date.now()): boolean {
  if (!iso) return false;
  const t = new Date(iso).getTime();
  return Number.isFinite(t) && now - t > STALE_AFTER_HOURS * 3_600_000;
}
