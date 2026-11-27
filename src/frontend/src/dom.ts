// Tiny DOM builder. Everything that comes from the API goes through textContent
// (never innerHTML), so server-provided strings cannot inject markup.

type Child = Node | string | number | null | undefined | false;
type Props = Record<string, string | number | boolean | EventListener | undefined | null>;

export function h<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  props: Props = {},
  ...children: Child[]
): HTMLElementTagNameMap[K] {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") el.className = String(value);
    else if (key.startsWith("on") && typeof value === "function") el.addEventListener(key.slice(2), value);
    else if (value === true) el.setAttribute(key, "");
    else el.setAttribute(key, String(value));
  }
  append(el, children);
  return el;
}

export function append(el: Element, children: Child[]): void {
  for (const c of children) {
    if (c === null || c === undefined || c === false) continue;
    el.append(typeof c === "string" || typeof c === "number" ? document.createTextNode(String(c)) : c);
  }
}

export function clear(el: Element): void {
  el.replaceChildren();
}

const ICONS = {
  shield: '<path d="M12 3l7 3v5c0 4.5-3 8-7 10-4-2-7-5.5-7-10V6z"/><path d="M9 12l2 2 4-4"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>',
  warn: '<path d="M12 3l10 18H2z"/><path d="M12 10v5M12 18h.01"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  back: '<path d="M15 5l-7 7 7 7"/>',
  close: '<path d="M6 6l12 12M18 6L6 18"/>',
  sidebar: '<rect x="3.5" y="4.5" width="17" height="15" rx="3"/><path d="M9.5 4.5v15"/>',
  swap: '<path d="M8 4v16M4 8l4-4 4 4M16 20V4M12 16l4 4 4-4"/>',
  layers: '<path d="M12 3l9 5-9 5-9-5z"/><path d="M3 13l9 5 9-5"/>',
  locate: '<circle cx="12" cy="12" r="4"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3"/>',
  // Walk and bike are bold (solid head, heavy strokes) so they read at a glance in the mode switch.
  walk: '<circle cx="14" cy="3.6" r="2.2" fill="currentColor" stroke="none"/><path stroke-width="2.6" stroke-linecap="butt" stroke-linejoin="miter" d="M13 7.4l-1.5 6.2M11.5 13.6l3.4 2.6.6 5.6M11.5 13.6L8.6 21.8M13 7.4L9 9.2l-.3 3.6M13 7.4l1.9 3.1 3 1.1"/>',
  bike: '<circle cx="15.8" cy="4" r="2.2" fill="currentColor" stroke="none"/><circle cx="5.5" cy="17.5" r="3.6" stroke-width="2.4"/><circle cx="18.5" cy="17.5" r="3.6" stroke-width="2.4"/><path stroke-width="2.6" stroke-linecap="butt" stroke-linejoin="miter" d="M11.5 18.5v-4.6L8.4 11.4l4.2-3.6 2.6 2.7h3.3"/>',
  route: '<circle cx="6" cy="19" r="2"/><path d="M8 19h8a4 4 0 000-8H8a4 4 0 010-8h8"/>',
  pin: '<path d="M12 21s7-6.2 7-11a7 7 0 10-14 0c0 4.8 7 11 7 11z"/><circle cx="12" cy="10" r="2.5"/>',
  navigate: '<path d="M12 2l7 19-7-4-7 4z"/>',
  straight: '<path d="M12 21V4M6 10l6-6 6 6"/>',
  turn_left: '<path d="M17 21v-8a4 4 0 00-4-4H5M9 5L5 9l4 4"/>',
  turn_right: '<path d="M7 21v-8a4 4 0 014-4h8M15 5l4 4-4 4"/>',
  slight_left: '<path d="M14 21v-7L8 6M7 11V5h6"/>',
  slight_right: '<path d="M10 21v-7l6-8M17 11V5h-6"/>',
  uturn: '<path d="M8 21V9a5 5 0 0110 0v4M14 9l4 4 4-4"/>',
  flag: '<path d="M5 21V4M5 4h11l-2 4 2 4H5"/>',
} as const;

export type IconName = keyof typeof ICONS;

/** Decorative inline SVG icon (static markup only, no API data). */
export function icon(name: IconName, size = 18): HTMLSpanElement {
  const span = document.createElement("span");
  span.className = "icon";
  span.setAttribute("aria-hidden", "true");
  span.innerHTML =
    `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" ` +
    `stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${ICONS[name]}</svg>`;
  return span;
}
