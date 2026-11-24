import { compareRoutes, type Comparison } from "./compare";
import { clear, h, icon } from "./dom";
import {
  type ConfidenceCategory,
  confidenceCategory,
  exposureUnitLabel,
  fmtCoord,
  fmtDistance,
  fmtExposure,
  fmtMinutes,
  fmtNumber,
  fmtPercent,
  fmtWib,
  fmtDateTimeWib,
  fmtGraphVersionDate,
} from "./format";
import { tr } from "./i18n";
import type { Mode, RouteRequest, RouteResponse, RouteResult } from "./types";

const MODE_LABEL: Record<Mode, () => string> = {
  walk: () => tr("Jalan kaki", "Walking"),
  bike: () => tr("Sepeda", "Cycling"),
};

const CONFIDENCE_LABEL: Record<ConfidenceCategory, () => string> = {
  rendah: () => tr("Rendah", "Low"),
  sedang: () => tr("Sedang", "Medium"),
  tinggi: () => tr("Tinggi", "High"),
};

export function priorityLabel(beta: number): string {
  if (Math.abs(beta - 0.5) < 0.005) return tr("prioritas seimbang", "balanced priority");
  return beta < 0.5 ? tr("condong ke lebih cepat", "leaning faster") : tr("condong ke lebih bersih", "leaning cleaner");
}

export interface ResultViewOptions {
  response: RouteResponse;
  request: RouteRequest;
  /** Names of the points, when known; otherwise the header shows their coordinates. */
  places: { origin: string | null; destination: string | null };
  onBack: () => void;
  /** Starts navigation along one of the routes (TI-SE-13). */
  onNavigate: (role: RouteResult["role"]) => void;
  /** Shown above the routes, e.g. why navigation could not start. */
  notice?: { title: string; text: string } | null;
  /** Streets already found for the routes' "via" lines. */
  names?: Partial<Record<RouteResult["role"], string>>;
  /** The route row shown as selected; the recommended one by default. */
  selected?: RouteResult["role"];
  /** The user chose another route row. */
  onSelect?: (role: RouteResult["role"]) => void;
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
    { class: "visually-hidden", tabindex: "-1", id: "result-title" },
    routeCount === 2 ? tr("2 rute ditemukan", "2 routes found") : tr("Rute ditemukan", "Route found"),
  );

  container.append(
    h(
      "div",
      { class: "result-head" },
      h("button", { type: "button", class: "icon-btn", "aria-label": tr("Kembali ke isian perjalanan", "Back to trip details"), onclick: opts.onBack }, icon("back", 20)),
      h(
        "div",
        { class: "trip-summary" },
        h("div", { class: "trip-line" }, opts.places.origin ?? fmtCoord(request.origin), " → ", opts.places.destination ?? fmtCoord(request.destination)),
        h(
          "div",
          { class: "trip-sub" },
          `${MODE_LABEL[request.mode]()} · ${priorityLabel(request.preference.beta)} (α ${fmtNumber(request.preference.alpha, 2)} · β ${fmtNumber(request.preference.beta, 2)})`,
        ),
      ),
    ),
    heading,
  );

  if (opts.notice) container.append(notice("warn", opts.notice.title, opts.notice.text, "alert"));

  if (metadata.dataStale) {
    container.append(
      notice(
        "warn",
        tr("Data kualitas udara belum diperbarui", "Air-quality data has not been updated"),
        wib
          ? tr(`Estimasi terakhir pukul ${wib}, lebih dari 3 jam lalu. Kondisi sekarang bisa berbeda.`, `Last estimate at ${wib}, more than 3 hours ago. Conditions now may differ.`)
          : tr("Estimasi terakhir lebih dari 3 jam lalu. Kondisi sekarang bisa berbeda.", "The last estimate is more than 3 hours old. Conditions now may differ."),
      ),
    );
  }
  if (metadata.missingEdgeCount > 0) {
    container.append(
      notice(
        "info",
        tr("Sebagian ruas tanpa estimasi", "Some segments have no estimate"),
        tr(
          `${fmtNumber(metadata.missingEdgeCount, 0)} ruas pada area rute tidak punya estimasi polutan; nilainya diisi median wilayah dengan keyakinan 0. Angka paparan bisa kurang akurat.`,
          `${fmtNumber(metadata.missingEdgeCount, 0)} segments in the route area have no pollutant estimate; they are filled with the area median at confidence 0. Exposure figures may be less accurate.`,
        ),
      ),
    );
  }
  // A fallback route carries its "Metode cadangan" chip on the card; no separate notice.
  if (!isFallback && recommended.preferenceApplied === false) {
    container.append(notice(
        "info",
        tr("Prioritas belum diterapkan", "Priority not applied"),
        tr("Server menandai bahwa prioritas waktu-paparan tidak diterapkan pada rute ini.", "The server reports that the time–exposure priority was not applied to this route."),
      ));
  }

  // Identical routes need no comparison box: the single route card says it all.
  if (cmp.kind !== "identical") container.append(comparisonBanner(cmp));
  container.append(routeList(response, request, isFallback, opts));

  const detail = detailSection(response);
  container.append(detail);
  return heading;
}

