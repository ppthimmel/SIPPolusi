// "Tentang indeks": a popup that explains the heatmap in plain words. It leads with what people
// need on the go (what each colour means and what to do), then what PM2.5 and NO2 are, how the
// Combined layer puts them together (with a worked example), and the sources.
// Class names, colours and WHO values come from the constants the map uses, so the text
// cannot drift from the legend.
import { WHO_GUIDELINE } from "./config";
import { h, icon } from "./dom";
import { EXPOSURE_CLASSES, EXPOSURE_STOPS, exposureClass } from "./exposure";
import { fmtNumber } from "./format";
import { tr } from "./i18n";

const WHO_URL = "https://www.who.int/publications/i/item/9789240034228";

let dialog: HTMLDialogElement | null = null;

/** Opens the popup, built anew so it follows the interface language. */
export function openIndexInfo(): void {
  if (!dialog) {
    const d = h("dialog", { class: "info-dialog", "aria-labelledby": "index-info-title" });
    // A click on the dimmed backdrop (outside the box) closes it; so does Escape (the browser's
    // "cancel"), which is held back so the closing animation can play.
    d.addEventListener("click", (e) => {
      if (e.target === d) closeAnimated(d);
    });
    d.addEventListener("cancel", (e) => {
      e.preventDefault();
      closeAnimated(d);
    });
    document.body.append(d);
    dialog = d;
  }
  const d = dialog;
  d.classList.remove("is-closing");
  d.replaceChildren(...content(() => closeAnimated(d)));
  d.showModal();
  d.querySelector(".info-body")?.scrollTo(0, 0);
}

/** What each colour class means and what to do, in the order of EXPOSURE_CLASSES. */
const CLASS_TEXT: ReadonlyArray<{ meaning: () => string; advice: () => string }> = [
  { meaning: () => tr("Udara bersih", "Clean air"), advice: () => tr("Aman untuk semua orang.", "Fine for everyone.") },
  {
    meaning: () => tr("Masih di bawah batas sehat WHO", "Still below the WHO health limit"),
    advice: () => tr("Aman untuk kebanyakan orang.", "Fine for most people."),
  },
  {
    meaning: () => tr("Melewati batas WHO, sampai 2×", "Above the WHO limit, up to 2×"),
    advice: () => tr("Kelompok rentan sebaiknya tidak berlama-lama.", "Sensitive groups should not linger."),
  },
  {
    meaning: () => tr("2–3× batas WHO", "2–3× the WHO limit"),
    advice: () => tr("Pilih rute yang lebih bersih bila bisa.", "Take a cleaner route if you can."),
  },
  {
    meaning: () => tr("Lebih dari 3× batas WHO", "More than 3× the WHO limit"),
    advice: () => tr("Hindari bila memungkinkan, terutama kelompok rentan.", "Avoid if you can, especially sensitive groups."),
  },
];

