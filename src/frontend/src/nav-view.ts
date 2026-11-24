import { clear, h, icon, type IconName } from "./dom";
import { fmtDistance, fmtMinutes, fmtNumber } from "./format";
import { tr } from "./i18n";
import type { Maneuver } from "./nav-geometry";
import type { NavSnapshot } from "./navigation";

const TURN: Record<Maneuver["kind"], { text: () => string; icon: IconName }> = {
  slight_left: { text: () => tr("Sedikit ke kiri", "Bear left"), icon: "slight_left" },
  slight_right: { text: () => tr("Sedikit ke kanan", "Bear right"), icon: "slight_right" },
  left: { text: () => tr("Belok kiri", "Turn left"), icon: "turn_left" },
  right: { text: () => tr("Belok kanan", "Turn right"), icon: "turn_right" },
  sharp_left: { text: () => tr("Belok tajam ke kiri", "Sharp left"), icon: "turn_left" },
  sharp_right: { text: () => tr("Belok tajam ke kanan", "Sharp right"), icon: "turn_right" },
  uturn: { text: () => tr("Putar balik", "Make a U-turn"), icon: "uturn" },
  arrive: { text: () => tr("Tiba di tujuan", "Arrive at destination"), icon: "flag" },
};

/** Coarser the farther away, as spoken directions are: 15 m, 120 m, 1,2 km. */
export function fmtAhead(meters: number): string {
  if (meters >= 1000) return fmtDistance(meters);
  return fmtDistance(meters < 50 ? Math.max(5, Math.round(meters / 5) * 5) : Math.round(meters / 10) * 10);
}

/** The instruction for the next maneuver, e.g. { action: "Belok kiri", when: "dalam 120 m" }. */
export function instruction(next: NavSnapshot["next"]): { action: string; when: string; icon: IconName } {
  if (!next) return { action: tr("Ikuti rute", "Follow the route"), when: "", icon: "straight" };
  const { maneuver, distanceM } = next;
  if (maneuver.kind === "arrive") {
    return distanceM <= 50
      ? { action: tr("Tujuan di depan", "Destination ahead"), when: tr(`${fmtAhead(distanceM)} lagi`, `in ${fmtAhead(distanceM)}`), icon: "flag" }
      : { action: tr("Ikuti jalan ini sampai tujuan", "Continue on this road to the destination"), when: fmtAhead(distanceM), icon: "straight" };
  }
  const t = TURN[maneuver.kind];
  return {
    action: t.text(),
    when: distanceM < 15 ? tr("sekarang", "now") : tr(`dalam ${fmtAhead(distanceM)}`, `in ${fmtAhead(distanceM)}`),
    icon: t.icon,
  };
}

