import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "./api";
import type { HeatStatus } from "./legend";
import { SurfaceRefresher } from "./surface-refresher";
import type { ExposureSurface } from "./types";

const surface = (label: string): ExposureSurface => ({ timeWindowStart: label }) as unknown as ExposureSurface;

/** A load() whose responses the test releases by hand, to stage late arrivals. */
function controllableLoad() {
  const calls: { signal: AbortSignal; resolve: (s: ExposureSurface) => void; reject: (e: unknown) => void }[] = [];
  const load = vi.fn(
    (signal: AbortSignal) =>
      new Promise<ExposureSurface>((resolve, reject) => {
        calls.push({ signal, resolve, reject });
        signal.addEventListener("abort", () => reject(new ApiError("aborted", "dibatalkan")));
      }),
  );
  return { load, calls };
}

describe("SurfaceRefresher", () => {
  let updates: { status: HeatStatus; surface: ExposureSurface | null }[];
  const record = (status: HeatStatus, s: ExposureSurface | null) => updates.push({ status, surface: s });

  beforeEach(() => {
    vi.useFakeTimers();
    updates = [];
  });
  afterEach(() => vi.useRealTimers());

  it("marks the shown surface as updating and cancels the old request as soon as the view moves", async () => {
    const { load, calls } = controllableLoad();
    const r = new SurfaceRefresher(load, record, 500);
    r.schedule(true);
    await vi.advanceTimersByTimeAsync(0);
    calls[0].resolve(surface("A"));
    await vi.advanceTimersByTimeAsync(0);
    expect(r.status).toBe("ready");

    // Second view: request B in flight, then the user pans again.
    r.schedule();
    await vi.advanceTimersByTimeAsync(500);
    expect(calls).toHaveLength(2);
    r.schedule();
    // Before the debounce has elapsed, the previous request is already cancelled
    // and the surface on screen is flagged as being updated.
    expect(calls[1].signal.aborted).toBe(true);
    expect(r.status).toBe("loading");
    expect(updates[updates.length - 1]).toEqual({ status: "loading", surface: surface("A") });

    // A late response for the area that was left changes nothing.
    calls[1].resolve(surface("B"));
    await vi.advanceTimersByTimeAsync(100);
    expect(r.status).toBe("loading");
    expect(r.surface).toEqual(surface("A"));
    expect(updates.some((u) => u.status === "ready" && u.surface?.timeWindowStart === "B")).toBe(false);

    // Only the request for the current view can mark the heatmap ready.
    await vi.advanceTimersByTimeAsync(400);
    expect(calls).toHaveLength(3);
    calls[2].resolve(surface("C"));
    await vi.advanceTimersByTimeAsync(0);
    expect(r.status).toBe("ready");
    expect(r.surface).toEqual(surface("C"));
  });

  it("does not let an in-flight response land as ready while a ready surface sits in the debounce window", async () => {
    const { load, calls } = controllableLoad();
    const r = new SurfaceRefresher(load, record, 500);
    r.schedule(true);
    await vi.advanceTimersByTimeAsync(0);
    r.schedule(); // view moved while the first request was still pending
    calls[0].resolve(surface("old"));
    await vi.advanceTimersByTimeAsync(0);
    expect(r.status).toBe("loading");
    expect(r.surface).toBeNull();
  });

  it("debounces bursts of view changes into one request", async () => {
    const { load } = controllableLoad();
    const r = new SurfaceRefresher(load, record, 500);
    for (let i = 0; i < 5; i++) {
      r.schedule();
      await vi.advanceTimersByTimeAsync(100);
    }
    await vi.advanceTimersByTimeAsync(500);
    expect(load).toHaveBeenCalledTimes(1);
  });

  it("keeps the last surface and reports an error when a refresh fails", async () => {
    const { load, calls } = controllableLoad();
    const r = new SurfaceRefresher(load, record, 500);
    r.schedule(true);
    await vi.advanceTimersByTimeAsync(0);
    calls[0].resolve(surface("A"));
    await vi.advanceTimersByTimeAsync(0);
    r.schedule(true);
    await vi.advanceTimersByTimeAsync(0);
    calls[1].reject(new ApiError("timeout", "habis waktu"));
    await vi.advanceTimersByTimeAsync(0);
    expect(r.status).toBe("error");
    expect(updates[updates.length - 1]).toEqual({ status: "error", surface: surface("A") });
  });

  it("stop() cancels the pending timer and the request in flight", async () => {
    const { load, calls } = controllableLoad();
    const r = new SurfaceRefresher(load, record, 500);
    r.schedule();
    r.stop();
    await vi.advanceTimersByTimeAsync(1000);
    expect(load).not.toHaveBeenCalled();

    r.schedule(true);
    await vi.advanceTimersByTimeAsync(0);
    r.stop();
    expect(calls[0].signal.aborted).toBe(true);
    await vi.advanceTimersByTimeAsync(0);
    expect(r.status).not.toBe("ready");
  });
});
