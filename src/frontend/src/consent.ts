// Privacy consent for location data (PNF-06, RI-09; review PR #119). One pop-up, shown before the
// first thing that sends or reads a location: focusing a place field (typing searches as you
// go), picking a point on the map, "Gunakan lokasi saya sebagai asal", the location button, or
// starting navigation. Agreeing is remembered in this browser (a flag only, never a position).
// Declining sends and sets nothing, and the next attempt asks again. Agreeing also gives the
// context for the browser's own location prompt that may follow.
import { h } from "./dom";
import { tr } from "./i18n";
import { navigationPrivacy, searchPrivacy } from "./notices";

const KEY = "sippolusi.privacy-consent.v1";
let agreedThisPage = false;
const listeners: Array<() => void> = [];

/** Called each time the user agrees, whichever way the pop-up was opened. */
export function onConsentGiven(listener: () => void): void {
  listeners.push(listener);
}

/** Storage may be blocked (private windows, strict settings): then the answer lasts for this page. */
export function hasConsent(storage: Pick<Storage, "getItem"> | undefined = safeStorage()): boolean {
  if (agreedThisPage) return true;
  try {
    return storage?.getItem(KEY) === "yes";
  } catch {
    return false;
  }
}

export function recordConsent(storage: Pick<Storage, "setItem"> | undefined = safeStorage()): void {
  agreedThisPage = true;
  try {
    storage?.setItem(KEY, "yes");
  } catch {
    /* kept for this page only */
  }
}

/** Test hook: forget the answer given on this page. */
export function resetConsentForTests(): void {
  agreedThisPage = false;
}

function safeStorage(): Storage | undefined {
  try {
    return globalThis.localStorage;
  } catch {
    return undefined;
  }
}

/** Resolves true once the user has agreed (at once when they did before), false when they decline. */
export function requireConsent(): Promise<boolean> {
  if (hasConsent()) return Promise.resolve(true);
  return open(false);
}

/** "Privasi" in the panel footer: the same text, to read again; a plain close once agreed. */
export function showPrivacy(): void {
  void open(hasConsent());
}

/** The browser's decision on geolocation, or "unknown" without the Permissions API (older Safari). */
export async function locationPermission(): Promise<PermissionState | "unknown"> {
  try {
    const status = await navigator.permissions?.query({ name: "geolocation" });
    return status?.state ?? "unknown";
  } catch {
    return "unknown";
  }
}

let dialog: HTMLDialogElement | null = null;
let pending: Promise<boolean> | null = null;

function open(readOnly: boolean): Promise<boolean> {
  if (pending) return pending; // a second trigger while it is open waits for the same answer
  const d = (dialog ??= document.body.appendChild(
    h("dialog", { class: "info-dialog consent-dialog", "aria-labelledby": "consent-title" }) as HTMLDialogElement,
  ));
  // Nothing for the browser to hand focus back to on close: a place field getting focus again
  // would ask again at once. Callers move focus themselves after an agreement.
  (document.activeElement as HTMLElement | null)?.blur();
  pending = new Promise<boolean>((resolve) => {
    const done = (ok: boolean) => {
      d.removeEventListener("cancel", onCancel);
      d.close();
      pending = null;
      if (ok && !readOnly) {
        recordConsent();
        for (const listener of listeners) listener();
      }
      resolve(ok || readOnly);
    };
    const onCancel = (e: Event) => {
      e.preventDefault();
      done(false); // Escape declines
    };
    d.addEventListener("cancel", onCancel);
    const agree = h("button", { type: "button", class: "primary", onclick: () => done(true) }, readOnly ? tr("Tutup", "Close") : tr("Setuju", "Agree"));
    d.replaceChildren(
      h("div", { class: "info-head" }, h("h2", { id: "consent-title" }, tr("Privasi data lokasi", "Location data privacy"))),
      h(
        "div",
        { class: "info-body" },
        h("h3", {}, tr("Pencarian tempat", "Place search")),
        h("p", {}, searchPrivacy()),
        h("h3", {}, tr("Navigasi", "Navigation")),
        h("p", {}, navigationPrivacy()),
        readOnly
          ? null
          : h("p", { class: "fine-print" }, tr("Titik asal dan tujuan hanya bisa dipilih setelah Anda setuju.", "You can choose a start and destination once you agree.")),
      ),
      h(
        "div",
        { class: "consent-actions" },
        readOnly ? null : h("button", { type: "button", class: "secondary consent-decline", onclick: () => done(false) }, tr("Tidak setuju", "Disagree")),
        agree,
      ),
    );
    d.showModal();
    agree.focus();
  });
  return pending;
}
