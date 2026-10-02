// Local synthetic component acceptance only; never part of application routes.
import { realpathSync } from "node:fs"
import { createRequire } from "node:module"
import { dirname, resolve } from "node:path"
import { fileURLToPath } from "node:url"

const web = resolve(dirname(fileURLToPath(import.meta.url)), "..")
const requireVite = createRequire(
  realpathSync(resolve(web, "node_modules/vite/package.json")),
)
const { build } = requireVite("esbuild")
const origin = "https://testserver"
const sdk = resolve(web, "app/configuration/client.ts")
const binding = resolve(web, "app/lib/view-as-binding.ts")

await build({
  absWorkingDir: web,
  stdin: {
    contents: `import React from "react";
      import { createRoot } from "react-dom/client";
      import { VisitPrepPackages } from "./app/components/visit-prep/VisitPrepPackages";
      import { VisitPrepEmailPolicy } from "./app/components/visit-prep/VisitPrepEmailPolicy";
      import { VisitPrepSMSPolicy } from "./app/components/visit-prep/VisitPrepSMSPolicy";
      createRoot(document.getElementById("root")).render(location.pathname === "/synthetic-sms-policy" ? <VisitPrepSMSPolicy
        access={{eligible:true,can_manage:true,can_send_email:false,can_send_sms:false}}
        scopeKey="synthetic_provider_harness" /> : <><VisitPrepPackages
        access={{eligible:true,can_manage:true,can_send_email:false,can_send_sms:false}}
        scopeKey="synthetic_provider_harness" /><VisitPrepEmailPolicy
        access={{eligible:true,can_manage:true,can_send_email:false,can_send_sms:false}}
        scopeKey="synthetic_provider_harness" /></>);`,
    resolveDir: web,
    sourcefile: "visit-prep-package-harness.tsx",
    loader: "tsx",
  },
  bundle: true,
  platform: "browser",
  format: "esm",
  minify: true,
  jsx: "automatic",
  tsconfig: resolve(web, "tsconfig.json"),
  define: { "process.env.NODE_ENV": '"production"' },
  outfile: resolve(web, "build/test/visit-prep-package-harness.js"),
  plugins: [
    {
      name: "synthetic-identity-boundary",
      setup(builder) {
        builder.onLoad({ filter: /client\.ts$/ }, (args) => {
          if (args.path !== sdk) return
          return {
            contents: `async function read(path, options = {}) {
          const response = await fetch("${origin}" + path, {cache:"no-store", ...options});
          return response.ok ? {data:await response.json(),response} : {error:{detail:"Unavailable"},response};
        }
        export const visitPrepList = ({query}) => read("/internal/v1/visit-prep/preps?" + new URLSearchParams(query));
        const synthesisPath = (path) => "/internal/v1/visit-prep/preps/" + encodeURIComponent(path.prep_id) + "/synthesis";
        export const visitPrepSynthesis = ({path}) => read(synthesisPath(path));
        export const visitPrepSynthesisRequest = ({path,body}) => read(synthesisPath(path), {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
        export const visitPrepEmailPolicyRead = () => read("/internal/v1/visit-prep/email-policy");
        export const visitPrepSmsPolicyRead = () => read("/internal/v1/visit-prep/sms-policy");
        export const visitPrepSmsPolicyDecide = ({body,headers}) => read("/internal/v1/visit-prep/sms-policy", {method:"POST",headers:{"Content-Type":"application/json",...headers},body:JSON.stringify(body)});
        export const visitPrepEmailStatus = ({path}) => read("/internal/v1/visit-prep/preps/" + encodeURIComponent(path.prep_id) + "/email-status");
        export const visitPrepSmsStatus = ({path}) => read("/internal/v1/visit-prep/preps/" + encodeURIComponent(path.prep_id) + "/sms-status");
        export const visitPrepEmailPolicyDecide = ({body,headers}) => read("/internal/v1/visit-prep/email-policy", {method:"POST",headers:{"Content-Type":"application/json",...headers},body:JSON.stringify(body)});`,
            loader: "js",
          }
        })
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
