import { defineConfig } from "vite";

// Local dev proxy so `fetch("/v1/routes")` reaches the Backend without CORS
// setup, mirroring the reverse-proxy role Railway's gateway plays in prod.
export default defineConfig({
  server: {
    proxy: {
      "/v1": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