function content(close: () => void): HTMLElement[] {
  const link = (href: string, text: string) => h("a", { href, target: "_blank", rel: "noopener noreferrer" }, text);

  // One row per colour: swatch, name and index range, then meaning and what to do.
  let from = 0;
  const classes = EXPOSURE_CLASSES.map((c, i) => {
    const to = c.max;
    const range = i === 0 ? `< ${fmtNumber(to, 1)}` : Number.isFinite(to) ? `${fmtNumber(from, 1)}–${fmtNumber(to, 1)}` : `≥ ${fmtNumber(from, 1)}`;
    const swatch = classColor(Number.isFinite(to) ? (from + to) / 2 : from + 0.5);
    from = to;
    return h(
      "li",
      { class: "info-class" },
      h("span", { class: "info-swatch", style: `background:${swatch}`, "aria-hidden": "true" }),
      h(
        "span",
        { class: "info-class-text" },
        h("span", { class: "info-class-name" }, c.label, " ", h("span", { class: "info-class-range mono" }, range)),
        h("span", {}, `${CLASS_TEXT[i].meaning()}. ${CLASS_TEXT[i].advice()}`),
      ),
    );
  });

  const card = (title: string, short: string, from: string) =>
    h("div", { class: "info-card" }, h("h4", {}, title), h("p", {}, short), h("p", { class: "info-card-from" }, from));

  return [
    h(
      "header",
      { class: "info-head" },
      h("h2", { id: "index-info-title" }, tr("Tentang indeks", "About the index")),
      h("button", { type: "button", class: "icon-btn", "aria-label": tr("Tutup", "Close"), onclick: close }, icon("close", 20)),
    ),
    h(
      "div",
      { class: "info-body" },
      h("h3", {}, tr("Arti warna", "What the colours mean")),
      h("ul", { class: "info-classes" }, ...classes),

      h("h3", {}, tr("Apa yang diukur?", "What is measured?")),
      h(
        "div",
        { class: "info-cards" },
        card(
          "PM2.5",
          tr(
            "Debu sangat halus yang bisa masuk jauh ke paru-paru dan darah.",
            "Very fine dust that can get deep into the lungs and blood.",
          ),
          tr("Dari asap kendaraan, industri, dan pembakaran.", "From vehicle exhaust, industry and burning."),
        ),
        card(
          "NO2",
          tr("Gas yang mengiritasi saluran napas dan bisa memicu asma.", "A gas that irritates the airways and can trigger asthma."),
          tr("Terutama dari kendaraan, tertinggi di jalan ramai.", "Mostly from traffic, highest on busy roads."),
        ),
      ),
      h("h3", {}, tr("Bagaimana Gabungan dihitung?", "How is Combined worked out?")),
      combinedChart(),
      combinedSteps(),

      h(
        "p",
        { class: "info-sources" },
        tr("Sumber: ", "Sources: "),
        link(WHO_URL, "WHO Air Quality Guidelines 2021"),
        " · ",
        tr("mengandung data Copernicus Sentinel-5P yang dimodifikasi", "contains modified Copernicus Sentinel-5P data"),
        " · NASA GEOS-CF · Landsat 8/9 (USGS/NASA) · NASA VIIRS Black Marble · ",
        tr("cuaca oleh ", "weather by "),
        link("https://open-meteo.com", "Open-Meteo.com"),
        " · ",
        tr("jalan © ", "roads © "),
        link("https://www.openstreetmap.org/copyright", tr("kontributor OpenStreetMap", "OpenStreetMap contributors")),
      ),
    ),
  ];
}

/** Plays the closing animation (style.css, .is-closing), then closes. */
function closeAnimated(d: HTMLDialogElement): void {
  if (!d.open || d.classList.contains("is-closing")) return;
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (reduce) return d.close();
  d.classList.add("is-closing");
  window.setTimeout(() => {
    d.classList.remove("is-closing");
    d.close();
  }, 150);
}

/** Three steps from two pollutants to one number, with a worked example. The example values are
 *  multiples of the WHO guideline, so they stay right if the guideline constants change. */
/** The worked example used by the chart and the steps: multiples of the WHO guideline. */
const EXAMPLE = { pm25: 2, no2: 1 } as const;
const EXAMPLE_INDEX = (EXAMPLE.pm25 + EXAMPLE.no2) / 2;
/** The chart's scale runs from 0 to this many times the guideline. */
const CHART_MAX = 3;

/** The worked example as a picture: PM2.5 and NO2 as bars against the WHO limit (a dashed line at
 *  1×), and their average as the Combined bar, each in the map's colour for its value. */
