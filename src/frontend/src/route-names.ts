import type { Place, Point } from "./types";

// "via Jl. …" labels for the route list. Routes carry no street names, so the point in the
// middle of a route is reverse-geocoded (the same /v1/geocode/reverse the form uses) and the
// street is read from the answer: the place itself when it is a street, else the street part
// of its address ("Jalan MH Thamrin kav 22, Menteng" → "Jl. MH Thamrin").

const STREET = /^(jalan|jl\.?)\s+/i;

/** "Jalan MH. Thamrin" → "Jl. MH. Thamrin". */
function shorten(street: string): string {
  return street.replace(STREET, "Jl. ").replace(/\s+/g, " ").trim();
}

/** The street a reverse-geocoded place lies on, or null when the answer names none. */
export function streetFromPlace(place: Pick<Place, "name" | "description"> | null): string | null {
  if (!place) return null;
  if (STREET.test(place.name)) return shorten(place.name);
  const first = place.description.split(",")[0]?.trim() ?? "";
  if (!STREET.test(first)) return null;
  // Drop the house number and what follows: "No. 8", "8", "kav 22", "Blok A".
  const street = first.replace(/\s+(no\.?\s*\d|kav\.?\s*\d|blok\s|\d).*$/i, "");
  return STREET.test(street) && street.replace(STREET, "").trim() ? shorten(street) : null;
}

/** The middle vertex of a route given as [lon, lat] pairs. */
export function routeMidpoint(coordinates: ReadonlyArray<readonly [number, number]>): Point | null {
  if (!coordinates.length) return null;
  const [lon, lat] = coordinates[Math.floor(coordinates.length / 2)];
  return { lat, lon };
}
