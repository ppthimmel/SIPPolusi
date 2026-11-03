import { clear, h, icon, type IconName } from "./dom";
import { fmtDistance, fmtMinutes, fmtNumber } from "./format";
import type { Maneuver } from "./nav-geometry";
import type { NavSnapshot } from "./navigation";

const TURN: Record<Maneuver["kind"], { text: string; icon: IconName }> = {
  slight_left: { text: "Sedikit ke kiri", icon: "slight_left" },
  slight_right: { text: "Sedikit ke kanan", icon: "slight_right" },
  left: { text: "Belok kiri", icon: "turn_left" },
  right: { text: "Belok kanan", icon: "turn_right" },
  sharp_left: { text: "Belok tajam ke kiri", icon: "turn_left" },
  sharp_right: { text: "Belok tajam ke kanan", icon: "turn_right" },
  uturn: { text: "Putar balik", icon: "uturn" },
  arrive: { text: "Tiba di tujuan", icon: "flag" },
};

/** Coarser the farther away, as spoken directions are: 15 m, 120 m, 1,2 km. */
export function fmtAhead(meters: number): string {
  if (meters >= 1000) return fmtDistance(meters);
  return fmtDistance(meters < 50 ? Math.max(5, Math.round(meters / 5) * 5) : Math.round(meters / 10) * 10);
}

/** The instruction for the next maneuver, e.g. { action: "Belok kiri", when: "dalam 120 m" }. */
export function instruction(next: NavSnapshot["next"]): { action: string; when: string; icon: IconName } {
  if (!next) return { action: "Ikuti rute", when: "", icon: "straight" };
  const { maneuver, distanceM } = next;
  if (maneuver.kind === "arrive") {
    return distanceM <= 50
      ? { action: "Tujuan di depan", when: `${fmtAhead(distanceM)} lagi`, icon: "flag" }
      : { action: "Ikuti jalan ini sampai tujuan", when: fmtAhead(distanceM), icon: "straight" };
  }
  const t = TURN[maneuver.kind];
  return { action: t.text, when: distanceM < 15 ? "sekarang" : `dalam ${fmtAhead(distanceM)}`, icon: t.icon };
}

/** "14.32" for the arrival time, local clock. */
export function fmtArrival(remainingMin: number, now = new Date()): string {
  const at = new Date(now.getTime() + remainingMin * 60_000);
  return `${String(at.getHours()).padStart(2, "0")}.${String(at.getMinutes()).padStart(2, "0")}`;
}

export interface NavMessage {
  kind: "info" | "warn" | "error" | "done";
  title: string;
  text: string;
}

export interface NavViewState {
  snapshot: NavSnapshot;
  destination: string;
  /** Overrides the message the status would give (reroute progress, GPS problems). */
  message: NavMessage | null;
  following: boolean;
}

/** The navigation panel (TI-SE-13). Built once, then only its text changes, so the buttons
 *  keep focus and screen readers hear a new instruction only when it changes. */
export class NavView {
  private readonly turnIcon = h("span", { class: "nav-turn-icon", "aria-hidden": "true" });
  private readonly action = h("strong", { class: "nav-action" });
  private readonly when = h("span", { class: "nav-when" });
  private readonly remaining = h("dd", { class: "mono" });
  private readonly time = h("dd", { class: "mono" });
  private readonly arrival = h("dd", { class: "mono" });
  private readonly status = h("div", { class: "nav-status" });
  private readonly live = h("p", { class: "visually-hidden", "aria-live": "polite" });
  private readonly target = h("div", { class: "trip-line" });
  /** Receives focus when navigation starts. */
  readonly title = h("h2", { class: "title result-title", tabindex: "-1" }, "Navigasi");
  private readonly recenter: HTMLButtonElement;
  private readonly finish: HTMLButtonElement;
  private announced = "";

