import { WHO_GUIDELINE } from "./config";
import { clear, h } from "./dom";
import { EXPOSURE_CLASSES, EXPOSURE_STOPS, HEAT_LAYER_LABELS } from "./exposure";
import { fmtNumber } from "./format";
import { tr } from "./i18n";
import { openIndexInfo } from "./index-info";
import type { HeatLayer } from "./types";

export type HeatStatus = "idle" | "loading" | "ready" | "error";

export interface HeatLegendState {
  visible: boolean;
  layer: HeatLayer;
  status: HeatStatus;
  /** The drawn data is older than 3 hours. */
  stale: boolean;
  /** True when something is drawn although the latest request failed. */
  showingOldData: boolean;
}

const TICKS = [0, 0.5, 1, 2, 3];
/** The bar is linear from 0 to SCALE_MAX, like the map's colour interpolation; values above it
 *  (the open-ended top class) get a solid end block taking the rest of the width. */
const SCALE_MAX = 3;
const LINEAR_SHARE = 85; // % of the bar width for 0 … SCALE_MAX
const scalePos = (v: number) => (Math.min(v, SCALE_MAX) / SCALE_MAX) * LINEAR_SHARE;
/** Tick labels: the index itself, or for PM2.5 and NO2 the concentration in µg/m³. The colours
 *  stay on multiples of the WHO guideline (layerRatio), so the ticks are those multiples × it. */
function tickLabel(layer: HeatLayer, ratio: number): string {
  if (ratio === 0) return "0";
  if (layer === "combined") return fmtNumber(ratio, 1);
  const value = ratio * WHO_GUIDELINE[layer];
  return fmtNumber(value, Number.isInteger(value) ? 0 : 1);
}

/** Map legend: the two route line styles and the heatmap scale with its layer switch
 *  (subbab 3.1, Gambar 3.5). Read top to bottom: what and when (title, data time), which
 *  layer, anything wrong, the scale, the map key, and the explanation folded away last.
 *  Built once; the parts update in place so keyboard focus survives a layer switch. */
export class Legend {
  private heat = h("div", { class: "legend-section", hidden: true });
  private layerButtons = new Map<HeatLayer, HTMLButtonElement>();
  private heatTitle = h("h2", { class: "legend-title" });
  private layerGroup = h("div", { class: "layer-switch", role: "group" });
  private status = h("p", { class: "heat-status", role: "status" });
  private retry = h("button", { type: "button", class: "link-btn", hidden: true });
  private scale = h("div", { class: "heat-scale-wrap" });
  private about = h("button", { type: "button", class: "link-btn legend-about", "aria-haspopup": "dialog", onclick: openIndexInfo });
  /** Loading discs beside the title (and beside the phone toggle's text): they take no room of
   *  their own, so nothing below moves while the heatmap loads. */
  private spinner = h("span", { class: "spinner legend-spinner", "aria-hidden": "true", hidden: true });
  private toggleSpinner = h("span", { class: "spinner legend-spinner", "aria-hidden": "true", hidden: true });
  private toggleText = h("span", {});

  private toggle: HTMLButtonElement;
  private lastHeat: HeatLegendState | null = null;

  onLayer: (layer: HeatLayer) => void = () => {};
  onRetry: () => void = () => {};

  constructor(private root: HTMLElement) {
    for (const layer of ["combined", "pm25", "no2"] as const) {
      const b = h("button", { type: "button", class: "layer-btn", "aria-pressed": "false", onclick: () => this.onLayer(layer) });
      this.layerButtons.set(layer, b);
      this.layerGroup.append(b);
    }
    this.retry.addEventListener("click", () => this.onRetry());
    this.heat.append(
      h("div", { class: "legend-head" }, this.heatTitle, this.spinner),
      this.layerGroup,
      this.status,
      this.retry,
      this.scale,
      this.about,
    );
    // Narrow screens: the legend folds into one button so it does not cover the route.
    this.toggle = h(
      "button",
      { type: "button", class: "legend-toggle", "aria-expanded": "false", onclick: () => this.expand(this.root.classList.contains("is-collapsed")) },
      h("span", { class: "legend-toggle-label" }, this.toggleText, this.toggleSpinner),
    );
    root.append(this.toggle, this.heat);
    this.expand(false);
    this.relabel();
  }

