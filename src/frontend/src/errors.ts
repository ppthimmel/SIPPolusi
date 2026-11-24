import { ApiError } from "./api";
import { tr } from "./i18n";

export type ErrorAction = "retry" | "edit" | "wait";
export type ErrorField = "origin" | "destination" | null;

export interface ErrorView {
  title: string;
  message: string;
  action: ErrorAction;
  /** Form field to outline in red, when the error is about one point. */
  field: ErrorField;
  /** Seconds to wait before the next try (rate limiting). */
  waitSec: number | null;
  requestId: string | null;
}

/** Which point a server message talks about. The contract's messages name it
 *  ("Titik tujuan berjarak …"); without a clear match no field is outlined. */
export function fieldFromMessage(message: string): ErrorField {
  const asal = /\basal\b/i.test(message);
  const tujuan = /\btujuan\b/i.test(message);
  if (asal === tujuan) return null;
  return asal ? "origin" : "destination";
}

/** A failed place search never blocks the form: the map and coordinates still work. */
export function describePlaceError(err: unknown): string {
  if (err instanceof ApiError && (err.code === "rate_limited" || err.status === 429)) {
    return tr("Terlalu banyak pencarian. Tunggu sebentar, atau pilih titik di peta.", "Too many searches. Wait a moment, or pick a point on the map.");
  }
  return tr(
    "Pencarian tempat sedang tidak tersedia. Pilih titik di peta.",
    "Place search is unavailable right now. Pick a point on the map.",
  );
}

/** Maps an API failure to what the user sees (Tabel 3.12, 3.15, 3.16), in plain words.
 *  Problems on our side (server, network, data not ready) share one general message
 *  without technical detail; problems the user can fix say only what to do. Server
 *  messages are never shown. */
export function describeRouteError(err: unknown): ErrorView {
  const requestId = err instanceof ApiError ? err.requestId : null;
  const make = (v: Omit<ErrorView, "requestId" | "field" | "waitSec"> & Partial<ErrorView>): ErrorView => ({
    field: null,
    waitSec: null,
    ...v,
    requestId,
  });
  const general = (): ErrorView =>
    make({
      title: tr("Rute belum dapat dicari", "Routes can't be found right now"),
      message: tr("Terjadi kendala saat mencari rute. Coba lagi beberapa saat lagi.", "Something went wrong while finding routes. Please try again in a moment."),
      action: "retry",
    });

  if (!(err instanceof ApiError) || err.kind !== "http") {
    if (err instanceof ApiError && err.kind === "aborted") {
      return make({ title: tr("Dibatalkan", "Cancelled"), message: tr("Pencarian rute dibatalkan.", "The route search was cancelled."), action: "edit" });
    }
    return general();
  }

  if (err.code === "rate_limited" || err.status === 429) {
    const wait = err.retryAfterSec;
    return make({
      title: tr("Terlalu banyak pencarian", "Too many searches"),
      message:
        wait != null
          ? tr(`Tunggu ${wait} detik, lalu coba lagi.`, `Wait ${wait} seconds, then try again.`)
          : tr("Tunggu sebentar, lalu coba lagi.", "Wait a moment, then try again."),
      action: "wait",
      waitSec: wait,
    });
  }

  switch (err.code) {
    case "point_not_on_network": {
      const field = fieldFromMessage(err.message) ?? "destination";
      return make({
        title: tr("Titik tidak berada di jalan", "Point is not on a road"),
        message:
          field === "origin"
            ? tr("Geser titik asal ke dekat jalan yang bisa dilalui.", "Move the starting point closer to a road.")
            : tr("Geser titik tujuan ke dekat jalan yang bisa dilalui.", "Move the destination closer to a road."),
        action: "edit",
        field,
      });
    }
    case "outside_study_area":
      return make({
        title: tr("Di luar wilayah layanan", "Outside the service area"),
        message: tr("Pilih titik di dalam wilayah Jakarta.", "Pick points inside Jakarta."),
        action: "edit",
        field: fieldFromMessage(err.message),
      });
    case "no_route_found":
      return make({
        title: tr("Rute tidak ditemukan", "No route found"),
        message: tr("Coba pindahkan titik atau ganti moda perjalanan.", "Try moving the points or changing the travel mode."),
        action: "edit",
      });
    case "invalid_request":
      return make({
        title: tr("Rute belum dapat dicari", "Routes can't be found right now"),
        message: tr("Periksa titik asal dan tujuan, lalu coba lagi.", "Check the start and destination, then try again."),
        action: "edit",
      });
  }

  if (err.status != null && err.status >= 400 && err.status < 500 && err.code !== "payload_too_large") {
    return make({
      title: tr("Rute belum dapat dicari", "Routes can't be found right now"),
      message: tr("Periksa titik asal dan tujuan, lalu coba lagi.", "Check the start and destination, then try again."),
      action: "edit",
    });
  }
  // Data not ready, service down, gateway timeout, 5xx and anything unexpected.
  return general();
}
