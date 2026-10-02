// Complete app build for isolated acceptance; never load local environment files.
// Run the React Router build from web/, with COSMANEURA_BUILD_TARGET=test.
import { resolve } from "node:path"
import { pathToFileURL } from "node:url"

const web = resolve(process.cwd())
const moduleAt = (name, suffix) =>
  import(pathToFileURL(resolve(web, "node_modules", name, suffix)).href)
const { defineConfig } = await moduleAt("vite", "dist/node/index.js")
const { reactRouter } = await moduleAt("@react-router/dev", "dist/vite.js")
const { default: paths } = await moduleAt(
  "vite-tsconfig-paths",
  "dist/index.js",
)
const { default: tailwind } = await moduleAt(
  "@tailwindcss/vite",
  "dist/index.mjs",
)

export default defineConfig({
  root: web,
  envDir: false,
  plugins: [tailwind(), reactRouter(), paths()],
  ssr: { noExternal: ["react-dropzone"] },
  define: {
    "import.meta.env.VITE_PYTHON_URL": '"https://testserver"',
    "import.meta.env.VITE_BUILD_COMMIT": JSON.stringify(
      process.env.VITE_BUILD_COMMIT ?? "visit-prep-synthetic-acceptance",
    ),
    "import.meta.env.VITE_SENTRY_DSN": '"https://synthetic@example.test/1"',
    "import.meta.env.VITE_INTAKE_CHAT_DOMAIN": '"testserver"',
    "import.meta.env.VITE_POSTHOG_KEY": '"synthetic-disabled"',
    "import.meta.env.VITE_POSTHOG_HOST": '"https://example.test"',
    "import.meta.env.VITE_CLERK_PUBLIC_KEY":
      '"pk_test_c3ludGhldGljLmV4YW1wbGUudGVzdCQ"',
  },
})
