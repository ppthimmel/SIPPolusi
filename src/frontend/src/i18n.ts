// Interface language: Indonesian (default) or English, chosen by the user and kept
// in this browser. Texts are written in place as tr("Indonesia", "English") and
// resolved when they are rendered, so a switch only needs a re-render.
// Text that comes from the Backend (server error messages, metadata.disclaimer)
// is shown as the server sent it.

export type Lang = "id" | "en";

const STORAGE_KEY = "sippolusi.lang";
const listeners = new Set<(lang: Lang) => void>();

function stored(): Lang {
  try {
    const v = globalThis.localStorage?.getItem(STORAGE_KEY);
    return v === "en" ? "en" : "id";
  } catch {
    return "id";
  }
}

let current: Lang = stored();

export function getLang(): Lang {
  return current;
}

/** BCP 47 locale for number and time formatting. */
export function locale(): string {
  return current === "en" ? "en-US" : "id-ID";
}

export function setLang(lang: Lang): void {
  if (lang === current) return;
  current = lang;
  try {
    globalThis.localStorage?.setItem(STORAGE_KEY, lang);
  } catch {
    // Storage blocked: the choice lasts until the page is reloaded.
  }
  for (const fn of listeners) fn(lang);
}

export function onLangChange(fn: (lang: Lang) => void): void {
  listeners.add(fn);
}

/** The text in the current language. */
export function tr(id: string, en: string): string {
  return current === "en" ? en : id;
}

const DOM_ATTRS = ["placeholder", "aria-label", "title", "content"] as const;

/** Static markup in index.html carries its English next to the Indonesian:
 *  `data-en` for the text, `data-en-<attr>` for an attribute. The Indonesian
 *  original is remembered on first use so the switch works both ways. */
export function translateDom(root: ParentNode = document): void {
  for (const el of Array.from(root.querySelectorAll<HTMLElement>("[data-en]"))) {
    if (el.dataset.id === undefined) el.dataset.id = el.textContent ?? "";
    el.textContent = current === "en" ? el.dataset.en! : el.dataset.id;
  }
  for (const attr of DOM_ATTRS) {
    const enKey = `data-en-${attr}`;
    const idKey = `data-id-${attr}`;
    for (const el of Array.from(root.querySelectorAll<HTMLElement>(`[${enKey}]`))) {
      if (!el.hasAttribute(idKey)) el.setAttribute(idKey, el.getAttribute(attr) ?? "");
      el.setAttribute(attr, current === "en" ? el.getAttribute(enKey)! : el.getAttribute(idKey)!);
    }
  }
  if (typeof document !== "undefined") document.documentElement.lang = current;
}
