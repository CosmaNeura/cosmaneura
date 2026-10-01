import type { RouteConfig } from "@react-router/dev/routes"
import { index, layout, prefix, route } from "@react-router/dev/routes"

// unlike previous RR versions, loaders and other options are not available
// this is meant to be a simple way to define routes configuration at a high level

export default [
  index("routes/index.tsx"),
  route("/sign-in/*", "routes/sign-in.tsx"),
  route("/sign-up/*", "routes/sign-up.tsx"),
  route("/onboarding", "routes/onboarding.tsx"),
  route("/invite", "routes/invite.tsx"),
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
        route(":id", "routes/notes/detail.tsx"),
      ]),
    ]),
    ...prefix("schedule", [
      layout("layouts/schedule.tsx", [
        index("routes/schedule/index.tsx"),
        route(":zoom_meeting_id", "routes/schedule/detail.tsx"),
      ]),
    ]),
    route("/settings", "routes/settings.tsx"),
    route("/visit-preps", "routes/visit-preps.tsx"),
  ]),
  // public, unauthenticated, intake chat
  route("chat/intake/:id", "routes/chat/intake.tsx"),
  // public legal / support pages
  route("privacy-policy", "routes/privacy-policy.tsx"),
  route("terms-of-service", "routes/terms-of-service.tsx"),
  route("support", "routes/support.tsx"),
  route("documentation", "routes/documentation.tsx"),
] satisfies RouteConfig