function combinedChart(): HTMLElement {
  const pct = (times: number) => `${(Math.min(times, CHART_MAX) / CHART_MAX) * 100}%`;
  const n = (v: number) => fmtNumber(v, Number.isInteger(v) ? 0 : 1);
  const bar = (label: string, times: number, value: string, combined = false) =>
    h(
      "div",
      { class: `info-bar${combined ? " is-combined" : ""}` },
      h("span", { class: "info-bar-label" }, label),
      h(
        "span",
        { class: "info-bar-track" },
        h("span", { class: "info-bar-fill", style: `width:${pct(times)};background:${classColor(times)}` }),
      ),
      h("span", { class: "info-bar-value mono" }, value),
    );
  return h(
    "figure",
    { class: "info-chart" },
    h(
      "div",
      { class: "info-bars", style: `--who-ratio:${1 / CHART_MAX}` },
      bar("PM2.5", EXAMPLE.pm25, `${n(EXAMPLE.pm25)}×`),
      bar("NO2", EXAMPLE.no2, `${n(EXAMPLE.no2)}×`),
      h("div", { class: "info-merge", "aria-hidden": "true" }, tr("rata-rata", "average"), " ↓"),
      bar(tr("Gabungan", "Combined"), EXAMPLE_INDEX, `${fmtNumber(EXAMPLE_INDEX, 1)}×`, true),
      h("span", { class: "info-who", "aria-hidden": "true" }, tr("batas WHO", "WHO limit")),
    ),
    h(
      "figcaption",
      { class: "visually-hidden" },
      tr(
        `Contoh: PM2.5 ${n(EXAMPLE.pm25)} kali batas WHO dan NO2 ${n(EXAMPLE.no2)} kali, rata-ratanya ${fmtNumber(EXAMPLE_INDEX, 1)} (${exposureClass(EXAMPLE_INDEX)}).`,
        `Example: PM2.5 at ${n(EXAMPLE.pm25)} times the WHO limit and NO2 at ${n(EXAMPLE.no2)} times; their average is ${fmtNumber(EXAMPLE_INDEX, 1)} (${exposureClass(EXAMPLE_INDEX)}).`,
      ),
    ),
  );
}

/** How Combined is made: one sentence, then the formula as stacked fractions with the WHO limits. */
function combinedSteps(): HTMLElement {
  const frac = (top: (HTMLElement | string)[], bottom: string) =>
    h("span", { class: "frac" }, h("span", { class: "frac-top" }, ...top), h("span", { class: "frac-bottom" }, bottom));
  const combined = tr("Gabungan", "Combined");
  return h(
    "div",
    {},
    h(
      "p",
      { class: "info-explain" },
      tr(
        "Setiap polutan dibandingkan dengan batas WHO-nya, lalu keduanya dirata-ratakan dengan bobot yang sama:",
        "Each pollutant is compared with its WHO limit, and the two are then averaged, counting equally:",
      ),
    ),
    h(
      "div",
      {
        class: "info-formula",
        role: "math",
        "aria-label": tr(
          `${combined} sama dengan PM2.5 dibagi ${WHO_GUIDELINE.pm25} ditambah NO2 dibagi ${WHO_GUIDELINE.no2}, semuanya dibagi 2`,
          `${combined} equals PM2.5 divided by ${WHO_GUIDELINE.pm25} plus NO2 divided by ${WHO_GUIDELINE.no2}, all divided by 2`,
        ),
      },
      h("span", { class: "formula-name" }, combined),
      " = ",
      frac([frac(["PM2.5"], String(WHO_GUIDELINE.pm25)), h("span", { class: "formula-op" }, "+"), frac(["NO2"], String(WHO_GUIDELINE.no2))], "2"),
    ),
    h(
      "p",
      { class: "info-formula-note" },
      tr(
        `PM2.5 dan NO2 dalam µg/m³. ${WHO_GUIDELINE.pm25} adalah batas WHO 24 jam untuk PM2.5, dan ${WHO_GUIDELINE.no2} untuk NO2.`,
        `PM2.5 and NO2 in µg/m³. ${WHO_GUIDELINE.pm25} is the WHO 24-hour limit for PM2.5, and ${WHO_GUIDELINE.no2} the one for NO2.`,
      ),
    ),
  );
}

/** The map's colour at index value `v` (linear between the stops, like the heatmap). */
function classColor(v: number): string {
  const stops = EXPOSURE_STOPS;
  if (v <= stops[0][0]) return stops[0][1];
  for (let i = 1; i < stops.length; i++) {
    const [v1, c1] = stops[i];
    const [v0, c0] = stops[i - 1];
    if (v <= v1) return mix(c0, c1, (v - v0) / (v1 - v0));
  }
  return stops[stops.length - 1][1];
}

function mix(a: string, b: string, t: number): string {
  const ch = (hex: string, i: number) => parseInt(hex.slice(1 + i * 2, 3 + i * 2), 16);
  return `rgb(${[0, 1, 2].map((i) => Math.round(ch(a, i) + (ch(b, i) - ch(a, i)) * t)).join(", ")})`;
}
