import { AppFooter } from "~/components/shared/AppFooter"

import { SignIn } from "@clerk/clerk-react"

export default function SignInPage() {
  return (
    <div className="bg-background flex min-h-screen flex-col">
      <div className="flex flex-1 items-center justify-center px-4 py-8">
        <SignIn path="/sign-in" signUpUrl="/sign-up" />
      </div>
      <AppFooter />
    </div>
  )
}
