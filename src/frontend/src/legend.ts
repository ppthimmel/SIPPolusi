import { WHO_GUIDELINE } from "./config";
import { append, clear, h } from "./dom";
import { EXPOSURE_CLASSES, EXPOSURE_STOPS, HEAT_LAYER_LABELS } from "./exposure";
import type { HeatLayer } from "./types";

export type HeatStatus = "idle" | "loading" | "ready" | "error";

export interface HeatLegendState {
  visible: boolean;
  layer: HeatLayer;
  status: HeatStatus;
  /** "12.00 WIB" of the data currently drawn, when known. */
  timeLabel: string | null;
  /** The drawn data is older than 3 hours. */
  stale: boolean;
  /** True when something is drawn although the latest request failed. */
  showingOldData: boolean;
}

const TICKS = ["0", "0,5", "1,0", "2,0", "3,0"];
const LAYER_TITLE: Record<HeatLayer, { title: string; unit: string }> = {
  combined: { title: "Indeks paparan gabungan", unit: "1,0 = setara pedoman WHO" },
  pm25: { title: "PM2.5", unit: "µg/m³ · warna dari rasio ke pedoman 15 µg/m³" },
  no2: { title: "NO2", unit: "µg/m³ · warna dari rasio ke pedoman 25 µg/m³" },
};

/** Map legend: the two route line styles and the heatmap scale with its layer
 *  switch (subbab 3.1, Gambar 3.5). Built once; the parts update in place so
 *  keyboard focus survives a layer switch. */
export class Legend {
  private routes = h("div", { class: "legend-section", hidden: true });
  private heat = h("div", { class: "legend-section", hidden: true });
  private layerButtons = new Map<HeatLayer, HTMLButtonElement>();
  private status = h("p", { class: "heat-status", role: "status" });
  private retry = h("button", { type: "button", class: "link-btn", hidden: true }, "Coba lagi");
  private scale = h("div", { class: "heat-scale-wrap" });

  private toggle: HTMLButtonElement;

  onLayer: (layer: HeatLayer) => void = () => {};
  onRetry: () => void = () => {};

  constructor(private root: HTMLElement) {
    const group = h("div", { class: "layer-switch", role: "group", "aria-label": "Lapisan heatmap" });
    for (const layer of ["combined", "pm25", "no2"] as const) {
      const b = h("button", { type: "button", class: "layer-btn", "aria-pressed": "false", onclick: () => this.onLayer(layer) }, HEAT_LAYER_LABELS[layer]);
      this.layerButtons.set(layer, b);
      group.append(b);
    }
    this.retry.addEventListener("click", () => this.onRetry());
    this.heat.append(
      h("h2", { class: "legend-title" }, "Lapisan heatmap"),
      group,
      this.status,
      this.retry,
      this.scale,
      h(
        "p",
        { class: "legend-note" },
        `Indeks = rata-rata PM2.5 dan NO2 setelah masing-masing dibagi pedoman WHO (${WHO_GUIDELINE.pm25} dan ${WHO_GUIDELINE.no2} µg/m³). Nilai 1,0 berarti tepat pada pedoman.`,
      ),
      h("div", { class: "legend-row" }, h("span", { class: "hatch-swatch", "aria-hidden": "true" }), "Area bergaris: keyakinan estimasi rendah."),
      h("div", { class: "legend-row" }, h("span", { class: "nodata-swatch", "aria-hidden": "true" }), "Abu-abu: tidak ada estimasi."),
    );
    // Narrow screens: the legend folds into one button so it does not cover the route.
    this.toggle = h("button", { type: "button", class: "legend-toggle", "aria-expanded": "false", onclick: () => this.expand(this.root.classList.contains("is-collapsed")) }, "Legenda peta");
    root.append(this.toggle, this.routes, this.heat);
    this.expand(false);
    this.setRoutes(null);
  }

  /** `fallback` relabels the solid line, since a fallback route is the fastest one. */
  setRoutes(info: { fallback: boolean; hasComparison: boolean } | null): void {
    clear(this.routes);
    this.routes.hidden = !info;
    if (info) {
      append(this.routes, [
        h("h2", { class: "legend-title" }, "Rute"),
        h("div", { class: "legend-row" }, h("span", { class: "swatch swatch-solid", "aria-hidden": "true" }), info.fallback ? "Rute rekomendasi (cadangan)" : "Rute rendah paparan"),
        info.hasComparison && h("div", { class: "legend-row" }, h("span", { class: "swatch swatch-dashed", "aria-hidden": "true" }), "Rute tercepat"),
      ]);
    }
    this.refreshVisibility();
  }

  setHeat(s: HeatLegendState): void {
    this.heat.hidden = !s.visible;
    for (const [layer, b] of this.layerButtons) b.setAttribute("aria-pressed", String(layer === s.layer));

    const when = s.timeLabel ? `Data kualitas udara ${s.timeLabel}` : null;
    let text = "";
    let tone = "";
    if (s.status === "loading") {
      text = s.showingOldData ? `Memperbarui heatmap… Sementara menampilkan data ${s.timeLabel ?? "sebelumnya"}.` : "Memuat heatmap…";
    } else if (s.status === "error") {
      text = s.showingOldData
        ? `Gagal memperbarui heatmap. Yang tampil adalah data lama${s.timeLabel ? ` (${s.timeLabel})` : ""}, bukan kondisi terbaru.`
        : "Heatmap tidak dapat dimuat. Rute tidak terpengaruh.";
      tone = "error";
    } else if (s.status === "ready") {
      text = when ?? "Heatmap diperbarui.";
      if (s.stale) {
        text += " · belum diperbarui (lebih dari 3 jam)";
        tone = "warn";
      }
    }
    this.status.textContent = text;
    this.status.className = `heat-status${tone ? ` is-${tone}` : ""}`;
    this.retry.hidden = s.status !== "error";

    const t = LAYER_TITLE[s.layer];
    clear(this.scale);
    const gradient = `linear-gradient(to right, ${EXPOSURE_STOPS.map(([, c], i) => `${c} ${(i / (EXPOSURE_STOPS.length - 1)) * 100}%`).join(", ")})`;
    this.scale.append(
      h("div", { class: "scale-head" }, h("span", { class: "scale-title" }, t.title), h("span", { class: "scale-unit" }, t.unit)),
      h("div", { class: "scale-bar", style: `background:${gradient}`, role: "img", "aria-label": "Skala warna dari sangat rendah (krem) ke sangat tinggi (merah tua)" }),
      h("div", { class: "scale-ticks mono", "aria-hidden": "true" }, ...TICKS.map((x) => h("span", {}, x))),
      h("div", { class: "scale-classes" }, ...EXPOSURE_CLASSES.map((c) => h("span", {}, c.label))),
    );
    this.refreshVisibility();
  }

  /** Only has a visible effect below 900 px; wider screens always show everything. */
  expand(open: boolean): void {
    this.root.classList.toggle("is-collapsed", !open);
    this.toggle.setAttribute("aria-expanded", String(open));
  }

  private refreshVisibility(): void {
    this.root.hidden = this.routes.hidden && this.heat.hidden;
  }
}
