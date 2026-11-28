import { STUDY_AREA } from "./config";
import { clear, h } from "./dom";
import type { PlaceSearchView } from "./place-search";
import type { Place } from "./types";
import { tr } from "./i18n";

const STATUS: Partial<Record<PlaceSearchView["kind"], () => string>> = {
  loading: () => tr("Mencari tempat…", "Searching for places…"),
  empty: () =>
    tr(
      "Tempat tidak ditemukan. Coba nama lain atau pilih titik di peta.",
      "No place found. Try another name or pick a point on the map.",
    ),
  outside: () =>
    tr(
      "Tempat itu berada di luar wilayah studi (Jakarta). Cari tempat di dalam Jakarta.",
      "That place is outside the study area (Jakarta). Search for a place inside Jakarta.",
    ),
};

/** True when a place lies inside the study area, the only area routes can be found in. */
export function placeInStudyArea(p: Pick<Place, "lat" | "lon">): boolean {
  const a = STUDY_AREA;
  return p.lat >= a.minLat && p.lat <= a.maxLat && p.lon >= a.minLon && p.lon <= a.maxLon;
}

/** The suggestion list of one field, as an ARIA combobox: the input keeps focus, arrows move
 *  through the options, Enter takes the highlighted one (else the first), Escape closes.
 *  Places outside the study area stay in the list, greyed out and marked, but cannot be chosen:
 *  arrows skip them and a click or Enter does nothing. */
export class PlaceCombobox {
  private places: Place[] = [];
  private active = -1;

  constructor(
    private readonly input: HTMLInputElement,
    private readonly menu: HTMLElement,
    private readonly list: HTMLElement,
    private readonly status: HTMLElement,
    private readonly onChoose: (place: Place) => void,
    private readonly onClose: () => void,
  ) {
    // Choosing with the mouse must not blur the input, which would close the list first.
    menu.addEventListener("mousedown", (e) => e.preventDefault());
    input.addEventListener("keydown", (e) => this.onKey(e));
  }

  render(view: PlaceSearchView): void {
    this.places = view.kind === "results" ? view.places : [];
    this.active = -1;
    this.status.textContent = view.kind === "error" ? view.message : (STATUS[view.kind]?.() ?? "");
    this.status.hidden = !this.status.textContent;
    clear(this.list);
    this.list.hidden = !this.places.length;
    this.places.forEach((place, i) => {
      const inside = placeInStudyArea(place);
      this.list.append(
        h(
          "li",
          {
            id: `${this.list.id}-${i}`,
            role: "option",
            "aria-selected": "false",
            "aria-disabled": inside ? undefined : "true",
            class: inside ? "place-option" : "place-option is-disabled",
            onclick: inside ? () => this.onChoose(place) : undefined,
          },
          h("span", { class: "place-name" }, place.name),
          h(
            "span",
            { class: "place-description" },
            [place.description, inside ? "" : tr("Di luar Jakarta", "Outside Jakarta")].filter(Boolean).join(" · "),
          ),
        ),
      );
    });
    const open = view.kind !== "closed";
    this.menu.hidden = !open;
    this.input.setAttribute("aria-expanded", String(open && this.places.length > 0));
    this.input.removeAttribute("aria-activedescendant");
    // On a phone the panel is short: without this the list opens below its fold.
    if (open) this.menu.scrollIntoView({ block: "nearest" });
  }

  private onKey(e: KeyboardEvent): void {
    if (this.menu.hidden) return;
    if (e.key === "Escape") {
      e.preventDefault();
      this.onClose();
    } else if (e.key === "Enter") {
      // A form without a chosen place cannot be sent anyway; Enter means "this one".
      e.preventDefault();
      const place = this.active >= 0 ? this.places[this.active] : this.places.find(placeInStudyArea);
      if (place && placeInStudyArea(place)) this.onChoose(place);
    } else if ((e.key === "ArrowDown" || e.key === "ArrowUp") && this.places.some(placeInStudyArea)) {
      e.preventDefault();
      // Move to the next choosable place, skipping those outside the study area.
      const n = this.places.length;
      const dir = e.key === "ArrowDown" ? 1 : -1;
      let i = this.active;
      do i = (i + dir + n) % n;
      while (!placeInStudyArea(this.places[i]));
      this.highlight(i);
    }
  }

  private highlight(index: number): void {
    this.active = index;
    Array.from(this.list.children).forEach((el, i) => {
      el.setAttribute("aria-selected", String(i === index));
      if (i === index) {
        this.input.setAttribute("aria-activedescendant", el.id);
        el.scrollIntoView({ block: "nearest" });
      }
    });
  }
}
