import type { ReactNode } from "react"

import { BrandLogo } from "~/components/brand/BrandLogo"

/** Adapted from the original Visit Prep frame at 0d92405fbe1b. */
export function InterviewFrame({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-screen bg-[var(--brand-bg)] text-[var(--brand-ink)]">
      <div className="mx-auto flex min-h-screen max-w-xl flex-col px-5 py-6 md:py-10">
        <BrandLogo variant="dark" size="sm" />
        <main className="flex flex-1 flex-col justify-center py-8">
          {children}
        </main>
        <p className="pb-4 text-center text-xs text-[var(--brand-muted-text)]">
          Visit preparation, without a patient portal login. Not for
          emergencies.
        </p>
      </div>
    </div>
  )
}
