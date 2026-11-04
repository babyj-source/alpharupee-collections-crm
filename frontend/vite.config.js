import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development the API runs on :8000 (python manage.py runserver).
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://127.0.0.1:8000" } },
  test: { environment: "node" },
});