  /** Rewrites every text in the current interface language. */
  relabel(): void {
    // On phones the toggle is the legend's title (the inner one is hidden there, style.css).
    this.toggleText.textContent = tr("Kualitas udara", "Air quality");
    this.heatTitle.textContent = tr("Kualitas udara", "Air quality");
    this.layerGroup.setAttribute("aria-label", tr("Lapisan heatmap", "Heatmap layer"));
    for (const [layer, b] of this.layerButtons) b.textContent = HEAT_LAYER_LABELS[layer];
    this.retry.textContent = tr("Coba lagi", "Try again");
    this.about.textContent = tr("Tentang indeks", "About the index");
    if (this.lastHeat) this.setHeat(this.lastHeat);
  }

  setHeat(s: HeatLegendState): void {
    this.lastHeat = s;
    this.heat.hidden = !s.visible;
    for (const [layer, b] of this.layerButtons) b.setAttribute("aria-pressed", String(layer === s.layer));


    // Loading shows as the spinner beside the title; screen readers still hear it through the
    // status line, which is then visually hidden so the layout stays put.
    const loading = s.status === "loading";
    this.spinner.hidden = !loading;
    this.toggleSpinner.hidden = !loading;
    // The status line speaks only when something is not as expected.
    let text = "";
    let tone = "";
    if (loading) {
      text = s.showingOldData ? tr("Memperbarui heatmap…", "Updating the heatmap…") : tr("Memuat heatmap…", "Loading the heatmap…");
    } else if (s.status === "error") {
      text = s.showingOldData
        ? tr("Gagal memperbarui; yang tampil data lama.", "Could not update; showing old data.")
        : tr("Heatmap tidak dapat dimuat. Rute tidak terpengaruh.", "The heatmap could not be loaded. Routes are not affected.");
      tone = "error";
    } else if (s.status === "ready" && s.stale) {
      text = tr("Data lebih dari 3 jam lalu.", "Data is more than 3 hours old.");
      tone = "warn";
    }
    this.status.textContent = text;
    this.status.className = loading ? "heat-status visually-hidden" : `heat-status${tone ? ` is-${tone}` : ""}`;
    this.retry.hidden = s.status !== "error";

    clear(this.scale);
    // Each stop sits at its value; the top colour carries on through the end block.
    const last = EXPOSURE_STOPS[EXPOSURE_STOPS.length - 1][1];
    const gradient = `linear-gradient(to right, ${EXPOSURE_STOPS.map(([v, c]) => `${c} ${scalePos(v)}%`).join(", ")}, ${last} 100%)`;
    // Class ranges: from the previous boundary to this one; the open-ended last class fills the end block.
    let from = 0;
    const classes = EXPOSURE_CLASSES.map((c) => {
      const start = scalePos(from);
      const end = Number.isFinite(c.max) ? scalePos(c.max) : 100;
      from = c.max;
      return h("span", { style: `left:${start}%;width:${end - start}%` }, c.label);
    });
    // The ticks' unit above the bar: concentrations for PM2.5 and NO2, a unitless index for Gabungan.
    const unit = s.layer === "combined" ? tr("indeks", "index") : "µg/m³";
    this.scale.append(
      h("div", { class: "scale-unit" }, unit),
      h(
        "div",
        {
          class: "scale-bar",
          style: `background:${gradient}`,
          role: "img",
          "aria-label": tr("Skala warna dari sangat rendah (krem) ke sangat tinggi (merah tua)", "Colour scale from very low (cream) to very high (dark red)"),
        },
        // Thin dividers at the class boundaries.
        ...EXPOSURE_CLASSES.filter((c) => Number.isFinite(c.max)).map((c) => h("span", { class: "scale-divider", style: `left:${scalePos(c.max)}%` })),
      ),
      h(
        "div",
        { class: "scale-ticks mono", "aria-hidden": "true" },
        ...TICKS.map((x) => h("span", { style: `left:${scalePos(x)}%` }, tickLabel(s.layer, x))),
      ),
      h("div", { class: "scale-classes" }, ...classes),
    );

    this.refreshVisibility();
  }

  /** Only has a visible effect below 900 px; wider screens always show everything. */
  expand(open: boolean): void {
    this.root.classList.toggle("is-collapsed", !open);
    this.toggle.setAttribute("aria-expanded", String(open));
  }

  private refreshVisibility(): void {
    this.root.hidden = this.heat.hidden;
  }
}
