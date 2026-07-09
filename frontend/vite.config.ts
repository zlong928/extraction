import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const apiTarget = env.VITE_API_PROXY_TARGET || env.VITE_API_BASE_URL || "http://127.0.0.1:8001";

  return {
    plugins: [react()],
    server: {
      port: 5173,
      proxy: {
        "/papers": {
          target: apiTarget,
          changeOrigin: true,
        },
        "/extractions": {
          target: apiTarget,
          changeOrigin: true,
        },
      },
    },
  };
});
