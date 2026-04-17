import { Link } from "react-router"

import { BrandLogo } from "~/components/brand/BrandLogo"

interface LegalPageMastheadProps {
  eyebrow: string
  title: string
  meta?: string
}

export function LegalPageMasthead({
  eyebrow,
  title,
  meta,
}: LegalPageMastheadProps) {
  return (
    <header
      className="relative border-b-[3px] border-[var(--brand-gold)] px-0 py-12 pb-14 text-[var(--brand-cream)]"
      style={{
        background:
          "linear-gradient(135deg, var(--brand-navy-deep) 0%, var(--brand-navy-mid) 100%)",
      }}
    >
      <div className="mx-auto max-w-[820px] px-8">
        <div className="mb-6">
          <BrandLogo variant="light" size="md" />
        </div>
        <span className="text-[11px] font-semibold tracking-[3px] text-[var(--brand-gold)] uppercase">
          {eyebrow}
        </span>
        <h1
          className="mt-2 mb-3 text-5xl leading-tight font-semibold text-[var(--brand-cream)] sm:text-[38px]"
          style={{ fontFamily: "'Cormorant Garamond', serif" }}
        >
          {title}
        </h1>
        {meta && (
          <p className="mt-3 text-sm text-[rgba(245,240,228,0.75)]">{meta}</p>
        )}
      </div>
    </header>
  )
}

export function LegalPageFooter() {
  return (
    <footer
      className="py-10 text-sm text-[rgba(245,240,228,0.8)]"
      style={{ background: "var(--brand-navy-deep)" }}
    >
      <div className="mx-auto flex max-w-[820px] flex-wrap justify-between gap-4 px-8">
        <div>CosmaNeura, Inc. · Dignified Intelligence for clinical care</div>
        <div className="flex gap-4">
          <Link
            to="/terms-of-service"
            className="text-[var(--brand-gold-bright)] underline"
          >
            Terms
          </Link>
          <Link
            to="/privacy-policy"
            className="text-[var(--brand-gold-bright)] underline"
          >
            Privacy
          </Link>
          <Link
            to="/support"
            className="text-[var(--brand-gold-bright)] underline"
          >
            Support
          </Link>
        </div>
      </div>
    </footer>
  )
}
