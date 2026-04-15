import { Navigate, href } from "react-router"

import PageNotFound from "~/components/shared/PageNotFound"
import { requireEnv } from "~/utils/environment"

const INTAKE_CHAT_DOMAIN = requireEnv("VITE_INTAKE_CHAT_DOMAIN")

// Redirect to /notes. Auth is enforced by the notes clientLoader via getClient().
export default function LoginRedirect() {
  // we don't want to do any of the login stuff on the chat domain, this will confuse consumers vs doctors
  if (window.location.hostname === INTAKE_CHAT_DOMAIN) {
    return <PageNotFound />
  }

  return <Navigate to={href("/notes")} replace />
}
