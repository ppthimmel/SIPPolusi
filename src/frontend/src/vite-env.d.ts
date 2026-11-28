/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** MapLibre style JSON URL for the basemap (default: OpenFreeMap "liberty"). */
  readonly VITE_BASEMAP_STYLE?: string;
  /** Raster tile URL template instead of the vector style, e.g. https://tiles.example/{z}/{x}/{y}.png */
  readonly VITE_BASEMAP_TILES?: string;
  /** Route request timeout in ms (default 12000, Tabel 3.15). */
  readonly VITE_ROUTE_TIMEOUT_MS?: string;
  /** Heatmap request timeout in ms (default 5000, Tabel 3.15). */
  readonly VITE_SURFACE_TIMEOUT_MS?: string;
  /** "Uji navigasi" test panel: true/false (default: on in `npm run dev`, off in a build). */
  readonly VITE_NAV_TEST_PANEL?: string;
  /** GPS spoofer (?lokasi, ?simulasi): on/off (default: on in `npm run dev`, off in a build). */
  readonly VITE_GPS_SPOOF?: string;
}
