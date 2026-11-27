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

/** Navigation screen over the map (TI-SE-13), laid out like a directions app: the instruction in
 *  a banner at the top (with "Then …" when the next turn follows soon), status notes as a small
 *  pill under it, and a card at the bottom with time left, distance and arrival time and the exit
 *  button. Built once, then only its text changes, so the buttons keep focus and screen readers
 *  hear a new instruction only when it changes. */
export class NavView {
  private readonly turnIcon = h("span", { class: "nav-turn-icon", "aria-hidden": "true" });
  private readonly action = h("strong", { class: "nav-action" });
  private readonly when = h("span", { class: "nav-when" });
  private readonly thenIcon = h("span", { class: "nav-then-icon", "aria-hidden": "true" });
  private readonly thenBox = h("div", { class: "nav-then", hidden: true }, h("span", {}, tr("Lalu", "Then")), this.thenIcon);
  private readonly banner = h("div", { class: "nav-banner" }, h("div", { class: "nav-instruction" }, this.turnIcon, h("div", {}, this.action, this.when)), this.thenBox);
  private readonly time = h("strong", { class: "nav-time" });
  private readonly summary = h("span", { class: "nav-summary" });
  private readonly status = h("div", { class: "nav-status" });
  private readonly live = h("p", { class: "visually-hidden", "aria-live": "polite" });
  private readonly sheet: HTMLElement;
  /** Receives focus when navigation starts. */
  readonly title = h("h2", { class: "visually-hidden", tabindex: "-1" }, tr("Navigasi", "Navigation"));
  private readonly recenter: HTMLButtonElement;
  private readonly finish: HTMLButtonElement;
  private announced = "";
  /** The notice now shown. It is rebuilt only when it changes, so its live role does not
   *  re-announce the same notice on every fix. */
  private shownMessage = "";

  constructor(container: HTMLElement, handlers: { onEnd: () => void; onRecenter: () => void }) {
    this.recenter = h("button", { type: "button", class: "nav-recenter", onclick: handlers.onRecenter }, icon("navigate", 18), ` ${tr("Ikuti posisi", "Re-centre")}`);
    this.finish = h("button", { type: "button", class: "nav-end", onclick: handlers.onEnd }, tr("Keluar", "Exit"));
    this.sheet = h(
      "div",
      { class: "nav-sheet" },
      this.recenter,
      h("div", { class: "nav-sheet-card" }, h("div", { class: "nav-trip" }, this.time, this.summary), this.finish),
    );
    clear(container);
    this.banner.append(this.status); // status notes stack under the instruction
    container.append(this.title, this.banner, this.sheet, this.live);
  }

  /** Space the banner (top) and the bottom card take over the map, for the camera padding. */
  insets(): { top: number; bottom: number } {
    const map = this.sheet.offsetParent as HTMLElement | null;
    if (!map) return { top: 0, bottom: 0 };
    const card = this.sheet.querySelector<HTMLElement>(".nav-sheet-card")!;
    return {
      top: this.banner.offsetTop + this.banner.offsetHeight,
      bottom: map.clientHeight - (this.sheet.offsetTop + card.offsetTop),
    };
  }

  render(state: NavViewState): void {
    const { snapshot: s } = state;
    const arrived = s.status === "arrived";
    const step = arrived ? { action: tr("Anda telah tiba", "You have arrived"), when: "", icon: "flag" as IconName } : instruction(s.next);
    clear(this.turnIcon);
    this.turnIcon.append(icon(step.icon, 36));
    this.action.textContent = step.action;
    this.when.textContent = step.when;

    // "Then …" only when the turn after this one comes soon after it.
    const then = !arrived && s.next && s.then && s.then.gapM <= THEN_WITHIN_M ? s.then.maneuver : null;
    this.thenBox.hidden = !then;
    clear(this.thenIcon);
    if (then) this.thenIcon.append(icon(then.kind === "arrive" ? "flag" : TURN[then.kind].icon, 20));

    this.time.textContent = arrived ? tr("Tiba", "Arrived") : s.remainingMin < 1 ? tr("< 1 menit", "< 1 min") : fmtMinutes(s.remainingMin);
    this.summary.textContent = arrived ? state.destination : `${fmtAhead(s.remainingM)} · ${fmtArrival(s.remainingMin)}`;

    const message = state.message ?? statusMessage(s);
    const key = message ? `${message.kind}\n${message.title}\n${message.text}` : "";
    if (key !== this.shownMessage) {
      this.shownMessage = key;
      clear(this.status);
      if (message) {
        this.status.append(
          h(
            "div",
            { class: `nav-pill nav-pill-${message.kind}`, role: message.kind === "error" ? "alert" : "status", title: message.text },
            icon(message.kind === "done" ? "flag" : message.kind === "info" ? "info" : "warn", 16),
            h("span", {}, message.title),
          ),
        );
      }
    }
    this.recenter.hidden = state.following || arrived;
    this.finish.textContent = arrived ? tr("Selesai", "Done") : tr("Keluar", "Exit");
    this.finish.classList.toggle("is-done", arrived);
    this.finish.setAttribute("aria-label", arrived ? tr("Selesai", "Done") : tr("Akhiri navigasi", "End navigation"));

    // Announce the turn, not every metre of the countdown.
    const spoken = arrived ? tr("Anda telah tiba di tujuan.", "You have arrived at your destination.") : `${step.action}${message ? `. ${message.title}` : ""}`;
    if (spoken !== this.announced) {
      this.announced = spoken;
      this.live.textContent = spoken;
    }
  }
}

/** "Then …" is shown when the turn after the next one is at most this far after it. */
const THEN_WITHIN_M = 250;

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
    case "arrived": // the banner already says so
    case "tracking":
      return null;
  }
}
