import { CONFIDENCE_HIGH, CONFIDENCE_LOW, STALE_AFTER_HOURS } from "./config";
import { locale, tr } from "./i18n";
import type { ExposureUnit, Point, Pollutant } from "./types";

const nfCache = new Map<string, Intl.NumberFormat>();

/** Number in the interface language, fixed digits: 34,8 (Indonesian) or 34.8 (English). */
export function fmtNumber(value: number, digits = 1): string {
  const key = `${locale()}:${digits}`;
  let nf = nfCache.get(key);
  if (!nf) {
    nf = new Intl.NumberFormat(locale(), {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    });
    nfCache.set(key, nf);
  }
  return nf.format(value);
}

export function fmtMinutes(minutes: number): string {
  return `${fmtNumber(Math.round(minutes), 0)} ${tr("menit", "min")}`;
}

export function fmtDistance(meters: number): string {
  return meters < 1000 ? `${fmtNumber(Math.round(meters), 0)} m` : `${fmtNumber(meters / 1000, 1)} km`;
}

export function fmtExposure(value: number): string {
  return fmtNumber(value, 1);
}

export function exposureUnitLabel(unit: ExposureUnit): string {
  return unit === "index_min" ? tr("indeks·menit", "index·min") : tr("µg/m³·menit", "µg/m³·min");
}

export function fmtPercent(value: number): string {
  return `${fmtNumber(Math.abs(value), 1)}%`;
}

const POLLUTANT_LABEL: Record<Pollutant, string> = { pm25: "PM2.5", no2: "NO2" };

/** "Sumber estimasi" in the result details, from metadata.estimation (TI-SE-10, D-07).
 *  Claims satellite and meteorological input only when the downscaling model took
 *  part; an IDW estimate comes from ground sensors alone. */
export function estimationSourceLabel(source: string | null, fallback: readonly Pollutant[]): string {
  const sensors = tr("interpolasi dari sensor darat SPKU", "interpolation from SPKU ground sensors");
  const fellBack = fallback.map((p) => POLLUTANT_LABEL[p]).join(tr(" dan ", " and "));
  switch (source) {
    case "idw":
      return fallback.length
        ? tr("Interpolasi dari sensor darat SPKU (IDW); model downscaling tidak tersedia", "Interpolation from SPKU ground sensors (IDW); downscaling model unavailable")
        : tr("Interpolasi dari sensor darat SPKU (IDW); model downscaling tidak dipakai", "Interpolation from SPKU ground sensors (IDW); downscaling model not used");
    case "stgnn":
    case "hybrid":
      return tr("Model downscaling spasial dari data satelit, meteorologi, dan sensor darat", "Spatial downscaling model from satellite, meteorological and ground-sensor data") +
        (fallback.length ? tr(`; ${fellBack} memakai ${sensors} karena modelnya tidak tersedia`, `; ${fellBack} use ${sensors} because the model is unavailable`) : "");
    case "mixed":
      return tr(`Sebagian ruas dari model downscaling spasial, sebagian dari ${sensors}`, `Some segments from the spatial downscaling model, some from ${sensors}`);
    default:
      return tr("Estimasi spasial; sumbernya tidak dilaporkan server", "Spatial estimate; the server did not report its source");
  }
}

export function fmtCoord(p: Point): string {
  return `${p.lat.toFixed(5)}, ${p.lon.toFixed(5)}`;
}

/** "12.00 WIB" (Indonesian) or "12:00 WIB" (English) for an ISO 8601 timestamp, or null when it cannot be parsed. */
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
  return `${tr(time.replace(":", "."), time.replace(".", ":"))} WIB`;
}

/** Day, full month name and year: "23 November 2026" in either language. */
function longDate(d: Date, timeZone: string): string {
  return new Intl.DateTimeFormat(tr("id-ID", "en-GB"), { day: "numeric", month: "long", year: "numeric", timeZone }).format(d);
}

/** "23 November 2026, 22.00 WIB (GMT+7)" for an ISO 8601 timestamp, in Jakarta time; null when unreadable. */
export function fmtDateTimeWib(iso: string | null | undefined): string | null {
  const time = fmtWib(iso);
  if (!time) return null;
  return `${longDate(new Date(iso!), "Asia/Jakarta")}, ${time} (GMT+7)`;
}

/** The date in a road-graph version such as "jakarta-20261005" → "5 Oktober 2026"; null when it holds no date. */
export function fmtGraphVersionDate(version: string | null | undefined): string | null {
  const m = version ? /(\d{4})(\d{2})(\d{2})/.exec(version) : null;
  if (!m) return null;
  const d = new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3])));
  if (Number.isNaN(d.getTime()) || d.getUTCDate() !== Number(m[3])) return null;
  return longDate(d, "UTC");
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