/** "14.32" (Indonesian) or "14:32" (English) for the arrival time, local clock. */
export function fmtArrival(remainingMin: number, now = new Date()): string {
  const at = new Date(now.getTime() + remainingMin * 60_000);
  return `${String(at.getHours()).padStart(2, "0")}${tr(".", ":")}${String(at.getMinutes()).padStart(2, "0")}`;
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
  readonly title = h("h2", { class: "title result-title", tabindex: "-1" }, tr("Navigasi", "Navigation"));
  private readonly recenter: HTMLButtonElement;
  private readonly finish: HTMLButtonElement;
  private announced = "";
  /** The notice now shown. It is rebuilt only when it changes, so its live role does not
   *  re-announce the same notice on every fix. */
  private shownMessage = "";

  constructor(container: HTMLElement, handlers: { onEnd: () => void; onRecenter: () => void }) {
    this.recenter = h("button", { type: "button", class: "secondary", onclick: handlers.onRecenter }, icon("locate", 18), ` ${tr("Ikuti posisi", "Follow position")}`);
    this.finish = h("button", { type: "button", class: "secondary dark nav-end", onclick: handlers.onEnd }, tr("Akhiri navigasi", "End navigation"));
    clear(container);
    container.append(
      h("div", { class: "result-head" }, h("div", { class: "trip-summary" }, h("div", { class: "trip-sub" }, tr("Navigasi ke", "Navigating to")), this.target)),
      this.title,
      // Above the instruction: on a phone the sheet shows only its top part.
      this.status,
      h("div", { class: "nav-instruction" }, this.turnIcon, h("div", {}, this.action, this.when)),
      h(
        "dl",
        { class: "metrics nav-metrics" },
        h("div", { class: "metric" }, h("dt", {}, tr("Sisa jarak", "Distance left")), this.remaining),
        h("div", { class: "metric" }, h("dt", {}, tr("Sisa waktu", "Time left")), this.time),
        h("div", { class: "metric" }, h("dt", {}, tr("Tiba sekitar", "Arrival around")), this.arrival),
      ),
      this.live,
      h("div", { class: "nav-actions" }, this.recenter, this.finish),
      h(
        "p",
        { class: "place-notice" },
        tr(
          "Posisi Anda diproses di perangkat ini. Server hanya menerimanya saat rute dihitung ulang dan tidak menyimpannya.",
          "Your position is processed on this device. The server only receives it when the route is recalculated and does not store it.",
        ),
      ),
    );
  }

  render(state: NavViewState): void {
    const { snapshot: s } = state;
    this.target.textContent = state.destination;
    const arrived = s.status === "arrived";
    const step = arrived ? { action: tr("Anda telah tiba", "You have arrived"), when: "", icon: "flag" as IconName } : instruction(s.next);
    clear(this.turnIcon);
    this.turnIcon.append(icon(step.icon, 34));
    this.action.textContent = step.action;
    this.when.textContent = step.when;
    this.remaining.textContent = arrived ? fmtDistance(0) : fmtAhead(s.remainingM);
    this.time.textContent = s.remainingMin < 1 ? tr("< 1 menit", "< 1 min") : fmtMinutes(s.remainingMin);
    this.arrival.textContent = arrived ? "—" : fmtArrival(s.remainingMin);

    const message = state.message ?? statusMessage(s);
    const key = message ? `${message.kind}\n${message.title}\n${message.text}` : "";
    if (key !== this.shownMessage) {
      this.shownMessage = key;
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
    }
    this.recenter.hidden = state.following || arrived;
    this.finish.textContent = arrived ? tr("Selesai", "Done") : tr("Akhiri navigasi", "End navigation");

    // Announce the turn, not every metre of the countdown.
    const spoken = arrived ? tr("Anda telah tiba di tujuan.", "You have arrived at your destination.") : `${step.action}${message ? `. ${message.title}` : ""}`;
    if (spoken !== this.announced) {
      this.announced = spoken;
      this.live.textContent = spoken;
    }
  }
}

function statusMessage(s: NavSnapshot): NavMessage | null {
  switch (s.status) {
    case "waiting":
      return {
        kind: "info",
        title: tr("Menunggu posisi GPS…", "Waiting for GPS position…"),
        text: tr("Posisi Anda akan tampil di peta begitu sinyal didapat.", "Your position will appear on the map as soon as there is a signal."),
      };
    case "weak_signal": {
      // In steps of 10 m: the text (and so the notice) does not change with every fix.
      const acc = fmtNumber(Math.ceil((s.fix?.accuracyM ?? 0) / 10) * 10, 0);
      return {
        kind: "warn",
        title: tr("Sinyal GPS lemah", "Weak GPS signal"),
        text: tr(
          `Akurasi sekitar ±${acc} m, jadi posisi di peta bisa meleset. Deteksi keluar rute ditunda sampai sinyal membaik.`,
          `Accuracy is about ±${acc} m, so the position on the map may be off. Off-route detection is paused until the signal improves.`,
        ),
      };
    }
    case "off_route":
      return { kind: "warn", title: tr("Anda keluar dari rute", "You left the route"), text: tr("Mencari rute baru dari posisi Anda…", "Finding a new route from your position…") };
    case "arrived":
      return {
        kind: "done",
        title: tr("Anda telah tiba di tujuan", "You have arrived at your destination"),
        text: tr("Navigasi selesai dan pelacakan posisi dihentikan.", "Navigation is finished and position tracking has stopped."),
      };
    case "tracking":
      return null;
  }
}
