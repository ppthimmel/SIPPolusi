/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Raster tile URL template for the basemap, e.g. https://tiles.example/{z}/{x}/{y}.png */
  readonly VITE_BASEMAP_TILES?: string;
  /** Route request timeout in ms (default 12000, Tabel 3.15). */
  readonly VITE_ROUTE_TIMEOUT_MS?: string;
  /** Heatmap request timeout in ms (default 5000, Tabel 3.15). */
  readonly VITE_SURFACE_TIMEOUT_MS?: string;
}
