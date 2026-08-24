import { BrandLogo } from "~/components/brand/BrandLogo"

/**
 * The shell every patient-facing Visit Prep screen sits in.
 *
 * Deliberately narrow and centered: this page is almost always opened from an
 * email on a phone. Nothing here is a portal -- no nav, no account, no way to
 * get anywhere else -- so there is no chrome beyond the mark and one footnote.
 */
export function InterviewFrame({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen bg-[var(--brand-bg)] text-[var(--brand-ink)]">
      <div className="mx-auto flex min-h-screen max-w-xl flex-col px-5 py-6 md:py-10">
        <BrandLogo variant="dark" size="sm" />
        <main className="flex flex-1 flex-col justify-center py-8">
          {children}
        </main>
        <p className="pb-4 text-center text-xs text-[var(--brand-muted-text)]">
          Private to your care team. This is not a patient portal login.
        </p>
      </div>
    </div>
  )
}
