/// <reference types="vitest" />
/// <reference types="vite/client" />
import tsconfigPaths from "vite-tsconfig-paths"

import react from "@vitejs/plugin-react"
import { defineConfig } from "vitest/config"

export default defineConfig({
  plugins: [react(), tsconfigPaths()],
  test: {
    globals: true,
    environment: "happy-dom",
    setupFiles: ["./test/setup-test-env.ts"],
    coverage: {
      provider: "v8",
    },
  },
})
