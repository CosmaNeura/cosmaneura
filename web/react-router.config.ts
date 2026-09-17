import { frontendBuildDirectory } from "./build-environment.mjs"
import type { Config } from "@react-router/dev/config"

export default {
  ssr: false,
  buildDirectory: frontendBuildDirectory(process.env),
} satisfies Config
