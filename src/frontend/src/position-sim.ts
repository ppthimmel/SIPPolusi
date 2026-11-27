// Simulated positions for testing navigation without walking the route (TI-SE-13).
// Development builds only: main.ts loads this module with `?simulasi=<skenario>` when
// import.meta.env.DEV is true, so production bundles never contain it.
import { bearingAt, pointAt, type RouteLine } from "./nav-geometry";
import type { Fix } from "./navigation";
import type { PositionProblem, PositionSource } from "./position";
import type { Point } from "./types";
import { h, icon } from "./dom";
import { tr } from "./i18n";

export const SIM_SCENARIOS = {
  normal: "Berjalan mengikuti rute sampai tujuan",
  keluar: "Menyimpang ±80 m dari rute di 40% perjalanan, lalu mengikuti rute baru",
  lemah: "Akurasi ±120 m di antara 30% dan 50% perjalanan",
  tolak: "Izin lokasi ditolak",
  "tanpa-gps": "Posisi tidak tersedia",
} as const;
export type SimScenario = keyof typeof SIM_SCENARIOS;

export function isSimScenario(value: string | null): value is SimScenario {
  return value !== null && value in SIM_SCENARIOS;
}

/** How far a manual detour (setDetour) walks away from the route, in metres. */
export const DETOUR_M = 80;

/** Moves `along` metres per tick along whatever route `line()` returns at the time. */
export class SimulatedPosition implements PositionSource {
  private timer: ReturnType<typeof setInterval> | undefined;
  private along = 0;
  private offset = 0;
  private detoured = false;
  private startLength = 0;
  /** Manual detour from the test panel: "away" walks off the route at a right angle up to
   *  DETOUR_M and waits there, "back" walks back onto it, null follows the scenario. */
  private manual: "away" | "back" | null = null;

  constructor(
    private readonly line: () => RouteLine,
    private readonly scenario: SimScenario,
    private readonly speedMps = 8,
    private readonly tickMs = 1_000,
    startAlongM = 0,
  ) {
    this.along = startAlongM;
  }

  /** Test panel: leave the route (true) or walk back onto it (false). */
  setDetour(away: boolean): void {
    this.manual = away ? "away" : "back";
  }

  /** True while the simulated position is off the route or heading away from it. */
  get isDetoured(): boolean {
    return this.manual === "away" || this.offset > 0;
  }

  start(onFix: (fix: Fix) => void, onProblem: (problem: PositionProblem) => void): void {
    this.stop();
    if (this.scenario === "tolak") return onProblem("denied");
    if (this.scenario === "tanpa-gps") return onProblem("unavailable");
    this.startLength = this.line().length;
    const step = this.speedMps * (this.tickMs / 1000);
    const tick = () => {
      const line = this.line();
      const fraction = this.startLength ? this.along / this.startLength : 0;
      if (this.manual === "away") {
        this.offset = Math.min(DETOUR_M, this.offset + step); // and stay there, not moving along
      } else if (this.manual === "back" && this.offset > 0) {
        this.offset = Math.max(0, this.offset - step);
        if (this.offset === 0) this.manual = null;
      } else if (this.scenario === "keluar" && !this.detoured && fraction >= 0.4) {
        // Walk away from the route at a right angle until a new route arrives (routeChanged()).
        this.offset = Math.min(80, this.offset + step);
      } else {
        this.along = Math.min(line.length, this.along + step);
      }
      const onRoute = pointAt(line, this.along);
      const p = this.offset ? sideways(onRoute, bearingAt(line, this.along) + 90, this.offset) : onRoute;
      const weak = this.scenario === "lemah" && fraction >= 0.3 && fraction < 0.5;
      onFix({
        ...p,
        accuracyM: weak ? 120 : 8,
        headingDeg: null,
        speedMps: null,
        time: Date.now(),
      });
    };
    tick();
    this.timer = setInterval(tick, this.tickMs);
  }

  /** The route was replaced by one starting at the current position. */
  routeChanged(): void {
    if (this.offset) this.detoured = true;
    this.along = 0;
    this.offset = 0;
    this.manual = null; // the new route starts where the detour ended: back on a route
  }

  stop(): void {
    clearInterval(this.timer);
    this.timer = undefined;
  }
}

/** A small panel over the map while navigating (development builds only), to leave the route
 *  and come back on demand instead of waiting for a scenario. `toggle` is called with true to
 *  leave and false to return. `isAway` reports the simulator's own state, which the panel
 *  follows: a new route that arrives off the route puts it back on one by itself. */
export function simPanel(toggle: (away: boolean) => void, isAway: () => boolean): HTMLElement {
  let away = isAway();
  const label = h("span", {});
  const button = h("button", { type: "button", class: "sim-btn" }, icon("uturn", 16), label);
  const note = h("p", { class: "sim-note" });
  const draw = () => {
    label.textContent = away ? tr("Kembali ke rute", "Return to the route") : tr("Keluar dari rute", "Leave the route");
    note.textContent = away
      ? tr(`Menjauh ±${DETOUR_M} m; rute baru dicari setelah 10 detik di luar rute.`, `Moving ±${DETOUR_M} m away; a new route is asked for after 10 s off it.`)
      : tr("Posisi disimulasikan mengikuti rute.", "Position is simulated along the route.");
    button.setAttribute("aria-pressed", String(away));
  };
  button.addEventListener("click", () => {
    away = !away;
    toggle(away);
    draw();
  });
  draw();
  const panel = h("div", { class: "sim-panel", role: "group", "aria-label": tr("Uji navigasi (pengembangan)", "Navigation test (development)") },
    h("strong", { class: "sim-title" }, tr("Uji navigasi", "Navigation test")), button, note);
  // Follow the simulator (twice a second) until the panel leaves the page.
  const sync = window.setInterval(() => {
    if (!panel.isConnected) return window.clearInterval(sync);
    const now = isAway();
    if (now !== away) {
      away = now;
      draw();
    }
  }, 500);
  return panel;
}

function sideways(p: Point, bearing: number, metres: number): Point {
  const b = (bearing * Math.PI) / 180;
  const dLat = (metres * Math.cos(b)) / 111_320;
  const dLon = (metres * Math.sin(b)) / (111_320 * Math.cos((p.lat * Math.PI) / 180));
  return { lat: p.lat + dLat, lon: p.lon + dLon };
}
