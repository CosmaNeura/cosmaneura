import {
  LegalCallout,
  LegalSection,
  LegalTable,
} from "~/components/brand/legal-components"
import {
  LegalPageFooter,
  LegalPageMasthead,
} from "~/components/brand/legal-page-chrome"

const SLA_HEADERS = ["Severity", "Examples", "Target first response"]
const SLA_ROWS = [
  [
    "<strong>Critical</strong>",
    "Service fully unavailable; suspected security incident or data-privacy issue",
    "Within 4 business hours",
  ],
  [
    "<strong>High</strong>",
    "A core workflow (meeting join, transcription, note generation) is broken for one or more users",
    "Within 1 business day",
  ],
  [
    "<strong>Normal</strong>",
    "Questions, feature requests, non-blocking bugs",
    "Within 2 business days",
  ],
]

const linkStyle = {
  color: "var(--brand-navy-mid)",
  textDecorationColor: "var(--brand-gold)",
}
const strongStyle = { color: "var(--brand-navy-deep)" }

function A({ href, children }: { href: string; children: string }) {
  return (
    <a href={href} className="underline" style={linkStyle}>
      {children}
    </a>
  )
}

interface FaqItemProps {
  question: string
  children: React.ReactNode
}

function FaqItem({ question, children }: FaqItemProps) {
  return (
    <details
      className="my-2.5 rounded-sm px-5 py-4"
      style={{ border: "1px solid var(--brand-rule)", background: "#FFFDF6" }}
    >
      <summary
        className="relative cursor-pointer list-none pr-7 font-semibold"
        style={{ color: "var(--brand-navy-deep)" }}
      >
        {question}
        <span
          className="absolute top-[-2px] right-0 text-[22px] font-normal select-none"
          style={{ color: "var(--brand-gold)" }}
          aria-hidden
        >
          +
        </span>
      </summary>
      <div className="mt-2.5 text-[15px]" style={{ color: "var(--brand-ink)" }}>
        {children}
      </div>
    </details>
  )
}

interface ContactCardProps {
  label: string
  title: string
  email: string
  emailSubject: string
  description: string
}

function ContactCard({
  label,
  title,
  email,
  emailSubject,
  description,
}: ContactCardProps) {
  return (
    <div
      className="relative rounded-[4px] px-6 py-6"
      style={{
        background: "#FFFDF6",
        border: "1px solid var(--brand-rule)",
        borderTop: "3px solid var(--brand-gold)",
      }}
    >
      <div
        className="mb-2.5 text-[11px] font-semibold tracking-[2.5px] uppercase"
        style={{ color: "var(--brand-gold)" }}
      >
        {label}
      </div>
      <h3
        className="mb-2 text-[22px] font-semibold"
        style={{
          fontFamily: "'Cormorant Garamond', serif",
          color: "var(--brand-navy-deep)",
        }}
      >
        {title}
      </h3>
      <a
        href={`mailto:${email}?subject=${encodeURIComponent(emailSubject)}`}
        className="my-2.5 block text-base font-medium underline"
        style={linkStyle}
      >
        Email {email}
      </a>
      <p className="m-0 text-sm" style={{ color: "var(--brand-muted-text)" }}>
        {description}
      </p>
    </div>
  )
}

