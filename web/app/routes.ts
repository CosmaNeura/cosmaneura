import type { RouteConfig } from "@react-router/dev/routes"
import { index, layout, prefix, route } from "@react-router/dev/routes"

// unlike previous RR versions, loaders and other options are not available
// this is meant to be a simple way to define routes configuration at a high level

export default [
  index("routes/login.tsx"),
  layout("layouts/authenticated.tsx", [
    ...prefix("intake", [
      layout("layouts/intake.tsx", [
        index("routes/intake/index.tsx"),
        route("/:id", "routes/intake/detail.tsx"),
      ]),
    ]),
    ...prefix("notes", [
      layout("layouts/notes.tsx", [
        index("routes/notes/index.tsx"),
        route("/:id", "routes/notes/detail.tsx"),
      ]),
    ]),
    route("/settings", "routes/settings.tsx"),
  ]),
] satisfies RouteConfig
