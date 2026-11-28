// The device's position for navigation and "Lokasi saya" (TI-SE-13), through the
// Geolocation API. Fixes go straight to the caller: nothing here keeps or logs them.
import type { Fix } from "./navigation";
import { tr } from "./i18n";

export type PositionProblem = "unsupported" | "insecure" | "denied" | "unavailable" | "timeout";

export interface PositionSource {
  start(onFix: (fix: Fix) => void, onProblem: (problem: PositionProblem) => void): void;
  stop(): void;
  /** Navigation switched to a new route (the simulator follows it; the device ignores it). */
  routeChanged?(): void;
}

/** Why the Geolocation API cannot be used at all here, or null when it can be tried. */
export function positionProblem(
  geolocation: Geolocation | undefined = globalThis.navigator?.geolocation,
  secure: boolean = globalThis.isSecureContext ?? true,
): PositionProblem | null {
  // Browsers expose geolocation only to HTTPS pages (and localhost).
  if (!secure) return "insecure";
  if (!geolocation) return "unsupported";
  return null;
}

/** GeolocationPositionError.code: 1 permission denied, 2 position unavailable, 3 timeout. */
export function problemFromCode(code: number): PositionProblem {
  return code === 1 ? "denied" : code === 3 ? "timeout" : "unavailable";
}

export function toFix(pos: GeolocationPosition): Fix {
  const c = pos.coords;
  const finite = (v: number | null) => (v !== null && Number.isFinite(v) ? v : null);
  return {
    lat: c.latitude,
    lon: c.longitude,
    accuracyM: Number.isFinite(c.accuracy) ? c.accuracy : Infinity,
    headingDeg: finite(c.heading),
    speedMps: finite(c.speed),
    time: pos.timestamp,
  };
}

const PROBLEM_TEXT: Record<PositionProblem, [id: string, en: string]> = {
  unsupported: ["Perangkat atau peramban ini tidak menyediakan lokasi (GPS).", "This device or browser does not provide location (GPS)."],
  insecure: ["Lokasi hanya tersedia bila halaman dibuka lewat koneksi aman (HTTPS).", "Location is only available when the page is opened over a secure connection (HTTPS)."],
  denied: [
    "Izin lokasi ditolak. Izinkan akses lokasi untuk situs ini di pengaturan peramban, lalu coba lagi.",
    "Location permission was denied. Allow location access for this site in the browser settings, then try again.",
  ],
  unavailable: [
    "Posisi tidak dapat ditentukan. Pastikan layanan lokasi (GPS) aktif, sebaiknya di tempat terbuka.",
    "Your position could not be determined. Make sure location services (GPS) are on, ideally outdoors.",
  ],
  timeout: ["Sinyal GPS belum didapat.", "No GPS signal yet."],
};

export function describePositionProblem(problem: PositionProblem): string {
  return tr(...PROBLEM_TEXT[problem]);
}

/** Problems after which no position will ever arrive in this session. */
export function isFatal(problem: PositionProblem): boolean {
  return problem === "unsupported" || problem === "insecure" || problem === "denied";
}

/** watchPosition with high accuracy: GPS where the device has it. */
export class DevicePosition implements PositionSource {
  private watchId: number | null = null;

  constructor(
    private readonly geolocation: Geolocation | undefined = globalThis.navigator?.geolocation,
    private readonly secure: boolean = globalThis.isSecureContext ?? true,
  ) {}

  start(onFix: (fix: Fix) => void, onProblem: (problem: PositionProblem) => void): void {
    this.stop();
    const problem = positionProblem(this.geolocation, this.secure);
    if (problem || !this.geolocation) return onProblem(problem ?? "unsupported");
    this.watchId = this.geolocation.watchPosition(
      (pos) => onFix(toFix(pos)),
      (err) => onProblem(problemFromCode(err.code)),
      // A fix at most 2 s old; the 15 s timeout reports a weak signal but the watch goes on.
      { enableHighAccuracy: true, maximumAge: 2_000, timeout: 15_000 },
    );
  }

  stop(): void {
    if (this.watchId !== null) this.geolocation?.clearWatch(this.watchId);
    this.watchId = null;
  }
}