function notice(kind: "warn" | "info", title: string, text: string, role?: "alert"): HTMLElement {
  return h(
    "div",
    { class: `notice notice-${kind}`, role: role ?? (kind === "warn" ? "status" : undefined) },
    icon(kind === "warn" ? "warn" : "info", 18),
    h("div", {}, h("strong", {}, title), h("p", {}, text)),
  );
}

function comparisonBanner(c: Comparison): HTMLElement {
  const mins = Math.round(c.deltaTimeMin);
  const timeText =
    mins > 0
      ? tr(`dengan tambahan ${fmtNumber(mins, 0)} menit`, `taking ${fmtNumber(mins, 0)} min longer`)
      : mins < 0
        ? tr(`dengan waktu tempuh ${fmtNumber(-mins, 0)} menit lebih singkat`, `taking ${fmtNumber(-mins, 0)} min less`)
        : tr("dengan waktu tempuh yang sama", "with the same travel time");
  const vsFastest = tr("paparan polutan dibanding rute tercepat", "pollutant exposure compared with the fastest route");

  switch (c.kind) {
    case "lower":
      return banner(`−${fmtPercent(c.deltaExposurePct!)}`, `${vsFastest}, ${timeText}`, "good");
    case "higher":
      return banner(`+${fmtPercent(c.deltaExposurePct!)}`, `${vsFastest}, ${timeText}`, "neutral");
    case "same":
      return banner("±0%", tr(`paparan polutan setara rute tercepat, ${timeText}`, `pollutant exposure equal to the fastest route, ${timeText}`), "neutral");
    case "identical": // not shown (see renderResult); kept so the switch stays exhaustive
    case "unknown":
      return banner("—", tr(`selisih paparan tidak dapat dihitung, ${timeText}`, `the exposure difference cannot be calculated, ${timeText}`), "neutral");
    case "none":
      return banner("—", tr("Rute pembanding tidak tersedia, jadi selisih tidak dapat dihitung.", "No comparison route is available, so the difference cannot be calculated."), "neutral");
  }
}

function banner(big: string, text: string, tone: "good" | "neutral"): HTMLElement {
  return h("div", { class: `delta-banner delta-${tone}` }, h("span", { class: "delta-big mono" }, big), h("span", { class: "delta-text" }, text));
}

type Role = RouteResult["role"];

/** Routes as a list, one row each, like a directions panel: mode icon, "via" line, time and
 *  distance on the right. The selected row is marked and holds its details and the navigation
 *  button; choosing another row only moves the selection (and the map emphasis). */
