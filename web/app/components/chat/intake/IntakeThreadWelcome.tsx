import type { FC } from "react"

// Patients are not signed in, so the practice's terminology is unavailable
// here; "provider" reads correctly for doctors, therapists and counselors.
export const IntakeThreadWelcome: FC = () => {
  return (
    <div className="flex w-full max-w-[var(--thread-max-width)] flex-grow flex-col">
      <div className="flex w-full flex-grow flex-col items-center justify-center text-center">
        <h1 className="mt-4 text-xl font-semibold">
          Let&apos;s make the most of your visit
        </h1>
        <p className="text-muted-foreground mt-3 max-w-prose">
          Your provider asked us to gather a few details before your
          appointment. Your answers go straight to them, so your time together
          can focus on what matters most to you.
        </p>
        <p className="text-muted-foreground mt-2 max-w-prose text-sm">
          There are no wrong answers. Write as much or as little as you like.
        </p>
      </div>
      <div className="border-border my-4 w-full border-t"></div>
    </div>
  )
}
