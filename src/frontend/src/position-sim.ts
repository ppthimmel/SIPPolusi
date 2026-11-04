// Simulated positions for testing navigation without walking the route (TI-SE-13).
// Development builds only: main.ts loads this module with `?simulasi=<skenario>` when
// import.meta.env.DEV is true, so production bundles never contain it.
import { bearingAt, pointAt, type RouteLine } from "./nav-geometry";
import type { Fix } from "./navigation";
import type { PositionProblem, PositionSource } from "./position";
import type { Point } from "./types";

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

/** Moves `along` metres per tick along whatever route `line()` returns at the time. */
export class SimulatedPosition implements PositionSource {
  private timer: ReturnType<typeof setInterval> | undefined;
  private along = 0;
  private offset = 0;
  private detoured = false;
  private startLength = 0;

  constructor(
    private readonly line: () => RouteLine,
    private readonly scenario: SimScenario,
    private readonly speedMps = 8,
    private readonly tickMs = 1_000,
  ) {}

  start(onFix: (fix: Fix) => void, onProblem: (problem: PositionProblem) => void): void {
    this.stop();
    if (this.scenario === "tolak") return onProblem("denied");
    if (this.scenario === "tanpa-gps") return onProblem("unavailable");
    this.startLength = this.line().length;
    const step = this.speedMps * (this.tickMs / 1000);
    const tick = () => {
      const line = this.line();
      const fraction = this.startLength ? this.along / this.startLength : 0;
      if (this.scenario === "keluar" && !this.detoured && fraction >= 0.4) {
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
  }

  stop(): void {
    clearInterval(this.timer);
    this.timer = undefined;
  }
}

function sideways(p: Point, bearing: number, metres: number): Point {
  const b = (bearing * Math.PI) / 180;
  const dLat = (metres * Math.cos(b)) / 111_320;
  const dLon = (metres * Math.sin(b)) / (111_320 * Math.cos((p.lat * Math.PI) / 180));
  return { lat: p.lat + dLat, lon: p.lon + dLon };
}
