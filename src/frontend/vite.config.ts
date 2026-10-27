import { defineConfig, loadEnv } from "vite";

// Local dev proxy so `fetch("/v1/routes")` reaches the Backend without CORS
// setup, mirroring the reverse-proxy role Railway's gateway plays in prod.
// BACKEND_URL comes from src/frontend/.env (see .env.example).
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  return {
    server: {
      proxy: {
        "/v1": {
          target: env.BACKEND_URL || "http://localhost:8000",
          changeOrigin: true,
        },
        // Scenario switch of `npm run mock`; the real Backend has no such path.
        "/__mock": {
          target: env.BACKEND_URL || "http://localhost:8000",
          changeOrigin: true,
        },
      },
    },
  };
});
