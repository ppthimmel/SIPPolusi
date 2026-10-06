import { ApiError } from "./api";

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

const RETRY_HINT = "Periksa koneksi, lalu coba lagi.";

/** Maps an API failure to the text and recovery action shown to the user
 *  (Tabel 3.12, 3.15, 3.16). Server messages are shown only for errors the user
 *  can act on (4xx); server-side failures get our own wording. */
export function describeRouteError(err: unknown): ErrorView {
  const base = { field: null, waitSec: null, requestId: null } as const;
  if (!(err instanceof ApiError)) {
    return { ...base, title: "Terjadi kesalahan", message: `Terjadi kesalahan tak terduga. ${RETRY_HINT}`, action: "retry" };
  }
  const requestId = err.requestId;
  const make = (v: Omit<ErrorView, "requestId" | "field" | "waitSec"> & Partial<ErrorView>): ErrorView => ({
    field: null,
    waitSec: null,
    ...v,
    requestId,
  });

  switch (err.kind) {
    case "timeout":
      return make({
        title: "Server terlalu lama merespons",
        message: "Pencarian rute melebihi 12 detik dan dibatalkan. Tidak ada hasil yang ditampilkan.",
        action: "retry",
      });
    case "network":
      return make({ title: "Tidak dapat terhubung ke server", message: RETRY_HINT, action: "retry" });
    case "invalid_response":
      return make({
        title: "Respons server tidak dapat dibaca",
        message: "Server mengirim data yang tidak sesuai format yang diharapkan, sehingga tidak ditampilkan. Coba lagi nanti.",
        action: "retry",
      });
    case "aborted":
      return make({ title: "Dibatalkan", message: "Pencarian rute dibatalkan.", action: "edit" });
    case "http":
      break;
  }

  switch (err.code) {
    case "rate_limited":
      return make({
        title: "Terlalu banyak permintaan",
        message:
          err.retryAfterSec != null
            ? `Batas permintaan tercapai. Coba lagi dalam ${err.retryAfterSec} detik.`
            : "Batas permintaan tercapai. Tunggu sebentar, lalu coba lagi.",
        action: "wait",
        waitSec: err.retryAfterSec,
      });
    case "point_not_on_network":
      return make({
        title: "Titik tidak berada di jalan",
        message: `${err.message} Geser titik ke dekat jalan yang bisa dilalui.`,
        action: "edit",
        field: fieldFromMessage(err.message) ?? "destination",
      });
    case "outside_study_area":
      return make({
        title: "Di luar wilayah studi",
        message: `${err.message} Pilih titik di dalam wilayah Jakarta.`,
        action: "edit",
        field: fieldFromMessage(err.message),
      });
    case "no_route_found":
      return make({
        title: "Rute tidak ditemukan",
        message:
          "Titik asal dan tujuan tidak terhubung oleh jalan yang bisa dilalui moda ini. Coba pindahkan titik atau ganti moda perjalanan.",
        action: "edit",
      });
    case "invalid_request":
      return make({
        title: "Permintaan tidak valid",
        message: "Isian perjalanan ditolak server. Periksa titik, moda, dan prioritas, lalu coba lagi.",
        action: "edit",
      });
    case "cache_unavailable":
      return make({
        title: "Data kualitas udara belum tersedia",
        message: "Estimasi paparan belum bisa dibaca, jadi rute tidak dapat dihitung. Coba lagi beberapa saat lagi.",
        action: "retry",
      });
    case "route_service_unavailable":
      return make({
        title: "Layanan rute sedang tidak tersedia",
        message: "Layanan pencarian rute tidak merespons. Coba lagi beberapa saat lagi.",
        action: "retry",
      });
    case "upstream_timeout":
      return make({
        title: "Server terlalu lama merespons",
        message: "Server tidak merespons tepat waktu. Tidak ada hasil yang ditampilkan.",
        action: "retry",
      });
  }

  if (err.status != null && err.status >= 500) {
    return make({
      title: "Server sedang bermasalah",
      message: "Terjadi gangguan di server. Tidak ada hasil yang ditampilkan. Coba lagi beberapa saat lagi.",
      action: "retry",
    });
  }
  if (err.status === 429) {
    return describeRouteError(
      new ApiError("http", err.message, {
        status: 429,
        code: "rate_limited",
        requestId: err.requestId ?? undefined,
        retryAfterSec: err.retryAfterSec ?? undefined,
      }),
    );
  }
  return make({
    title: "Permintaan ditolak",
    message: "Server menolak permintaan ini. Periksa isian, lalu coba lagi.",
    action: "edit",
  });
}
