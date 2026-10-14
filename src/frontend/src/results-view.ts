import { compareRoutes, type Comparison } from "./compare";
import { clear, h, icon } from "./dom";
import {
  confidenceCategory,
  exposureUnitLabel,
  fmtCoord,
  fmtDistance,
  fmtExposure,
  fmtMinutes,
  fmtNumber,
  fmtPercent,
  fmtWib,
} from "./format";
import type { FallbackReason, Mode, RouteRequest, RouteResponse, RouteResult } from "./types";

const MODE_LABEL: Record<Mode, string> = { walk: "Jalan kaki", bike: "Sepeda" };

const FALLBACK_TEXT: Record<FallbackReason, string> = {
  policy_timeout: "Model utama tidak merespons tepat waktu",
  policy_error: "Model utama mengalami galat",
  invalid_route: "Model utama menghasilkan rute yang tidak valid",
};

export function priorityLabel(beta: number): string {
  if (Math.abs(beta - 0.5) < 0.005) return "prioritas seimbang";
  return beta < 0.5 ? "condong ke lebih cepat" : "condong ke lebih bersih";
}

export interface ResultViewOptions {
  response: RouteResponse;
  request: RouteRequest;
  onBack: () => void;
}

/** Rebuilds the result panel (Gambar 3.1b, 3.4). Returns the heading so the
 *  caller can move focus to it. */
export function renderResult(container: HTMLElement, opts: ResultViewOptions): HTMLElement {
  const { response, request } = opts;
  const { recommended, comparison, metadata } = response;
  const isFallback = recommended.routeSource === "baseline";
  const cmp = compareRoutes(response);
  const wib = fmtWib(metadata.timeWindowStart);
  const routeCount = comparison && cmp.kind !== "identical" ? 2 : 1;

  clear(container);

  const heading = h(
    "h2",
    { class: "title result-title", tabindex: "-1", id: "result-title" },
    routeCount === 2 ? "2 rute ditemukan" : "Rute ditemukan",
  );

  container.append(
    h(
      "div",
      { class: "result-head" },
      h("button", { type: "button", class: "icon-btn", "aria-label": "Kembali ke isian perjalanan", onclick: opts.onBack }, icon("back", 20)),
      h(
        "div",
        { class: "trip-summary" },
        h("div", { class: "trip-line" }, fmtCoord(request.origin), " → ", fmtCoord(request.destination)),
        h(
          "div",
          { class: "trip-sub" },
          `${MODE_LABEL[request.mode]} · ${priorityLabel(request.preference.beta)} (α ${fmtNumber(request.preference.alpha, 2)} · β ${fmtNumber(request.preference.beta, 2)})`,
        ),
      ),
    ),
    h("div", { class: "title-row" }, heading, wib && h("span", { class: "time-chip" }, icon("clock", 14), `Data kualitas udara ${wib}`)),
  );

  if (metadata.dataStale) {
    container.append(
      notice(
        "warn",
        "Data kualitas udara belum diperbarui",
        wib
          ? `Estimasi terakhir pukul ${wib}, lebih dari 3 jam lalu. Kondisi sekarang bisa berbeda.`
          : "Estimasi terakhir lebih dari 3 jam lalu. Kondisi sekarang bisa berbeda.",
      ),
    );
  }
  if (metadata.missingEdgeCount > 0) {
    container.append(
      notice(
        "info",
        "Sebagian ruas tanpa estimasi",
        `${fmtNumber(metadata.missingEdgeCount, 0)} ruas pada area rute tidak punya estimasi polutan; nilainya diisi median wilayah dengan keyakinan 0. Angka paparan bisa kurang akurat.`,
      ),
    );
  }
  if (isFallback) {
    const why = metadata.fallbackReason ? FALLBACK_TEXT[metadata.fallbackReason] : "Model utama tidak tersedia";
    container.append(
      notice(
        "info",
        "Memakai metode cadangan",
        `${why}, jadi yang ditampilkan adalah rute tercepat. Prioritas waktu-paparan yang Anda atur belum diterapkan pada rute ini.`,
      ),
    );
  } else if (recommended.preferenceApplied === false) {
    container.append(notice("info", "Prioritas belum diterapkan", "Server menandai bahwa prioritas waktu-paparan tidak diterapkan pada rute ini."));
  }

  container.append(comparisonBanner(cmp), ...routeCards(response, isFallback));

  const detail = detailSection(response);
  container.append(detail);
  return heading;
}

function notice(kind: "warn" | "info", title: string, text: string): HTMLElement {
  return h(
    "div",
    { class: `notice notice-${kind}`, role: kind === "warn" ? "status" : undefined },
    icon(kind === "warn" ? "warn" : "info", 18),
    h("div", {}, h("strong", {}, title), h("p", {}, text)),
  );
}

