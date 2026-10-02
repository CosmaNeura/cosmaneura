// Actual creator/email controls with a synthetic provider identity, local only.
import { realpathSync } from "node:fs"
import { createRequire } from "node:module"
import { dirname, resolve } from "node:path"
import { fileURLToPath } from "node:url"

const web = resolve(dirname(fileURLToPath(import.meta.url)), "..")
const requireVite = createRequire(
  realpathSync(resolve(web, "node_modules/vite/package.json")),
)
const { build } = requireVite("esbuild")
const sdk = resolve(web, "app/configuration/client.ts")
const binding = resolve(web, "app/lib/view-as-binding.ts")

await build({
  absWorkingDir: web,
  stdin: {
    contents: `import React from "react";
      import { createRoot } from "react-dom/client";
      import { VisitPrepCreator } from "./app/components/visit-prep/VisitPrepCreator";
      createRoot(document.getElementById("root")).render(<VisitPrepCreator
        access={{eligible:true,can_manage:true,can_send_email:true,can_send_sms:false}}
        scopeKey="synthetic_email_provider" physicianDisplayName="Synthetic physician" />);`,
    resolveDir: web,
    sourcefile: "visit-prep-email-harness.tsx",
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
    "import.meta.env.VITE_VISIT_PREP_EMAIL_WORKFLOW_ENABLED": '"true"',
    "import.meta.env.VITE_INTAKE_CHAT_DOMAIN": '"intake.test.invalid"',
  },
  outfile: resolve(web, "build/test/visit-prep-email-harness.js"),
  plugins: [
    {
      name: "synthetic-provider-boundary",
      setup(builder) {
        builder.onLoad({ filter: /client\.ts$/ }, (args) => {
          if (args.path !== sdk) return
          return {
            loader: "js",
            contents: `
          async function read(path, options = {}) {
            const response = await fetch("https://testserver" + path, {cache:"no-store", ...options});
            return response.ok ? {data:await response.json(),response} : {error:{detail:"Unavailable"},response};
          }
          const prepPath = path => "/internal/v1/visit-prep/preps/" + encodeURIComponent(path.prep_id);
          const post = (path, body, headers = {}) => read(path, {method:"POST", headers:{"Content-Type":"application/json",...headers}, body:JSON.stringify(body)});
          export const visitPrepQuestionSetList = () => read("/internal/v1/visit-prep/question-sets");
          export const visitPrepCreate = ({body,headers}) => post("/internal/v1/visit-prep/preps",body,headers);
          export const visitPrepCopyLink = ({path,body}) => post(prepPath(path)+"/copy-link",body);
          export const visitPrepRevoke = ({path,body}) => post(prepPath(path)+"/revoke",body);
          export const visitPrepEmailInvitation = ({path,body,headers}) => post(prepPath(path)+"/email-invitation",body,headers);
          export const visitPrepEmailStatus = ({path}) => read(prepPath(path)+"/email-status");
        `,
          }
        })
        builder.onLoad({ filter: /view-as-binding\.ts$/ }, (args) => {
          if (args.path !== binding) return
          return {
            loader: "js",
            contents: `
          export const VIEW_AS_HARD_BLOCK_EVENT = "cosmaneura:view-as-hard-block";
          export const subscribeToViewAsStateChanges = () => () => {};
        `,
          }
        })
      },
    },
  ],
})
