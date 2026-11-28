import { describe, expect, it } from "vitest";
import { routeMidpoint, streetFromPlace } from "./route-names";

describe("streetFromPlace", () => {
  it("uses the place when it is a street, shortened", () => {
    expect(streetFromPlace({ name: "Jalan Medan Merdeka Selatan", description: "Gambir, Jakarta" })).toBe("Jl. Medan Merdeka Selatan");
  });

  it("reads the street from the address of a building, without the house number", () => {
    expect(streetFromPlace({ name: "BII Tower", description: "Jalan MH Thamrin kav 22, Menteng, Central Jakarta" })).toBe("Jl. MH Thamrin");
    expect(streetFromPlace({ name: "Kementerian", description: "Jalan MH. Thamrin 8, Tanah Abang, DKI Jakarta" })).toBe("Jl. MH. Thamrin");
    expect(streetFromPlace({ name: "Toko", description: "Jl. Sabang No. 12, Menteng" })).toBe("Jl. Sabang");
  });

  it("gives null when no street is named", () => {
    expect(streetFromPlace({ name: "Monas", description: "Gambir, Jakarta" })).toBeNull();
    expect(streetFromPlace({ name: "Taman", description: "" })).toBeNull();
    expect(streetFromPlace(null)).toBeNull();
  });
});

describe("routeMidpoint", () => {
  it("returns the middle vertex as lat/lon", () => {
    expect(routeMidpoint([[106.8, -6.2], [106.81, -6.21], [106.82, -6.22]])).toEqual({ lat: -6.21, lon: 106.81 });
    expect(routeMidpoint([])).toBeNull();
  });
});
