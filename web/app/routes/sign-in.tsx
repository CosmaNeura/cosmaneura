import { SignIn } from "@clerk/clerk-react"

export default function SignInPage() {
  return (
    <div className="bg-background flex min-h-screen items-center justify-center px-4">
      <SignIn path="/sign-in" signUpUrl="/sign-up" />
    </div>
  )
}
