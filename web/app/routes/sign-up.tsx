import { SignUp } from "@clerk/clerk-react"

export default function SignUpPage() {
  return (
    <div className="bg-background flex min-h-screen items-center justify-center px-4">
      <SignUp path="/sign-up" signInUrl="/sign-in" />
    </div>
  )
}
