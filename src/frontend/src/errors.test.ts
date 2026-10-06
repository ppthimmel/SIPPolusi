import { describe, expect, it } from "vitest";
import { ApiError } from "./api";
import { describeRouteError, fieldFromMessage } from "./errors";

const http = (status: number, code: string | undefined, message = "pesan server", extra: { retryAfterSec?: number } = {}) =>
  describeRouteError(new ApiError("http", message, { status, code, requestId: "req-1", ...extra }));

describe("describeRouteError — every state of Tabel 3.12 / 3.16", () => {
  it("point_not_on_network outlines the field the message names and says what to do", () => {
    const v = http(422, "point_not_on_network", "Titik tujuan berjarak lebih dari 200 m dari ruas yang dapat dilalui.");
    expect(v.field).toBe("destination");
    expect(v.action).toBe("edit");
    expect(v.message).toMatch(/Geser titik ke dekat jalan/);
  });

  it("point_not_on_network for the origin outlines the origin", () => {
    expect(http(422, "point_not_on_network", "Titik asal berjarak lebih dari 200 m.").field).toBe("origin");
  });

  it("does not guess a field when the message names none (outside_study_area)", () => {
    expect(http(422, "outside_study_area", "Titik di luar wilayah.").field).toBeNull();
    expect(http(422, "outside_study_area", "Titik asal dan tujuan di luar wilayah.").field).toBeNull();
  });

  it("no_route_found asks to move a point or change mode", () => {
    const v = http(422, "no_route_found");
    expect(v.title).toBe("Rute tidak ditemukan");
    expect(v.action).toBe("edit");
  });

  it("rate_limited carries the wait time from Retry-After (UT-FE-03b)", () => {
    const v = http(429, "rate_limited", "x", { retryAfterSec: 30 });
    expect(v.action).toBe("wait");
    expect(v.waitSec).toBe(30);
    expect(v.message).toMatch(/30 detik/);
  });

  it("a 429 without an error body is still rate limiting", () => {
    expect(http(429, undefined, "HTTP 429", { retryAfterSec: 5 }).waitSec).toBe(5);
  });

  it.each(["cache_unavailable", "route_service_unavailable", "upstream_timeout"])("%s offers a retry (UT-FE-03c)", (code) => {
    expect(http(503, code).action).toBe("retry");
  });

  it("never shows raw server messages of 5xx errors", () => {
    const v = http(500, undefined, "psycopg2.OperationalError: connection refused");
    expect(v.message).not.toMatch(/psycopg2/);
    expect(v.action).toBe("retry");
  });

  it("maps timeout, network failure and an unreadable response, each with a retry", () => {
    expect(describeRouteError(new ApiError("timeout", "t")).message).toMatch(/12 detik/);
    for (const kind of ["timeout", "network", "invalid_response"] as const) {
      expect(describeRouteError(new ApiError(kind, "x")).action).toBe("retry");
    }
  });

  it("invalid_request tells the user to check the inputs", () => {
    expect(http(400, "invalid_request").action).toBe("edit");
  });

  it("keeps the request id for support", () => {
    expect(http(503, "cache_unavailable").requestId).toBe("req-1");
  });

  it("handles errors that are not ApiError", () => {
    expect(describeRouteError(new Error("boom")).action).toBe("retry");
  });
});

describe("fieldFromMessage", () => {
  it("detects the point a message is about", () => {
    expect(fieldFromMessage("Titik asal berjarak …")).toBe("origin");
    expect(fieldFromMessage("Titik tujuan berjarak …")).toBe("destination");
    expect(fieldFromMessage("asal dan tujuan")).toBeNull();
    expect(fieldFromMessage("Titik tidak valid")).toBeNull();
  });
});
