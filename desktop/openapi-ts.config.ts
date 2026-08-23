import { defineConfig } from "@hey-api/openapi-ts"

export default defineConfig({
  input: "../contracts/desktop-v1.openapi.json",
  output: "src/generated/desktop-v1",
  plugins: ["@hey-api/typescript", "@hey-api/client-fetch"],
})