function routeList(resp: RouteResponse, request: RouteRequest, isFallback: boolean, opts: ResultViewOptions): HTMLElement {
  const identical = compareRoutes(resp).kind === "identical";
  const routes: { r: RouteResult; title: string; swatch: "solid" | "dashed" }[] = [
    {
      r: resp.recommended,
      title: isFallback ? tr("Rute rekomendasi", "Recommended route") : tr("Rute rendah paparan", "Low-exposure route"),
      swatch: "solid",
    },
  ];
  if (resp.comparison && !identical) routes.push({ r: resp.comparison, title: tr("Rute tercepat", "Fastest route"), swatch: "dashed" });

  const list = h("ul", { class: "route-list", "aria-label": tr("Pilihan rute", "Route options") });
  const rows = new Map<Role, { li: HTMLLIElement; pick: HTMLButtonElement }>();
  const select = (role: Role, notify: boolean) => {
    for (const [r, { li, pick }] of rows) {
      li.classList.toggle("is-selected", r === role);
      pick.setAttribute("aria-pressed", String(r === role));
    }
    if (notify) opts.onSelect?.(role);
  };

  const unit = exposureUnitLabel(resp.exposureUnit);
  const exposureLabel = resp.exposureUnit === "index_min" ? tr("Paparan", "Exposure") : tr("Paparan PM2.5", "PM2.5 exposure");
  for (const { r, title, swatch } of routes) {
    const via = opts.names?.[r.role];
    const pick = h(
      "button",
      { type: "button", class: "route-pick", "aria-pressed": "false", onclick: () => select(r.role, true) },
      h("span", { class: "route-mode", "aria-hidden": "true" }, icon(request.mode === "bike" ? "bike" : "walk", 22)),
      h(
        "span",
        { class: "route-main" },
        h("span", { class: "route-via", id: `route-via-${r.role}` }, via ? tr(`via ${via}`, `via ${via}`) : title),
        h(
          "span",
          { class: "route-meta" },
          h("span", { class: `swatch swatch-${swatch}`, "aria-hidden": "true" }),
          via ? `${title} · ` : "",
          `${exposureLabel} ${fmtExposure(r.exposure)} ${unit}`,
        ),
      ),
      h("span", { class: "route-time" }, h("strong", { class: "mono" }, fmtMinutes(r.travelTimeMin)), h("span", { class: "mono" }, fmtDistance(r.distanceM))),
    );
    const extra = h(
      "div",
      { class: "route-extra" },
      h(
        "button",
        {
          type: "button",
          class: "primary nav-start",
          "aria-label": tr(`Mulai navigasi: ${title}`, `Start navigation: ${title}`),
          onclick: () => opts.onNavigate(r.role),
        },
        icon("navigate", 18),
        ` ${tr("Mulai navigasi", "Start navigation")}`,
      ),
    );
    const li = h("li", { class: "route-row" }, pick, extra);
    rows.set(r.role, { li, pick });
    list.append(li);
  }
  select(opts.selected && rows.has(opts.selected) ? opts.selected : "recommended", false);
  return list;
}

/** Fills in the "via …" line of a route row once its street is known. */
export function setRouteVia(container: HTMLElement, role: Role, street: string): void {
  const el = container.querySelector<HTMLElement>(`#route-via-${role}`);
  if (!el) return;
  const title = el.textContent ?? "";
  el.textContent = tr(`via ${street}`, `via ${street}`);
  const meta = el.nextElementSibling;
  if (meta && !title.startsWith("via ")) meta.insertBefore(document.createTextNode(`${title} · `), meta.childNodes[1] ?? null);
}

export function confidenceBadge(score: number | null): HTMLElement {
  const cat = confidenceCategory(score);
  if (!cat) return h("span", { class: "chip chip-none" }, icon("shield", 13), tr("Keyakinan tidak tersedia", "Confidence unavailable"));
  return h(
    "span",
    { class: `chip chip-${cat}`, title: tr("Tingkat keyakinan estimasi model", "Confidence level of the model estimate") },
    icon("shield", 13),
    tr(`Keyakinan ${CONFIDENCE_LABEL[cat]()} · `, `${CONFIDENCE_LABEL[cat]()} confidence · `),
    h("span", { class: "mono" }, fmtNumber(score!, 2)),
  );
}

function detailSection(resp: RouteResponse): HTMLElement {
  const m = resp.metadata;
  const rows: [string, string][] = [];
  const airQuality = fmtDateTimeWib(m.timeWindowStart);
  if (airQuality) rows.push([tr("Data kualitas udara", "Air-quality data"), airQuality]);
  if (m.graphVersion) {
    rows.push([tr("Versi data jalan", "Road data version"), fmtGraphVersionDate(m.graphVersion) ?? m.graphVersion]);
  }
  const year = new Date().getFullYear();

  return h(
    "details",
    { class: "details" },
    h("summary", {}, tr("Sumber data dan versi", "Data sources and versions")),
    h("dl", { class: "detail-list" }, ...rows.flatMap(([k, v]) => [h("dt", {}, k), h("dd", {}, v)])),
    h(
      "p",
      { class: "detail-copyright" },
      `© ${year} SIPPolusi · ${tr("Data jalan", "Road data")} © `,
      h("a", { href: "https://www.openstreetmap.org/copyright", target: "_blank", rel: "noopener noreferrer" }, tr("kontributor OpenStreetMap", "OpenStreetMap contributors")),
    ),
  );
}
