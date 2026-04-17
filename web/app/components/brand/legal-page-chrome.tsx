import { Link } from "react-router"
import { BrandLogo } from "~/components/brand/BrandLogo"

interface LegalPageMastheadProps {
  eyebrow: string
  title: string
  meta?: string
}

export function LegalPageMasthead({ eyebrow, title, meta }: LegalPageMastheadProps) {
  return (
    <header
      className="text-[var(--brand-cream)] px-0 py-12 pb-14 border-b-[3px] border-[var(--brand-gold)] relative"
      style={{ background: "linear-gradient(135deg, var(--brand-navy-deep) 0%, var(--brand-navy-mid) 100%)" }}
    >
      <div className="max-w-[820px] mx-auto px-8">
        <div className="mb-6">
          <BrandLogo variant="light" size="md" />
        </div>
        <span className="text-[11px] uppercase tracking-[3px] text-[var(--brand-gold)] font-semibold">
          {eyebrow}
        </span>
        <h1
          className="text-5xl font-semibold leading-tight mt-2 mb-3 text-[var(--brand-cream)] sm:text-[38px]"
          style={{ fontFamily: "'Cormorant Garamond', serif" }}
        >
          {title}
        </h1>
        {meta && (
          <p className="text-sm text-[rgba(245,240,228,0.75)] mt-3">{meta}</p>
        )}
      </div>
    </header>
  )
}

export function LegalPageFooter() {
  return (
    <footer
      className="text-[rgba(245,240,228,0.8)] py-10 text-sm"
      style={{ background: "var(--brand-navy-deep)" }}
    >
      <div className="max-w-[820px] mx-auto px-8 flex justify-between flex-wrap gap-4">
        <div>CosmaNeura, Inc. · Dignified Intelligence for clinical care</div>
        <div className="flex gap-4">
          <Link to="/terms-of-service" className="text-[var(--brand-gold-bright)] underline">Terms</Link>
          <Link to="/privacy-policy" className="text-[var(--brand-gold-bright)] underline">Privacy</Link>
          <Link to="/support" className="text-[var(--brand-gold-bright)] underline">Support</Link>
        </div>
      </div>
    </footer>
  )
}
