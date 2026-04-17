import { LegalPageMasthead, LegalPageFooter } from "~/components/brand/legal-page-chrome"
import {
  LegalCallout,
  LegalSection,
  LegalSubheading,
  LegalToc,
} from "~/components/brand/legal-components"

const TOC = [
  { href: "#s1", label: "Acceptance & eligibility" },
  { href: "#s2", label: "The service" },
  { href: "#s3", label: "Account & access" },
  { href: "#s4", label: "Your responsibilities" },
  { href: "#s5", label: "HIPAA & PHI" },
  { href: "#s6", label: "Your data & our AI" },
  { href: "#s7", label: "Third-party services" },
  { href: "#s8", label: "Prohibited uses" },
  { href: "#s9", label: "Intellectual property" },
  { href: "#s10", label: "Fees & billing" },
  { href: "#s11", label: "Medical disclaimer" },
  { href: "#s12", label: "Warranties & disclaimers" },
  { href: "#s13", label: "Limitation of liability" },
  { href: "#s14", label: "Indemnification" },
  { href: "#s15", label: "Term & termination" },
  { href: "#s16", label: "Governing law" },
  { href: "#s17", label: "Dispute resolution" },
  { href: "#s18", label: "Changes to these Terms" },
  { href: "#s19", label: "Contact us" },
]

const linkStyle = { color: "var(--brand-navy-mid)", textDecorationColor: "var(--brand-gold)" }
const strongStyle = { color: "var(--brand-navy-deep)" }

function A({ href, children }: { href: string; children: string }) {
  return (
    <a href={href} className="underline" style={linkStyle}>
      {children}
    </a>
  )
}

