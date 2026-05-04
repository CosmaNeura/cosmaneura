import { AppFooter } from "~/components/shared/AppFooter"

import { SignUp } from "@clerk/clerk-react"

export default function SignUpPage() {
  return (
    <div className="bg-background flex min-h-screen flex-col">
      <div className="flex flex-1 items-center justify-center px-4 py-8">
        <SignUp path="/sign-up" signInUrl="/sign-in" />
      </div>
      <AppFooter />
    </div>
  )
}
