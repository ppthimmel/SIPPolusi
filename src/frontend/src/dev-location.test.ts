import { describe, expect, it } from "vitest";
import { DEV_PLACES, parseDevLocation } from "./dev-location";

describe("parseDevLocation", () => {
  it("knows the named places", () => {
    expect(parseDevLocation("monas")).toEqual(DEV_PLACES.monas);
    expect(parseDevLocation("Blok-M")).toEqual(DEV_PLACES["blok-m"]);
  });

  it("takes lat,lon", () => {
    expect(parseDevLocation("-6.2, 106.8")).toEqual({ lat: -6.2, lon: 106.8 });
  });

  it("rejects anything else", () => {
    expect(parseDevLocation("bandung")).toBeNull();
    expect(parseDevLocation("-6.2")).toBeNull();
    expect(parseDevLocation("200, 106")).toBeNull();
    expect(parseDevLocation(null)).toBeNull();
  });
});
