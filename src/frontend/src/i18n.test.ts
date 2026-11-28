import { afterEach, describe, expect, it } from "vitest";
import { ApiError } from "./api";
import { describeRouteError } from "./errors";
import { exposureClass } from "./exposure";
import { fmtMinutes, fmtNumber, fmtWib } from "./format";
import { getLang, onLangChange, setLang, tr } from "./i18n";
import { validatePoint } from "./validation";

describe("interface language", () => {
  afterEach(() => setLang("id"));

  it("defaults to Indonesian", () => {
    expect(getLang()).toBe("id");
    expect(tr("Rute", "Route")).toBe("Rute");
    expect(fmtNumber(34.8)).toBe("34,8");
  });

  it("switches texts and number format to English", () => {
    const seen: string[] = [];
    onLangChange((l) => seen.push(l));
    setLang("en");
    expect(seen).toEqual(["en"]);
    expect(tr("Rute", "Route")).toBe("Route");
    expect(fmtNumber(34.8)).toBe("34.8");
    expect(fmtMinutes(8)).toBe("8 min");
    expect(fmtWib("2026-01-01T05:00:00Z")).toBe("12:00 WIB");
    expect(exposureClass(1.5)).toBe("Moderate");
    expect(validatePoint({ lat: 0, lon: 0 }, "tujuan")).toBe("The destination is outside the study area (Jakarta).");
    expect(describeRouteError(new ApiError("network", "x")).title).toBe("Routes can't be found right now");
  });

  it("goes back to Indonesian", () => {
    setLang("en");
    setLang("id");
    expect(exposureClass(1.5)).toBe("Sedang");
    expect(fmtWib("2026-01-01T05:00:00Z")).toBe("12.00 WIB");
  });
});