export default function Support() {
  return (
    <div
      style={{
        fontFamily: "Inter, -apple-system, sans-serif",
        color: "var(--brand-ink)",
        background: "var(--brand-bg)",
        minHeight: "100vh",
      }}
    >
      <LegalPageMasthead
        eyebrow="Help"
        title="Support"
        meta="We're here to help you get back to seeing patients."
      />

      <main className="py-14 pb-20">
        <div className="mx-auto max-w-[820px] px-8">
          <p
            className="mb-10 pl-[22px] text-lg leading-relaxed"
            style={{ borderLeft: "3px solid var(--brand-gold)" }}
          >
            Cosmas supports licensed clinicians and clinical-practice staff who
            use the app to reduce their documentation burden. If you need help
            with installation, authorization, meeting capture, note generation,
            billing, a HIPAA Business Associate Agreement, or a security
            concern, the fastest way to reach us is by email.
          </p>

          <LegalSection id="contact" num="§ 01" title="Contact Us">
            <div className="my-7 mb-9 grid grid-cols-2 gap-5 sm:grid-cols-1">
              <ContactCard
                label="General & clinical users"
                title="info@cosmaneura.com"
                email="info@cosmaneura.com"
                emailSubject="Cosmas support request"
                description="Onboarding, account questions, billing, BAAs, feature requests, and anything else."
              />
              <ContactCard
                label="Technical & developer"
                title="dev@cosmaneura.com"
                email="dev@cosmaneura.com"
                emailSubject="Cosmas technical issue"
                description="Integration issues, API questions, webhook failures, and Zoom-app troubleshooting."
              />
            </div>
            <LegalCallout>
              <strong style={strongStyle}>
                In your email, please include:
              </strong>{" "}
              the email address you use to sign in to Cosmas, the name of your
              practice, a description of what you were doing when the issue
              occurred, approximate time and date (with time zone), and any
              error message or screenshot. For Zoom-specific issues, the meeting
              ID and scheduled time help us reproduce the problem quickly.
            </LegalCallout>
          </LegalSection>

          <LegalSection id="hours" num="§ 02" title="Hours & Response Times">
            <p className="my-3">
              We support Cosmas from our headquarters in New Hampshire, USA. Our
              standard support hours are{" "}
              <strong style={strongStyle}>
                Monday – Friday, 9:00 a.m. – 6:00 p.m. Eastern Time
              </strong>
              , excluding U.S. federal holidays.
            </p>
            <LegalTable headers={SLA_HEADERS} rows={SLA_ROWS} />
          </LegalSection>

          <LegalSection
            id="security"
            num="§ 03"
            title="Security & Privacy Concerns"
          >
            <p className="my-3">
              If you believe you have identified a security vulnerability in
              Cosmas, or if you suspect that PHI or other sensitive information
              has been exposed, please email{" "}
              <A href="mailto:info@cosmaneura.com">info@cosmaneura.com</A> with
              "Security" in the subject line. We will acknowledge receipt within
              one business day and keep you informed as we investigate.
            </p>
            <p className="my-3">
              Please do not publicly disclose vulnerabilities before we have had
              a reasonable opportunity to remediate them.
            </p>
          </LegalSection>

          <LegalSection
            id="baa"
            num="§ 04"
            title="Business Associate Agreements"
          >
            <p className="my-3">
              If you process Protected Health Information (PHI) through Cosmas,
              you need a signed Business Associate Agreement (BAA) with
              CosmaNeura. Email{" "}
              <A href="mailto:info@cosmaneura.com?subject=BAA%20request">
                info@cosmaneura.com
              </A>{" "}
              with "BAA request" in the subject and we will send our current
              template for your review.
            </p>
          </LegalSection>

          <LegalSection id="faq" num="§ 05" title="Frequently Asked Questions">
            <FaqItem question="I installed Cosmas but the bot isn't joining my Zoom meetings.">
              <p>
                The most common cause is a Zoom account permissions mismatch —
                for example, a calendar or Zoom integration configured at the
                individual-user level in a way that prevents external apps from
                joining on your behalf. Email{" "}
                <A href="mailto:dev@cosmaneura.com">dev@cosmaneura.com</A> with
                the scheduled meeting ID and the email address you authorized
                with, and we will trace the authorization chain.
              </p>
            </FaqItem>
            <FaqItem question="Does Cosmas work if my patients use a different video platform?">
              <p>
                At present, Cosmas officially supports Zoom for scheduled
                telehealth visits. Support for Microsoft Teams and Google Meet
                is on our roadmap. If that is important to your practice, let us
                know at{" "}
                <A href="mailto:info@cosmaneura.com">info@cosmaneura.com</A> —
                user demand informs our build order.
              </p>
            </FaqItem>
            <FaqItem question="Can I try Cosmas without a Business Associate Agreement?">
              <p>
                You can evaluate the app in a non-clinical context (for example,
                with a simulated visit or role-play) without a BAA. As soon as
                you introduce Protected Health Information, a signed BAA must be
                in place. Email us at{" "}
                <A href="mailto:info@cosmaneura.com?subject=BAA%20request">
                  info@cosmaneura.com
                </A>
                .
              </p>
            </FaqItem>
            <FaqItem question="How do I delete my account and associated data?">
              <p>
                Email{" "}
                <A href="mailto:info@cosmaneura.com?subject=Account%20deletion%20request">
                  info@cosmaneura.com
                </A>{" "}
                from the address associated with your Cosmas account. We will
                confirm the request and process deletion consistent with HIPAA,
                applicable state record-retention rules, and your Business
                Associate Agreement.
              </p>
            </FaqItem>
            <FaqItem question="Can I opt out of having my content used to improve the Cosmas AI?">
              <p>
                Yes. We use Customer Content to improve Cosmas within the limits
                of our BAA with you, and we never sell or monetize it. If you
                would prefer to opt out of product-improvement use entirely,
                email{" "}
                <A href="mailto:info@cosmaneura.com?subject=Opt-out%20from%20product%20improvement">
                  info@cosmaneura.com
                </A>
                . See our <A href="/privacy-policy">Privacy Policy</A>, §§ 5 and
                11, for details.
              </p>
            </FaqItem>
            <FaqItem question="Where are Cosmas and CosmaNeura based?">
              <p>
                CosmaNeura, Inc. is a Delaware C-corporation headquartered in
                New Hampshire, USA.
              </p>
            </FaqItem>
          </LegalSection>

          <hr className="my-14 border-0 border-t border-[var(--brand-rule)]" />
          <p
            className="text-[13px]"
            style={{ color: "var(--brand-muted-text)" }}
          >
            © 2026 CosmaNeura, Inc. All rights reserved. "Cosmas" and
            "CosmaNeura" are trademarks of CosmaNeura, Inc.
          </p>
        </div>
      </main>

      <LegalPageFooter />
    </div>
  )
}
