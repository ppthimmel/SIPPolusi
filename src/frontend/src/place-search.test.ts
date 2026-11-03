import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "./api";
import { PlaceSearch, PointNamer, type PlaceSearchView } from "./place-search";
import type { Place, PlaceSearchResult } from "./types";

const place = (name: string): Place => ({ name, description: "", lat: -6.2, lon: 106.8 });
const found = (...names: string[]): PlaceSearchResult => ({ places: names.map(place), outsideStudyArea: false });

/** A load() whose answers the test releases by hand, to stage late arrivals. */
function controllable<A, R>() {
  const calls: { arg: A; signal: AbortSignal; resolve: (r: R) => void; reject: (e: unknown) => void }[] = [];
  const load = vi.fn(
    (arg: A, signal: AbortSignal) =>
      new Promise<R>((resolve, reject) => {
        calls.push({ arg, signal, resolve, reject });
        signal.addEventListener("abort", () => reject(new ApiError("aborted", "dibatalkan")));
      }),
  );
  return { load, calls };
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe("PlaceSearch", () => {
  function setup() {
    const { load, calls } = controllable<string, PlaceSearchResult>();
    const views: PlaceSearchView[] = [];
    const search = new PlaceSearch(load, (v) => views.push(v), 300, 3);
    return { search, load, calls, views };
  }
  const settle = () => vi.advanceTimersByTimeAsync(0);

  it("does not search text shorter than the minimum", async () => {
    const { search, load, views } = setup();
    search.schedule("  mo ");
    await vi.advanceTimersByTimeAsync(1000);
    expect(load).not.toHaveBeenCalled();
    expect(views[views.length - 1]).toEqual({ kind: "closed" });
  });

  it("sends one request after the last keystroke, with the trimmed text", async () => {
    const { search, load, views } = setup();
    for (const text of ["mon", "mona", " monas "]) {
      search.schedule(text);
      await vi.advanceTimersByTimeAsync(100);
    }
    expect(views[views.length - 1]).toEqual({ kind: "loading" });
    expect(load).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(300);
    expect(load).toHaveBeenCalledTimes(1);
    expect(load.mock.calls[0][0]).toBe("monas");
  });

  it("cancels the request for older text at once and ignores its late answer", async () => {
    const { search, calls, views } = setup();
    search.schedule("monas");
    await vi.advanceTimersByTimeAsync(300);
    search.schedule("monas 2");
    expect(calls[0].signal.aborted).toBe(true);
    calls[0].resolve(found("Terlambat"));
    await settle();
    expect(views[views.length - 1]).toEqual({ kind: "loading" });
    await vi.advanceTimersByTimeAsync(300);
    calls[1].resolve(found("Monumen Nasional"));
    await settle();
    expect(views[views.length - 1]).toEqual({ kind: "results", places: [place("Monumen Nasional")] });
  });

  it("tells not found from found only outside the area", async () => {
    const { search, calls, views } = setup();
    search.schedule("tidak ada");
    await vi.advanceTimersByTimeAsync(300);
    calls[0].resolve({ places: [], outsideStudyArea: false });
    await settle();
    expect(views[views.length - 1]).toEqual({ kind: "empty" });
    search.schedule("bandung");
    await vi.advanceTimersByTimeAsync(300);
    calls[1].resolve({ places: [], outsideStudyArea: true });
    await settle();
    expect(views[views.length - 1]).toEqual({ kind: "outside" });
  });

  it.each([
    [new ApiError("http", "x", { status: 503, code: "geocoding_unavailable" }), /sedang tidak tersedia/],
    [new ApiError("network", "x"), /sedang tidak tersedia/],
    [new ApiError("timeout", "x"), /sedang tidak tersedia/],
    [new ApiError("invalid_response", "x"), /sedang tidak tersedia/],
    [new ApiError("http", "x", { status: 429, code: "rate_limited" }), /Terlalu banyak pencarian/],
  ])("explains a failure and points to the alternatives", async (err, message) => {
    const { search, calls, views } = setup();
    search.schedule("monas");
    await vi.advanceTimersByTimeAsync(300);
    calls[0].reject(err);
    await settle();
    const view = views[views.length - 1];
    expect(view?.kind).toBe("error");
    expect(view?.kind === "error" && view.message).toMatch(message);
    expect(view?.kind === "error" && view.message).toMatch(/peta/);
  });

  it("stop closes the list, cancels the request and drops pending keystrokes", async () => {
    const { search, load, calls, views } = setup();
    search.schedule("monas");
    await vi.advanceTimersByTimeAsync(300);
    search.schedule("monas 2");
    search.stop();
    expect(calls[0].signal.aborted).toBe(true);
    await vi.advanceTimersByTimeAsync(1000);
    expect(load).toHaveBeenCalledTimes(1);
    expect(views[views.length - 1]).toEqual({ kind: "closed" });
  });
});

describe("PointNamer", () => {
  const P = { lat: -6.2, lon: 106.8 };
  const Q = { lat: -6.3, lon: 106.9 };

  function setup() {
    const { load, calls } = controllable<typeof P, Place | null>();
    const names: [string, typeof P][] = [];
    return { namer: new PointNamer(load, (place, p) => names.push([place.name, p])), calls, names };
  }

  it("reports the name of the point", async () => {
    const { namer, calls, names } = setup();
    namer.name(P);
    calls[0].resolve(place("Monumen Nasional"));
    await vi.advanceTimersByTimeAsync(0);
    expect(names).toEqual([["Monumen Nasional", P]]);
  });

  it("only the newest point is named", async () => {
    const { namer, calls, names } = setup();
    namer.name(P);
    namer.name(Q);
    expect(calls[0].signal.aborted).toBe(true);
    calls[0].resolve(place("Lama"));
    calls[1].resolve(place("Baru"));
    await vi.advanceTimersByTimeAsync(0);
    expect(names).toEqual([["Baru", Q]]);
  });

  it("keeps quiet on no place, a failure and a cancelled lookup", async () => {
    const { namer, calls, names } = setup();
    namer.name(P);
    calls[0].resolve(null);
    await vi.advanceTimersByTimeAsync(0);
    namer.name(P);
    calls[1].reject(new ApiError("network", "x"));
    await vi.advanceTimersByTimeAsync(0);
    namer.name(P);
    namer.cancel();
    calls[2].resolve(place("Terlambat"));
    await vi.advanceTimersByTimeAsync(0);
    expect(names).toEqual([]);
  });
});
