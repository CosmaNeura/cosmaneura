import { Navigate, href } from "react-router"

import PageNotFound from "~/components/shared/PageNotFound"
import { requireEnv } from "~/utils/environment"

import { SignIn, SignedIn, SignedOut } from "@clerk/clerk-react"

const INTAKE_CHAT_DOMAIN = requireEnv("VITE_INTAKE_CHAT_DOMAIN")

// redirect users to the correct page based on their authentication status
export default function LoginRedirect() {
  // we don't want to do any of the login stuff on the chat domain, this will confuse consumers vs doctors
  if (window.location.hostname === INTAKE_CHAT_DOMAIN) {
    return <PageNotFound />
  }

  return (
    <div className="mt-[10%] flex min-h-screen justify-center">
      <SignedIn>
        <Navigate to={href("/notes")} />
      </SignedIn>
      <SignedOut>
        <SignIn />
      </SignedOut>
    </div>
  )
}
