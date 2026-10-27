import { ApiError } from "./api";
import type { HeatStatus } from "./legend";
import type { ExposureSurface } from "./types";

/** Keeps the heatmap in step with the map view (Tabel 3.4, 3.15).
 *
 *  A view change marks the surface on screen as being updated and cancels the
 *  request for the previous view at once, before the debounce starts, so a late
 *  response for an area the user has already left can never be shown as "ready". */
export class SurfaceRefresher {
  status: HeatStatus = "idle";
  /** Last surface that loaded; stays on screen (dimmed) when a refresh fails. */
  surface: ExposureSurface | null = null;
  private controller: AbortController | null = null;
  private timer: ReturnType<typeof setTimeout> | undefined;

  constructor(
    private readonly load: (signal: AbortSignal) => Promise<ExposureSurface>,
    private readonly onUpdate: (status: HeatStatus, surface: ExposureSurface | null) => void,
    private readonly debounceMs: number,
  ) {}

  /** The view changed or a refresh was asked for. */
  schedule(immediate = false): void {
    this.cancel();
    this.set("loading");
    this.timer = setTimeout(() => void this.run(), immediate ? 0 : this.debounceMs);
  }

  /** Heatmap switched off: nothing pending may land afterwards. */
  stop(): void {
    this.cancel();
  }

  private cancel(): void {
    clearTimeout(this.timer);
    this.timer = undefined;
    this.controller?.abort();
    this.controller = null;
  }

  private async run(): Promise<void> {
    const controller = new AbortController();
    this.controller = controller;
    try {
      const surface = await this.load(controller.signal);
      if (this.controller !== controller) return;
      this.controller = null;
      this.surface = surface;
      this.set("ready");
    } catch (err) {
      if (this.controller !== controller) return;
      this.controller = null;
      if (err instanceof ApiError && err.kind === "aborted") return;
      this.set("error");
    }
  }

  private set(status: HeatStatus): void {
    this.status = status;
    this.onUpdate(status, this.surface);
  }
}
