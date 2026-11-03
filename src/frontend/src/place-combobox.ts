import { clear, h } from "./dom";
import type { PlaceSearchView } from "./place-search";
import type { Place } from "./types";

const STATUS: Partial<Record<PlaceSearchView["kind"], string>> = {
  loading: "Mencari tempat…",
  empty: "Tempat tidak ditemukan. Coba nama lain, pilih titik di peta, atau ketik koordinat lat, lon.",
  outside: "Tempat itu berada di luar wilayah studi (Jakarta). Cari tempat di dalam Jakarta.",
};

/** The suggestion list of one field, as an ARIA combobox: the input keeps focus, arrows move
 *  through the options, Enter takes the highlighted one (else the first), Escape closes. */
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
    this.status.textContent = view.kind === "error" ? view.message : (STATUS[view.kind] ?? "");
    this.status.hidden = !this.status.textContent;
    clear(this.list);
    this.list.hidden = !this.places.length;
    this.places.forEach((place, i) => {
      this.list.append(
        h(
          "li",
          { id: `${this.list.id}-${i}`, role: "option", "aria-selected": "false", class: "place-option", onclick: () => this.onChoose(place) },
          h("span", { class: "place-name" }, place.name),
          place.description && h("span", { class: "place-description" }, place.description),
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
      const place = this.places[Math.max(this.active, 0)];
      if (place) this.onChoose(place);
    } else if ((e.key === "ArrowDown" || e.key === "ArrowUp") && this.places.length) {
      e.preventDefault();
      const n = this.places.length;
      this.highlight(e.key === "ArrowDown" ? (this.active + 1) % n : (this.active - 1 + n) % n);
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
