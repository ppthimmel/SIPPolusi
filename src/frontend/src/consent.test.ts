import { afterEach, describe, expect, it, vi } from "vitest";

import { hasConsent, locationPermission, recordConsent, requireConsent, resetConsentForTests } from "./consent";
import { navigationPrivacy, searchPrivacy } from "./notices";

function memoryStorage(): Storage {
  const data = new Map<string, string>();
  return {
    getItem: (k: string) => data.get(k) ?? null,
    setItem: (k: string, v: string) => void data.set(k, v),
  } as unknown as Storage;
}

afterEach(() => {
  resetConsentForTests();
  vi.unstubAllGlobals();
});

describe("privacy consent", () => {
  it("is remembered in the browser once given", () => {
    const storage = memoryStorage();
    expect(hasConsent(storage)).toBe(false);
    recordConsent(storage);
    resetConsentForTests(); // a new page load
    expect(hasConsent(storage)).toBe(true);
  });

  it("lasts for the page when storage is blocked", () => {
    const blocked = {
      getItem: () => {
        throw new DOMException("denied", "SecurityError");
      },
      setItem: () => {
        throw new DOMException("denied", "SecurityError");
      },
    } as unknown as Storage;
    expect(hasConsent(blocked)).toBe(false);
    recordConsent(blocked);
    expect(hasConsent(blocked)).toBe(true);
  });

  it("asks nothing again once agreed", async () => {
    // A pop-up would need `document`, which this test environment does not have.
    vi.stubGlobal("localStorage", memoryStorage());
    recordConsent();
    await expect(requireConsent()).resolves.toBe(true);
  });

  it("reads the browser's location decision, or unknown without the Permissions API", async () => {
    vi.stubGlobal("navigator", { permissions: { query: async () => ({ state: "granted" }) } });
    expect(await locationPermission()).toBe("granted");
    vi.stubGlobal("navigator", {});
    expect(await locationPermission()).toBe("unknown");
  });

  it("uses the approved notice texts", () => {
    expect(searchPrivacy()).toContain("layanan pencarian lokasi");
    expect(navigationPrivacy()).toContain("dikirim ke SIPPolusi");
  });
});
