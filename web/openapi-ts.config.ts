import { defineConfig } from "@hey-api/openapi-ts";
import { defaultPlugins } from "@hey-api/openapi-ts";

export default defineConfig({
  input: "./openapi.json",
  output: {
    lint: "biome",
    format: "biome",
    path: "src/client",
    case: "camelCase",
  },
  client: "@hey-api/client-fetch",
  experimentalParser: true,
  plugins: [
    ...defaultPlugins,
    "zod",
    {
      name: "@hey-api/sdk",
      validator: true,
    },
    "@tanstack/react-query",
  ],
});