import { Button } from "@/components/ui/button"
import { isPublicIntakeLocation } from "~/configuration/posthog"
import { requireEnv } from "~/utils/environment"

// Patients on the intake link must never be pointed into the clinician app.
// Without the domain setting (the error being shown may be that very
// misconfiguration), assume the patient domain and offer no clinician link.
export function isOnPatientIntake() {
  try {
    return isPublicIntakeLocation(
      window.location,
      requireEnv("VITE_INTAKE_CHAT_DOMAIN"),
    )
  } catch {
    return true
  }
}

export function ErrorPage({
  eyebrow,
  title,
  description,
  showRetry = false,
  children,
}: {
  eyebrow?: React.ReactNode
  title: string
  description: string
  showRetry?: boolean
  children?: React.ReactNode
}) {
  const showHome = !isOnPatientIntake()
  return (
    <main className="bg-background flex min-h-screen items-center justify-center px-4 py-16">
      <div className="flex w-full max-w-lg flex-col items-center text-center">
        {eyebrow}
        <h1 className="text-foreground mt-6 text-3xl font-semibold tracking-tight sm:text-4xl">
          {title}
        </h1>
        <p className="text-muted-foreground mt-4 text-base">{description}</p>
        {(showRetry || showHome) && (
          <div className="mt-8 flex flex-wrap justify-center gap-3">
            {showRetry && (
              <Button type="button" onClick={() => window.location.reload()}>
                Try again
              </Button>
            )}
            {showHome && (
              // A full load resets whatever state broke the page.
              <Button variant={showRetry ? "outline" : "default"} asChild>
                <a href="/">Go to home</a>
              </Button>
            )}
          </div>
        )}
        {children}
      </div>
    </main>
  )
}
