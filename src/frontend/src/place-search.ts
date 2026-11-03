import { ApiError } from "./api";
import { describePlaceError } from "./errors";
import type { Place, PlaceSearchResult, Point } from "./types";

export type PlaceSearchView =
  | { kind: "closed" }
  | { kind: "loading" }
  | { kind: "results"; places: Place[] }
  | { kind: "empty" }
  | { kind: "outside" }
  | { kind: "error"; message: string };

/** Suggestions for one field (TI-SE-12). Each keystroke cancels the request for the previous
 *  text at once, before the debounce starts, so a late answer can never replace newer text. */
export class PlaceSearch {
  view: PlaceSearchView = { kind: "closed" };
  private controller: AbortController | null = null;
  private timer: ReturnType<typeof setTimeout> | undefined;

  constructor(
    private readonly load: (query: string, signal: AbortSignal) => Promise<PlaceSearchResult>,
    private readonly onUpdate: (view: PlaceSearchView) => void,
    private readonly debounceMs: number,
    private readonly minChars: number,
  ) {}

  /** The user typed `text`. */
  schedule(text: string): void {
    this.cancel();
    const query = text.trim();
    if (query.length < this.minChars) return this.set({ kind: "closed" });
    this.set({ kind: "loading" });
    this.timer = setTimeout(() => void this.run(query), this.debounceMs);
  }

  /** A place was chosen, the field lost focus, or the text became coordinates. */
  stop(): void {
    this.cancel();
    if (this.view.kind !== "closed") this.set({ kind: "closed" });
  }

  private cancel(): void {
    clearTimeout(this.timer);
    this.timer = undefined;
    this.controller?.abort();
    this.controller = null;
  }

  private async run(query: string): Promise<void> {
    const controller = new AbortController();
    this.controller = controller;
    try {
      const { places, outsideStudyArea } = await this.load(query, controller.signal);
      if (this.controller !== controller) return;
      this.controller = null;
      if (places.length) this.set({ kind: "results", places });
      else this.set({ kind: outsideStudyArea ? "outside" : "empty" });
    } catch (err) {
      if (this.controller !== controller) return;
      this.controller = null;
      if (err instanceof ApiError && err.kind === "aborted") return;
      this.set({ kind: "error", message: describePlaceError(err) });
    }
  }

  private set(view: PlaceSearchView): void {
    this.view = view;
    this.onUpdate(view);
  }
}

/** Looks up the name of a point chosen on the map. The name only ever replaces the coordinates:
 *  on a failure or no place nearby they stay, which is still a usable answer. */
export class PointNamer {
  private controller: AbortController | null = null;

  constructor(
    private readonly load: (p: Point, signal: AbortSignal) => Promise<Place | null>,
    private readonly onName: (place: Place, p: Point) => void,
  ) {}

  name(p: Point): void {
    this.cancel();
    const controller = new AbortController();
    this.controller = controller;
    this.load(p, controller.signal).then(
      (place) => {
        if (this.controller !== controller) return;
        this.controller = null;
        if (place) this.onName(place, p);
      },
      () => {
        if (this.controller === controller) this.controller = null;
      },
    );
  }

  cancel(): void {
    this.controller?.abort();
    this.controller = null;
  }
}
