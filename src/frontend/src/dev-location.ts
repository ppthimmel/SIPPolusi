// A fixed position in Jakarta in place of the device's, for testing "Lokasi saya", "Gunakan
// lokasi saya sebagai asal" and navigation from a desk outside the study area.
// Development builds only: main.ts loads this module with `?lokasi=<tempat|lat,lon>` when
// import.meta.env.DEV is true, so production bundles never contain it. For movement along a
// route during navigation use `?simulasi=` (position-sim.ts) instead; it takes precedence there.
import type { Point } from "./types";

export const DEV_PLACES: Record<string, Point> = {
  monas: { lat: -6.1754, lon: 106.8272 },
  "bundaran-hi": { lat: -6.1950, lon: 106.8230 },
  sudirman: { lat: -6.2088, lon: 106.8227 },
  "blok-m": { lat: -6.2441, lon: 106.7993 },
  kota: { lat: -6.1376, lon: 106.8146 },
};

/** A place name from DEV_PLACES or "lat,lon"; null when it is neither. */
export function parseDevLocation(value: string | null): Point | null {
  if (!value) return null;
  const named = DEV_PLACES[value.trim().toLowerCase()];
  if (named) return named;
  const parts = value.split(",").map((s) => Number(s.trim()));
  if (parts.length !== 2 || parts.some((n) => !Number.isFinite(n))) return null;
  const [lat, lon] = parts;
  return Math.abs(lat) <= 90 && Math.abs(lon) <= 180 ? { lat, lon } : null;
}

function position(p: Point): GeolocationPosition {
  const coords = {
    latitude: p.lat,
    longitude: p.lon,
    accuracy: 8,
    altitude: null,
    altitudeAccuracy: null,
    heading: null,
    speed: null,
  };
  return { coords: { ...coords, toJSON: () => coords }, timestamp: Date.now(), toJSON: () => ({ coords, timestamp: Date.now() }) } as GeolocationPosition;
}

/** Replaces navigator.geolocation with one that always reports `p` (a new fix every second while watched). */
export function useFakeLocation(p: Point): void {
  const timers = new Map<number, ReturnType<typeof setInterval>>();
  let next = 1;
  const fake: Geolocation = {
    getCurrentPosition(success) {
      setTimeout(() => success(position(p)), 150);
    },
    watchPosition(success) {
      const id = next++;
      setTimeout(() => success(position(p)), 150);
      timers.set(id, setInterval(() => success(position(p)), 1_000));
      return id;
    },
    clearWatch(id) {
      clearInterval(timers.get(id));
      timers.delete(id);
    },
  };
  Object.defineProperty(navigator, "geolocation", { value: fake, configurable: true });
  console.info(`[dev] lokasi palsu aktif: ${p.lat}, ${p.lon}`);
}
