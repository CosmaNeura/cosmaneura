// Actual patient components; local synthetic acceptance, not an application route.
import { realpathSync } from "node:fs"
import { createRequire } from "node:module"
import { dirname, resolve } from "node:path"
import { fileURLToPath } from "node:url"

const web = resolve(dirname(fileURLToPath(import.meta.url)), "..")
const requireVite = createRequire(
  realpathSync(resolve(web, "node_modules/vite/package.json")),
)
const { build } = requireVite("esbuild")
const binding = resolve(web, "app/lib/view-as-binding.ts")

await build({
  absWorkingDir: web,
  stdin: {
    contents: `import React from "react";
      import { createRoot } from "react-dom/client";
      import { PatientInterview } from "./app/components/visit-prep/PatientInterview";
      createRoot(document.getElementById("root")).render(<PatientInterview />);`,
    resolveDir: web,
    sourcefile: "visit-prep-patient-harness.tsx",
    loader: "tsx",
  },
  bundle: true,
  platform: "browser",
  format: "esm",
  minify: true,
  jsx: "automatic",
  tsconfig: resolve(web, "tsconfig.json"),
  define: {
    "process.env.NODE_ENV": '"production"',
    "import.meta.env.VITE_PYTHON_URL": '"https://testserver"',
    "import.meta.env.DEV": "false",
    "import.meta.env.VITE_VISIT_PREP_PATIENT_AI_ENABLED": '"false"',
  },
  outfile: resolve(
    web,
    "build/test/client/assets/visit-prep-patient-harness.js",
  ),
  plugins: [
    {
      name: "synthetic-support-view-not-active",
      setup(builder) {
        builder.onLoad({ filter: /view-as-binding\.ts$/ }, (args) => {
          if (args.path !== binding) return
          return {
            contents: `export const VIEW_AS_HARD_BLOCK_EVENT = "cosmaneura:view-as-hard-block";
            export const subscribeToViewAsStateChanges = () => () => {};`,
            loader: "js",
          }
        })
      },
    },
  ],
})
