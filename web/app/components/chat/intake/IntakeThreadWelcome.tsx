import type { FC } from "react"

export const IntakeThreadWelcome: FC = () => {
  return (
    <div className="flex w-full max-w-[var(--thread-max-width)] flex-grow flex-col">
      <div className="flex w-full flex-grow flex-col items-center justify-center">
        <p className="mt-4 text-lg font-medium">
          Welcome to Your Appointment Intake
        </p>
        <p className="mt-4">
          We'll be asking you a few questions and sending them to your doctor to
          get more done in your appointment.
        </p>
      </div>
      <div className="border-border my-4 w-full border-t"></div>
    </div>
  )
}
