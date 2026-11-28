// Required notices, one copy each so every place shows the same words. The Indonesian text is the
// wording approved for the UX evaluation build (review PR #119).
import { tr } from "./i18n";

/** PNF-07, always visible under the exposure comparison of a route result. */
export const exposureDisclaimerShort = (): string =>
  tr("Estimasi model, bukan saran medis", "Model estimate, not medical advice");

/** PNF-07, full text: behind the ⓘ after the short line. */
export const exposureDisclaimer = (): string =>
  tr(
    "Paparan polusi adalah estimasi model dan dapat berbeda dari kondisi sebenarnya. Gunakan sebagai pertimbangan memilih rute. Ini bukan saran medis.",
    "Pollution exposure is a model estimate and may differ from actual conditions. Use it to help choose a route. This is not medical advice.",
  );

/** PNF-06: what goes to the place search service (consent pop-up, consent.ts). */
export const searchPrivacy = (): string =>
  tr(
    "Teks pencarian dan koordinat titik yang Anda pilih dapat dikirim ke layanan pencarian lokasi untuk menampilkan nama atau saran tempat.",
    "Your search text and the coordinates of points you choose may be sent to a place search service to show place names or suggestions.",
  );

/** PNF-06 / RI-09: what happens to the position during navigation (consent pop-up, consent.ts). */
export const navigationPrivacy = (): string =>
  tr(
    "Posisi GPS Anda diproses di perangkat selama navigasi. Jika rute perlu dihitung ulang, posisi saat itu dikirim ke SIPPolusi sebagai titik awal rute baru.",
    "Your GPS position is processed on your device during navigation. If the route needs recalculating, your position at that moment is sent to SIPPolusi as the start of the new route.",
  );
