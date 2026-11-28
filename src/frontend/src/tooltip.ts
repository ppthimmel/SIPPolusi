// Tooltips in the app's own style instead of the browser's. Any element with a `title` keeps it in
// the markup (translateDom and the code still set it); when the pointer rests on it or keyboard
// focus reaches it, the text moves to `data-tip` (so the browser's own box does not also appear)
// and is shown in one shared bubble placed beside the element, inside the window.

const SHOW_DELAY_MS = 350;
const GAP = 8; // between the element and the bubble
const EDGE = 8; // minimum distance from the window edge

let bubble: HTMLDivElement | null = null;
let target: HTMLElement | null = null;
let timer = 0;

export function initTooltips(): void {
  bubble = document.createElement("div");
  bubble.className = "tooltip";
  bubble.id = "app-tooltip";
  bubble.setAttribute("role", "tooltip");
  bubble.hidden = true;
  document.body.append(bubble);

  document.addEventListener("pointerover", (e) => {
    if (e.pointerType === "touch") return; // no hover on touch screens
    const el = tipTarget(e.target);
    if (el === target) return;
    hide();
    if (el) schedule(el, SHOW_DELAY_MS);
  });
  document.addEventListener("pointerout", (e) => {
    if (target && !target.contains(e.relatedTarget as Node | null)) hide();
  });
  document.addEventListener("focusin", (e) => {
    const el = tipTarget(e.target);
    hide();
    // Keyboard focus only: a click also focuses, and the tooltip would then stay up.
    if (el && el.matches(":focus-visible")) schedule(el, 0);
  });
  document.addEventListener("focusout", hide);
  document.addEventListener("pointerdown", hide, true);
  document.addEventListener("scroll", hide, true);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") hide();
  });
}

/** The nearest element with a tooltip text, moving a fresh `title` into `data-tip`. */
function tipTarget(node: EventTarget | null): HTMLElement | null {
  const el = node instanceof Element ? node.closest<HTMLElement>("[title], [data-tip]") : null;
  if (!el) return null;
  const title = el.getAttribute("title");
  if (title !== null) {
    // An element named only by its title (an icon button) keeps that name for screen readers.
    if (title && !el.hasAttribute("aria-label") && !el.textContent?.trim()) el.setAttribute("aria-label", title);
    el.dataset.tip = title;
    el.removeAttribute("title");
  }
  return el.dataset.tip ? el : null;
}

function schedule(el: HTMLElement, delay: number): void {
  target = el;
  timer = window.setTimeout(() => show(el), delay);
}

function show(el: HTMLElement): void {
  if (!bubble || target !== el || !el.isConnected) return;
  bubble.textContent = el.dataset.tip ?? "";
  bubble.hidden = false;
  el.setAttribute("aria-describedby", bubble.id);
  place(bubble, el.getBoundingClientRect());
}

function hide(): void {
  window.clearTimeout(timer);
  if (target?.getAttribute("aria-describedby") === "app-tooltip") target.removeAttribute("aria-describedby");
  target = null;
  if (bubble) bubble.hidden = true;
}

/** Below the element, else above, else to the left or right; always kept inside the window. */
function place(tip: HTMLElement, r: DOMRect): void {
  const w = tip.offsetWidth;
  const hgt = tip.offsetHeight;
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const centredX = r.left + r.width / 2 - w / 2;
  const centredY = r.top + r.height / 2 - hgt / 2;
  const options: [number, number][] = [
    [centredX, r.bottom + GAP],
    [centredX, r.top - GAP - hgt],
    [r.left - GAP - w, centredY],
    [r.right + GAP, centredY],
  ];
  const fits = ([x, y]: [number, number]) => x >= EDGE && y >= EDGE && x + w <= vw - EDGE && y + hgt <= vh - EDGE;
  // Buttons stacked against the right edge (the map buttons): beside them, so the bubble does
  // not cover the next button in the column.
  if (r.right > vw - 80 && fits(options[2])) return move(tip, options[2]);
  // Below or above may only need a sideways nudge; prefer those before going to the side.
  const nudge = ([x, y]: [number, number]): [number, number] => [Math.min(Math.max(x, EDGE), vw - EDGE - w), y];
  move(tip, [options[0], options[1]].map(nudge).find((o) => fits(o) && !covers(o, w, hgt, r)) ?? options.slice(2).find(fits) ?? nudge(options[0]));
}

function move(tip: HTMLElement, [x, y]: [number, number]): void {
  tip.style.left = `${Math.round(x)}px`;
  tip.style.top = `${Math.round(y)}px`;
}

/** Whether a bubble at (x, y) would cover the element itself. */
function covers([x, y]: [number, number], w: number, hgt: number, r: DOMRect): boolean {
  return x < r.right && x + w > r.left && y < r.bottom && y + hgt > r.top;
}