export default function TermsOfService() {
  return (
    <div style={{ fontFamily: "Inter, -apple-system, sans-serif", color: "var(--brand-ink)", background: "var(--brand-bg)", minHeight: "100vh" }}>
      <LegalPageMasthead
        eyebrow="Legal"
        title="Terms of Service"
        meta="Effective date: April 16, 2026 · Last updated: April 16, 2026"
      />

      <main className="py-14 pb-20">
        <div className="max-w-[820px] mx-auto px-8">

          <p className="text-lg leading-relaxed mb-10 pl-[22px]" style={{ borderLeft: "3px solid var(--brand-gold)" }}>
            These Terms of Service ("Terms") govern your access to and use of Cosmas, an AI-assisted clinical documentation application operated by CosmaNeura, Inc. ("CosmaNeura," "we," "us," or "our"). Please read them carefully. By installing, enabling, or using Cosmas, you agree to be bound by these Terms.
          </p>

          <LegalToc items={TOC} />

          <LegalSection id="s1" num="§ 01" title="Acceptance & Eligibility">
            <p className="my-3">By installing, enabling, or using Cosmas (the "Service"), you represent that you are (a) a licensed healthcare professional, a member of a healthcare professional's workforce, or a person authorized to act on their behalf; (b) at least 18 years of age; and (c) legally capable of entering into a binding contract in your jurisdiction. If you are accepting these Terms on behalf of an organization (a clinic, practice, or employer), you represent that you have authority to bind that organization to these Terms, and "you" refers to that organization.</p>
            <p className="my-3">Cosmas is a business-to-professional tool. It is not offered to consumers, patients, or individuals seeking personal medical advice.</p>
          </LegalSection>

          <LegalSection id="s2" num="§ 02" title="The Service">
            <p className="my-3">Cosmas is a multi-tenant software application that integrates with Zoom and other supported platforms to assist physicians and clinicians with the preparation of clinical documentation. Current and planned capabilities include, without limitation:</p>
            <ul className="pl-[22px] my-2">
              <li className="my-1.5">Auto-joining scheduled Zoom visits as a participant (the "bot") to capture audio and, where authorized, video and chat;</li>
              <li className="my-1.5">Generating transcripts, SOAP notes, visit summaries, and related clinical documentation drafts;</li>
              <li className="my-1.5">Performing related intake, scheduling, and documentation-workflow functions.</li>
            </ul>
            <p className="my-3">CosmaNeura may update, add, modify, suspend, or remove features of the Service at any time. We will use reasonable efforts to notify you of material changes.</p>
          </LegalSection>

          <LegalSection id="s3" num="§ 03" title="Account & Access">
            <p className="my-3">You access Cosmas by authorizing the application through your Zoom account (and, where applicable, your calendar or scheduling system) using OAuth or a similar authorization flow. You are responsible for (i) maintaining the security and confidentiality of your login credentials; (ii) all activity that occurs under your account; and (iii) promptly notifying us of any unauthorized use. We are not liable for losses resulting from unauthorized use of your account that was not caused by us.</p>
          </LegalSection>

          <LegalSection id="s4" num="§ 04" title="Your Responsibilities">
            <LegalSubheading>Lawful use and patient consent</LegalSubheading>
            <p className="my-3">You are solely responsible for ensuring that your use of Cosmas complies with all applicable federal, state, and local laws, rules, and regulations, including without limitation HIPAA, state medical-recording and wiretapping laws, state telehealth rules, professional-licensure requirements, and any applicable institutional policies. You agree to:</p>
            <ul className="pl-[22px] my-2">
              <li className="my-1.5">Obtain and document all patient consents required for recording, transcription, and AI-assisted documentation under applicable law and your professional standards;</li>
              <li className="my-1.5">Provide patients with clear notice, before recording begins, that an AI assistant is participating in the visit;</li>
              <li className="my-1.5">Maintain your own Business Associate Agreement ("BAA") with Zoom (via a Zoom plan that supports HIPAA use) and with any other platform you connect to Cosmas;</li>
              <li className="my-1.5">Review, edit, and clinically verify every draft note, transcript, and other output before using, signing, or submitting it for any clinical, billing, or legal purpose.</li>
            </ul>
            <LegalSubheading>Accuracy of inputs</LegalSubheading>
            <p className="my-3">You are responsible for the accuracy of information you provide to Cosmas, including meeting details, participant identities, clinical context, and any manual corrections.</p>
            <LegalSubheading>Workforce training</LegalSubheading>
            <p className="my-3">If you are an organization, you are responsible for training your workforce on proper use of Cosmas, including consent workflows, review-before-sign-off practices, and HIPAA Privacy and Security Rule requirements.</p>
          </LegalSection>

          <LegalSection id="s5" num="§ 05" title="HIPAA & Protected Health Information">
            <p className="my-3">To the extent you are a HIPAA Covered Entity (or a Business Associate of one) and you use Cosmas to create, receive, maintain, or transmit Protected Health Information ("PHI"), CosmaNeura will act as your Business Associate. A Business Associate Agreement ("BAA") between you and CosmaNeura is required <em>before</em> any PHI is processed through Cosmas.</p>
            <LegalCallout>
              <strong style={strongStyle}>Required step before go-live.</strong> If you have not executed a BAA with CosmaNeura, do not transmit PHI through the Service. Request a BAA by emailing <A href="mailto:info@cosmaneura.com">info@cosmaneura.com</A>.
            </LegalCallout>
            <p className="my-3">Where a BAA is in place, CosmaNeura will use and disclose PHI only as permitted by that BAA, by these Terms, and by applicable law, and will implement the administrative, physical, and technical safeguards required by the HIPAA Security Rule.</p>
            <p className="my-3">Where you use Cosmas outside a HIPAA-covered context, you remain responsible for compliance with any other applicable data-protection laws (for example, state consumer health-data laws, GDPR for EU residents, and similar regimes).</p>
          </LegalSection>

          <LegalSection id="s6" num="§ 06" title="Your Data & Our AI">
            <p className="my-3">We treat your data with care, and we are deliberate about how we may use it to improve Cosmas. The following terms control:</p>
            <LegalSubheading>Your ownership</LegalSubheading>
            <p className="my-3">You retain all rights, title, and interest in and to the content you, your workforce, or your patients provide to or create within Cosmas, including recordings, transcripts, notes, and clinical documentation ("Customer Content"). Nothing in these Terms transfers ownership of Customer Content or PHI to CosmaNeura.</p>
            <LegalSubheading>License you grant us</LegalSubheading>
            <p className="my-3">You grant CosmaNeura a worldwide, non-exclusive, royalty-free license to host, store, process, transmit, display, and otherwise use Customer Content solely for the purposes permitted in these Terms and in the BAA (if any), namely:</p>
            <ol className="pl-[22px] my-2 list-decimal">
              <li className="my-1.5"><strong style={strongStyle}>Service delivery.</strong> To operate, maintain, support, secure, and troubleshoot Cosmas and to provide the functionality you request.</li>
              <li className="my-1.5"><strong style={strongStyle}>Product improvement and new features.</strong> To analyze, test, fine-tune, train, and evaluate machine-learning models, prompts, workflows, and features that are part of, or may become part of, Cosmas.</li>
              <li className="my-1.5"><strong style={strongStyle}>Aggregate analytics.</strong> To generate de-identified, aggregated statistics about usage and performance of the Service.</li>
            </ol>
            <LegalSubheading>What we will not do</LegalSubheading>
            <LegalCallout>
              <strong style={strongStyle}>CosmaNeura will not sell, rent, or license Customer Content — and will not sell, rent, or license models, datasets, or insights derived from your Customer Content — to any third party.</strong> We will not use Customer Content to deliver advertising, to monetize data through a data broker, or to provide a competing data product to third parties. Model and product improvements developed using Customer Content benefit Cosmas and its users; we do not commercialize your data as a standalone asset.
            </LegalCallout>
            <LegalSubheading>How we handle PHI for improvement</LegalSubheading>
            <p className="my-3">Where Customer Content includes PHI, we will only use that PHI for product improvement (Section 6.2 above) to the extent expressly permitted by our BAA with you and by 45 C.F.R. § 164.504(e)(2)(i)(B) — that is, for our proper management and administration and to provide data aggregation services relating to your health care operations, and/or after the PHI has been de-identified in accordance with 45 C.F.R. § 164.514. We will never use identifiable PHI to train models where our BAA or applicable law prohibits it.</p>
            <LegalSubheading>Your controls</LegalSubheading>
            <p className="my-3">You may request, at any time, that we stop using your Customer Content for product-improvement purposes by emailing <A href="mailto:info@cosmaneura.com">info@cosmaneura.com</A>. Honoring such a request will not affect model improvements that have already been incorporated (which cannot technically be reversed), but will prevent future use of your Customer Content for those purposes.</p>
            <LegalSubheading>Our outputs</LegalSubheading>
            <p className="my-3">As between you and CosmaNeura, you own the specific outputs (transcripts, draft notes, summaries) that Cosmas generates from your inputs, subject to (i) CosmaNeura's underlying intellectual-property rights in the Service (see § 9) and (ii) the license you grant us in this § 6. Because the Service is used by many customers, similar or identical outputs may be generated for others; you do not obtain exclusive rights to outputs.</p>
          </LegalSection>

          <LegalSection id="s7" num="§ 07" title="Third-Party Services">
            <p className="my-3">Cosmas integrates with third-party platforms and subprocessors, including Zoom Video Communications, Inc. and meeting-bot / transcription providers. Your use of those platforms is governed by their own terms and privacy policies. You are responsible for maintaining a compliant account (including any required BAA) with each third-party platform you connect to Cosmas. CosmaNeura is not responsible for third-party service availability, accuracy, security practices, or pricing.</p>
          </LegalSection>

          <LegalSection id="s8" num="§ 08" title="Prohibited Uses">
            <p className="my-3">You agree not to, and not to permit any third party to:</p>
            <ul className="pl-[22px] my-2">
              <li className="my-1.5">Use Cosmas to record any person without the consent required by applicable law;</li>
              <li className="my-1.5">Use Cosmas for any purpose that is illegal, harmful, deceptive, or infringes another person's rights;</li>
              <li className="my-1.5">Reverse engineer, decompile, or attempt to extract source code, models, or weights, except where applicable law expressly prohibits such a restriction;</li>
              <li className="my-1.5">Resell, sublicense, or provide the Service to third parties outside your organization without our written permission;</li>
              <li className="my-1.5">Attempt to bypass rate limits, security controls, or access controls;</li>
              <li className="my-1.5">Input content you do not have the right to input (for example, content that violates another person's privacy, intellectual-property, or publicity rights);</li>
              <li className="my-1.5">Use Cosmas to generate content intended to deceive, impersonate, or cause harm;</li>
              <li className="my-1.5">Use outputs as a substitute for independent clinical judgment (see § 11).</li>
            </ul>
          </LegalSection>

          <LegalSection id="s9" num="§ 09" title="Intellectual Property">
            <p className="my-3">The Service, including all software, models, designs, user interfaces, documentation, and trademarks (including the marks "Cosmas" and "CosmaNeura"), is and will remain the exclusive property of CosmaNeura, its licensors, and third parties whose content is integrated into the Service. Subject to these Terms, CosmaNeura grants you a limited, non-exclusive, non-transferable, revocable license to access and use the Service for your internal clinical and administrative purposes.</p>
            <p className="my-3">You may submit feedback, suggestions, or feature requests ("Feedback"). You grant CosmaNeura a perpetual, irrevocable, royalty-free license to use Feedback for any purpose, including to improve the Service, without obligation to you.</p>
          </LegalSection>

          <LegalSection id="s10" num="§ 10" title="Fees & Billing">
            <p className="my-3">Some or all of the Service may be offered free of charge, on a subscription basis, or on a per-use basis. Applicable fees will be described in a separate order form, subscription plan, or partner agreement. Fees are non-refundable except as required by law or expressly stated in the applicable order. You are responsible for all taxes associated with your use of the Service other than taxes based on CosmaNeura's net income.</p>
          </LegalSection>

          <LegalSection id="s11" num="§ 11" title="Medical Disclaimer">
            <LegalCallout>
              <strong style={strongStyle}>Cosmas is a clinical-documentation assistant. It is not a medical device, diagnostic tool, or source of medical advice.</strong> Outputs are draft materials intended to support — not replace — the independent clinical judgment of a licensed healthcare professional. Every transcript, summary, or note produced by Cosmas must be reviewed, corrected as needed, and approved by a qualified clinician before it is used for any clinical, billing, regulatory, or legal purpose. CosmaNeura does not practice medicine and does not guarantee clinical outcomes or the accuracy of any output.
            </LegalCallout>
          </LegalSection>

          <LegalSection id="s12" num="§ 12" title="Warranties & Disclaimers">
            <p className="my-3 uppercase text-sm leading-relaxed">Except as expressly stated in a separate written agreement with CosmaNeura, the Service is provided "as is" and "as available." To the maximum extent permitted by law, CosmaNeura and its licensors disclaim all warranties, whether express, implied, statutory, or otherwise, including any warranties of merchantability, fitness for a particular purpose, title, non-infringement, and any warranties arising out of a course of dealing or usage of trade. We do not warrant that the Service will be uninterrupted, secure, or error-free, that defects will be corrected, or that outputs will be accurate or complete.</p>
          </LegalSection>

          <LegalSection id="s13" num="§ 13" title="Limitation of Liability">
            <p className="my-3 uppercase text-sm leading-relaxed">To the maximum extent permitted by law, in no event will CosmaNeura, its affiliates, or its licensors be liable for any indirect, incidental, special, consequential, exemplary, or punitive damages, or for any loss of profits, revenues, data, goodwill, or business opportunity, arising out of or related to these Terms or the Service, whether based in contract, tort (including negligence), strict liability, or any other legal theory, even if CosmaNeura has been advised of the possibility of such damages.</p>
            <p className="my-3 uppercase text-sm leading-relaxed">CosmaNeura's total cumulative liability arising out of or related to these Terms or the Service will not exceed the greater of (a) the amounts you paid to CosmaNeura for the Service in the twelve (12) months immediately preceding the event giving rise to the claim or (b) one hundred U.S. dollars (US$100).</p>
            <p className="my-3">The limitations in this § 13 apply to the fullest extent permitted by law. Some jurisdictions do not allow the exclusion or limitation of certain damages, so some of the above may not apply to you.</p>
          </LegalSection>

          <LegalSection id="s14" num="§ 14" title="Indemnification">
            <p className="my-3">You will defend, indemnify, and hold harmless CosmaNeura, its affiliates, and their respective officers, directors, employees, and agents from and against any third-party claims, damages, liabilities, costs, and expenses (including reasonable attorneys' fees) arising out of or related to: (i) your breach of these Terms or applicable law; (ii) your failure to obtain required patient consents; (iii) your Customer Content or any unauthorized inputs; (iv) your use of the Service in combination with any third-party product or service; or (v) reliance on Cosmas outputs without independent clinical review.</p>
          </LegalSection>

          <LegalSection id="s15" num="§ 15" title="Term & Termination">
            <p className="my-3">These Terms begin when you first accept them and continue until terminated. You may terminate at any time by uninstalling the Service and revoking its authorization in Zoom and any connected platforms. We may suspend or terminate your access, with or without notice, if we believe (in our reasonable discretion) that you have violated these Terms, that continued use poses a security or legal risk, or that we are required to do so by law.</p>
            <p className="my-3">Upon termination, the license in § 9 terminates and you must stop using the Service. Provisions that by their nature should survive termination will survive, including §§ 6 (to the extent of pre-termination use), 9, 11, 12, 13, 14, 16, and 17.</p>
            <p className="my-3">We will retain and delete your data after termination in accordance with our <A href="/privacy-policy">Privacy Policy</A> and any applicable BAA.</p>
          </LegalSection>

          <LegalSection id="s16" num="§ 16" title="Governing Law">
            <p className="my-3">These Terms are governed by the laws of the State of New Hampshire, United States, without regard to its conflict-of-laws principles. The United Nations Convention on Contracts for the International Sale of Goods does not apply.</p>
          </LegalSection>

          <LegalSection id="s17" num="§ 17" title="Dispute Resolution">
            <p className="my-3">The parties will first attempt in good faith to resolve any dispute through informal negotiation. If a dispute is not resolved within thirty (30) days, either party may initiate binding arbitration administered by the American Arbitration Association (AAA) under its Commercial Arbitration Rules, seated in Manchester, New Hampshire (or another location mutually agreed in writing). The arbitrator's award may be entered as a judgment in any court of competent jurisdiction. Nothing in this § 17 prevents either party from seeking injunctive or equitable relief in a court of competent jurisdiction to protect its intellectual-property or confidential-information rights. <strong style={strongStyle}>Class-action waiver:</strong> disputes must be brought on an individual basis; class, collective, and representative actions are not permitted.</p>
          </LegalSection>

          <LegalSection id="s18" num="§ 18" title="Changes to These Terms">
            <p className="my-3">We may update these Terms from time to time. When we make material changes, we will update the "Effective date" above and, where practicable, provide additional notice (for example, by email or in-app notification). Your continued use of the Service after the effective date of a revised version constitutes acceptance of the changes.</p>
          </LegalSection>

          <LegalSection id="s19" num="§ 19" title="Contact Us">
            <p className="my-3">Questions about these Terms? Need a BAA? Tell us:</p>
            <ul className="pl-[22px] my-2">
              <li className="my-1.5">General &amp; legal: <A href="mailto:info@cosmaneura.com">info@cosmaneura.com</A></li>
              <li className="my-1.5">Technical &amp; developer: <A href="mailto:dev@cosmaneura.com">dev@cosmaneura.com</A></li>
              <li className="my-1.5">CosmaNeura, Inc. — New Hampshire, USA</li>
            </ul>
          </LegalSection>

          <hr className="border-0 border-t border-[var(--brand-rule)] my-14" />
          <p className="text-[13px]" style={{ color: "var(--brand-muted-text)" }}>
            © 2026 CosmaNeura, Inc. All rights reserved. "Cosmas" and "CosmaNeura" are trademarks of CosmaNeura, Inc.
          </p>
        </div>
      </main>

      <LegalPageFooter />
    </div>
  )
}
