/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Raster tile URL template for the basemap, e.g. https://tiles.example/{z}/{x}/{y}.png */
  readonly VITE_BASEMAP_TILES?: string;
}