function comparisonBanner(c: Comparison): HTMLElement {
  const mins = Math.round(c.deltaTimeMin);
  const timeText =
    mins > 0
      ? `dengan tambahan ${fmtNumber(mins, 0)} menit`
      : mins < 0
        ? `dengan waktu tempuh ${fmtNumber(-mins, 0)} menit lebih singkat`
        : "dengan waktu tempuh yang sama";

  switch (c.kind) {
    case "lower":
      return banner(`−${fmtPercent(c.deltaExposurePct!)}`, `paparan polutan dibanding rute tercepat, ${timeText}`, "good");
    case "higher":
      return banner(`+${fmtPercent(c.deltaExposurePct!)}`, `paparan polutan dibanding rute tercepat, ${timeText}`, "neutral");
    case "same":
      return banner("±0%", `paparan polutan setara rute tercepat, ${timeText}`, "neutral");
    case "identical":
      return banner("Sama", "Rute rekomendasi sama dengan rute tercepat; tidak ada selisih waktu maupun paparan.", "neutral");
    case "unknown":
      return banner("—", `selisih paparan tidak dapat dihitung, ${timeText}`, "neutral");
    case "none":
      return banner("—", "Rute pembanding tidak tersedia, jadi selisih tidak dapat dihitung.", "neutral");
  }
}

function banner(big: string, text: string, tone: "good" | "neutral"): HTMLElement {
  return h("div", { class: `delta-banner delta-${tone}` }, h("span", { class: "delta-big mono" }, big), h("span", { class: "delta-text" }, text));
}

function routeCards(resp: RouteResponse, isFallback: boolean): HTMLElement[] {
  const cards: HTMLElement[] = [];
  const identical = compareRoutes(resp).kind === "identical";
  cards.push(
    routeCard(resp.recommended, resp, {
      title: isFallback ? "Rute rekomendasi" : "Rute rendah paparan",
      sub: "Rekomendasi",
      swatch: "solid",
      fallbackChip: isFallback,
      recommended: true,
    }),
  );
  if (resp.comparison && !identical) {
    cards.push(routeCard(resp.comparison, resp, { title: "Rute tercepat", sub: "Pembanding", swatch: "dashed", fallbackChip: false, recommended: false }));
  }
  return cards;
}

function routeCard(
  r: RouteResult,
  resp: RouteResponse,
  o: { title: string; sub: string; swatch: "solid" | "dashed"; fallbackChip: boolean; recommended: boolean },
): HTMLElement {
  const unit = exposureUnitLabel(resp.exposureUnit);
  const exposureLabel = resp.exposureUnit === "index_min" ? "Paparan gabungan" : "Paparan PM2.5";
  return h(
    "article",
    { class: `route-card${o.recommended ? " route-card-rec" : ""}`, "aria-label": o.title },
    h("h3", { class: "route-title" }, h("span", { class: `swatch swatch-${o.swatch}`, "aria-hidden": "true" }), o.title),
    h("div", { class: "route-badges" }, h("span", { class: "route-sub" }, o.sub), confidenceBadge(r.confidence), o.fallbackChip && h("span", { class: "chip chip-fallback" }, icon("info", 13), "Metode cadangan")),
    h(
      "dl",
      { class: "metrics" },
      metric("Waktu tempuh", fmtMinutes(r.travelTimeMin)),
      metric("Jarak", fmtDistance(r.distanceM)),
      metric(exposureLabel, fmtExposure(r.exposure), unit),
    ),
  );
}

function metric(label: string, value: string, unit?: string): HTMLElement {
  return h("div", { class: "metric" }, h("dt", {}, label), h("dd", { class: "mono" }, value), unit && h("dd", { class: "unit" }, unit));
}

export function confidenceBadge(score: number | null): HTMLElement {
  const cat = confidenceCategory(score);
  if (!cat) return h("span", { class: "chip chip-none" }, icon("shield", 13), "Keyakinan tidak tersedia");
  return h(
    "span",
    { class: `chip chip-${cat}`, title: "Tingkat keyakinan estimasi model" },
    icon("shield", 13),
    `Keyakinan ${cat[0].toUpperCase()}${cat.slice(1)} · `,
    h("span", { class: "mono" }, fmtNumber(score!, 2)),
  );
}

function detailSection(resp: RouteResponse): HTMLElement {
  const m = resp.metadata;
  const rows: [string, string][] = [];
  const wib = fmtWib(m.timeWindowStart);
  if (wib) rows.push(["Data kualitas udara", `${wib}${m.timeWindowStart ? ` (${m.timeWindowStart})` : ""}`]);
  rows.push([
    "Sumber rute",
    resp.recommended.routeSource === "baseline" ? "Rute tercepat (metode cadangan)" : "Model rute multi-tujuan (waktu dan paparan)",
  ]);
  rows.push(["Sumber estimasi", "Model downscaling spasial dari data satelit, meteorologi, dan sensor darat"]);
  if (m.downscalingVersion) rows.push(["Versi model downscaling", m.downscalingVersion]);
  if (m.routePolicyVersion) rows.push(["Versi model rute", m.routePolicyVersion]);
  rows.push(["Ruas tanpa estimasi", fmtNumber(m.missingEdgeCount, 0)]);
  if (m.requestId) rows.push(["ID permintaan", m.requestId]);

  return h(
    "details",
    { class: "details" },
    h("summary", {}, "Sumber data dan versi"),
    h("dl", { class: "detail-list" }, ...rows.flatMap(([k, v]) => [h("dt", {}, k), h("dd", {}, v)])),
    m.disclaimer && h("p", { class: "detail-disclaimer" }, m.disclaimer),
  );
}