  constructor(container: HTMLElement, handlers: { onEnd: () => void; onRecenter: () => void }) {
    this.recenter = h("button", { type: "button", class: "secondary", onclick: handlers.onRecenter }, icon("locate", 18), " Ikuti posisi");
    this.finish = h("button", { type: "button", class: "secondary dark nav-end", onclick: handlers.onEnd }, "Akhiri navigasi");
    clear(container);
    container.append(
      h("div", { class: "result-head" }, h("div", { class: "trip-summary" }, h("div", { class: "trip-sub" }, "Navigasi ke"), this.target)),
      this.title,
      // Above the instruction: on a phone the sheet shows only its top part.
      this.status,
      h("div", { class: "nav-instruction" }, this.turnIcon, h("div", {}, this.action, this.when)),
      h(
        "dl",
        { class: "metrics nav-metrics" },
        h("div", { class: "metric" }, h("dt", {}, "Sisa jarak"), this.remaining),
        h("div", { class: "metric" }, h("dt", {}, "Sisa waktu"), this.time),
        h("div", { class: "metric" }, h("dt", {}, "Tiba sekitar"), this.arrival),
      ),
      this.live,
      h("div", { class: "nav-actions" }, this.recenter, this.finish),
      h(
        "p",
        { class: "place-notice" },
        "Posisi Anda diproses di perangkat ini. Server hanya menerimanya saat rute dihitung ulang dan tidak menyimpannya.",
      ),
    );
  }

  render(state: NavViewState): void {
    const { snapshot: s } = state;
    this.target.textContent = state.destination;
    const arrived = s.status === "arrived";
    const step = arrived ? { action: "Anda telah tiba", when: "", icon: "flag" as IconName } : instruction(s.next);
    clear(this.turnIcon);
    this.turnIcon.append(icon(step.icon, 34));
    this.action.textContent = step.action;
    this.when.textContent = step.when;
    this.remaining.textContent = arrived ? fmtDistance(0) : fmtAhead(s.remainingM);
    this.time.textContent = s.remainingMin < 1 ? "< 1 menit" : fmtMinutes(s.remainingMin);
    this.arrival.textContent = arrived ? "—" : fmtArrival(s.remainingMin);

    const message = state.message ?? statusMessage(s);
    clear(this.status);
    if (message) {
      this.status.append(
        h(
          "div",
          { class: `notice notice-${message.kind === "done" ? "done" : message.kind}`, role: message.kind === "error" ? "alert" : "status" },
          icon(message.kind === "done" ? "flag" : message.kind === "info" ? "info" : "warn", 18),
          h("div", {}, h("strong", {}, message.title), message.text && h("p", {}, message.text)),
        ),
      );
    }
    this.recenter.hidden = state.following || arrived;
    this.finish.textContent = arrived ? "Selesai" : "Akhiri navigasi";

    // Announce the turn, not every metre of the countdown.
    const spoken = arrived ? "Anda telah tiba di tujuan." : `${step.action}${message ? `. ${message.title}` : ""}`;
    if (spoken !== this.announced) {
      this.announced = spoken;
      this.live.textContent = spoken;
    }
  }
}

function statusMessage(s: NavSnapshot): NavMessage | null {
  switch (s.status) {
    case "waiting":
      return { kind: "info", title: "Menunggu posisi GPS…", text: "Posisi Anda akan tampil di peta begitu sinyal didapat." };
    case "weak_signal":
      return {
        kind: "warn",
        title: "Sinyal GPS lemah",
        text: `Akurasi sekitar ±${fmtNumber(Math.round(s.fix?.accuracyM ?? 0), 0)} m, jadi posisi di peta bisa meleset. Deteksi keluar rute ditunda sampai sinyal membaik.`,
      };
    case "off_route":
      return { kind: "warn", title: "Anda keluar dari rute", text: "Mencari rute baru dari posisi Anda…" };
    case "arrived":
      return { kind: "done", title: "Anda telah tiba di tujuan", text: "Navigasi selesai dan pelacakan posisi dihentikan." };
    case "tracking":
      return null;
  }
}
