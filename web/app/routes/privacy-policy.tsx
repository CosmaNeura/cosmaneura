import {
  LegalCallout,
  LegalSection,
  LegalSubheading,
  LegalTable,
  LegalToc,
} from "~/components/brand/legal-components"
import {
  LegalPageFooter,
  LegalPageMasthead,
} from "~/components/brand/legal-page-chrome"

const TOC = [
  { href: "#s1", label: "Who this policy applies to" },
  { href: "#s2", label: "Information we collect" },
  { href: "#s3", label: "How we use information" },
  { href: "#s4", label: "HIPAA & PHI" },
  { href: "#s5", label: "AI training & product improvement" },
  { href: "#s6", label: "How we share information" },
  { href: "#s7", label: "Subprocessors" },
  { href: "#s8", label: "What we do not do" },
  { href: "#s9", label: "Data security" },
  { href: "#s10", label: "Data retention & deletion" },
  { href: "#s11", label: "Your rights & choices" },
  { href: "#s12", label: "State privacy rights" },
  { href: "#s13", label: "International transfers" },
  { href: "#s14", label: "Children's privacy" },
  { href: "#s15", label: "Changes to this policy" },
  { href: "#s16", label: "Contact us" },
]

const USE_TABLE_HEADERS = ["Purpose", "Legal basis / authority"]
const USE_TABLE_ROWS = [
  [
    "Provide the Service (join meetings, transcribe, generate notes, sync calendars)",
    "Performance of contract; BAA permitted use",
  ],
  [
    "Authenticate users, secure accounts, and prevent fraud or abuse",
    "Legitimate interest; BAA security obligations",
  ],
  [
    "Provide customer support and respond to requests",
    "Performance of contract; your consent",
  ],
  [
    "Improve Cosmas — debug, test, evaluate, fine-tune models and prompts, and build new features (see § 5)",
    "Legitimate interest; BAA health care operations; de-identification under 45 C.F.R. § 164.514",
  ],
  [
    "Generate de-identified, aggregated analytics and reports",
    "Legitimate interest; HIPAA-compliant de-identification",
  ],
  ["Comply with law and respond to lawful requests", "Legal obligation"],
  [
    "Send service, security, and product-update communications",
    "Performance of contract; legitimate interest",
  ],
]

const linkStyle = {
  color: "var(--brand-navy-mid)",
  textDecorationColor: "var(--brand-gold)",
}

function A({ href, children }: { href: string; children: string }) {
  return (
    <a href={href} className="underline" style={linkStyle}>
      {children}
    </a>
  )
}

export default function PrivacyPolicy() {
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
        eyebrow="Legal"
        title="Privacy Policy"
        meta="Effective date: April 16, 2026 · Last updated: April 16, 2026"
      />

      <main className="py-14 pb-20">
        <div className="mx-auto max-w-[820px] px-8">
          <p
            className="mb-10 pl-[22px] text-lg leading-relaxed"
            style={{ borderLeft: "3px solid var(--brand-gold)" }}
          >
            CosmaNeura, Inc. ("CosmaNeura," "we," "us") respects the privacy of
            clinicians, their patients, and their staff. This Privacy Policy
            explains what information we collect through Cosmas, how we use and
            share it, and the choices you have. It works together with our{" "}
            <A href="/terms-of-service">Terms of Service</A> and, where
            applicable, your Business Associate Agreement ("BAA") with us.
          </p>

          <LegalCallout>
            <strong style={{ color: "var(--brand-navy-deep)" }}>
              In one sentence:
            </strong>{" "}
            Cosmas processes meeting content and related data so clinicians can
            spend more time with patients and less time on paperwork; we may use
            that content — within the limits of HIPAA and our BAA — to improve
            Cosmas and build new features for our users, and we do <em>not</em>{" "}
            sell it, rent it, or monetize it as a data product.
          </LegalCallout>

          <LegalToc items={TOC} />

          <LegalSection id="s1" num="§ 01" title="Who This Policy Applies To">
            <p className="my-3">
              Cosmas is a business-to-professional service designed for licensed
              healthcare professionals and the staff of clinical practices. This
              policy addresses information handled by CosmaNeura when a
              clinician, practice, or authorized user installs, configures, or
              uses Cosmas.
            </p>
            <p className="my-3">
              If you are a patient whose visit is documented by Cosmas on behalf
              of your clinician, your clinician (not CosmaNeura) is the party
              responsible, under HIPAA and state privacy laws, for the
              collection, use, and disclosure of your Protected Health
              Information ("PHI"). Please direct privacy questions about your
              clinical record to your clinician's practice.
            </p>
          </LegalSection>

          <LegalSection id="s2" num="§ 02" title="Information We Collect">
            <LegalSubheading>Account and contact information</LegalSubheading>
            <ul className="my-2 pl-[22px]">
              <li className="my-1.5">
                Name, professional email address, practice name, role, and time
                zone;
              </li>
              <li className="my-1.5">
                Authentication data received from Zoom, Google, or other
                identity providers when you authorize Cosmas (we do not receive
                or store your passwords for those services);
              </li>
              <li className="my-1.5">
                Billing and payment information, if you purchase a paid plan,
                processed by a third-party payment processor.
              </li>
            </ul>
            <LegalSubheading>Meeting content (may include PHI)</LegalSubheading>
            <ul className="my-2 pl-[22px]">
              <li className="my-1.5">
                Audio, and where authorized, video and in-meeting chat of Zoom
                visits that you schedule Cosmas to join;
              </li>
              <li className="my-1.5">
                Transcripts generated from those recordings;
              </li>
              <li className="my-1.5">
                Draft SOAP notes, summaries, and other clinical-documentation
                outputs produced by our models;
              </li>
              <li className="my-1.5">
                Edits, corrections, and annotations you make to outputs.
              </li>
            </ul>
            <LegalSubheading>Scheduling and integration data</LegalSubheading>
            <ul className="my-2 pl-[22px]">
              <li className="my-1.5">
                Calendar events and meeting metadata (date, time, title,
                participants, join URL) from Zoom, Acuity, Google Calendar, or
                other systems you connect;
              </li>
              <li className="my-1.5">
                Patient identifiers you send us through integrations (for
                example, appointment type or patient reference IDs).
              </li>
            </ul>
            <LegalSubheading>Usage and technical data</LegalSubheading>
            <ul className="my-2 pl-[22px]">
              <li className="my-1.5">
                Pages viewed, features used, clicks, session duration, and error
                events in Cosmas;
              </li>
              <li className="my-1.5">
                Device and browser type, operating system, IP address, and
                approximate location;
              </li>
              <li className="my-1.5">
                Log files, cookies, and similar technologies. We limit cookies
                to those necessary to operate and secure the Service and to
                measure aggregate usage.
              </li>
            </ul>
            <LegalSubheading>Communications</LegalSubheading>
            <p className="my-3">
              Email and support-ticket content you send to us, including via{" "}
              <A href="mailto:info@cosmaneura.com">info@cosmaneura.com</A>.
            </p>
          </LegalSection>

          <LegalSection id="s3" num="§ 03" title="How We Use Information">
            <LegalTable headers={USE_TABLE_HEADERS} rows={USE_TABLE_ROWS} />
          </LegalSection>

          <LegalSection
            id="s4"
            num="§ 04"
            title="HIPAA & Protected Health Information"
          >
            <p className="my-3">
              When Cosmas processes PHI on behalf of a HIPAA Covered Entity (or
              a Business Associate of one), CosmaNeura acts as a Business
              Associate. Our use and disclosure of PHI is governed by:
            </p>
            <ul className="my-2 pl-[22px]">
              <li className="my-1.5">
                The HIPAA Privacy Rule (45 C.F.R. Part 164, Subpart E) and
                Security Rule (Subpart C);
              </li>
              <li className="my-1.5">
                The Breach Notification Rule (Subpart D);
              </li>
              <li className="my-1.5">
                The BAA executed between you and CosmaNeura.
              </li>
            </ul>
            <p className="my-3">Under that framework, we will:</p>
            <ul className="my-2 pl-[22px]">
              <li className="my-1.5">
                Use and disclose PHI only as permitted by the BAA and these
                documents;
              </li>
              <li className="my-1.5">
                Implement administrative, physical, and technical safeguards to
                protect PHI, including encryption in transit (TLS 1.2+) and at
                rest;
              </li>
              <li className="my-1.5">
                Require our subprocessors that handle PHI to provide written
                assurances (including, where required, their own BAAs) that are
                at least as protective as our BAA with you;
              </li>
              <li className="my-1.5">
                Report Security Incidents and Breaches to you in accordance with
                the BAA and applicable law;
              </li>
              <li className="my-1.5">
                Make PHI available to you, or directly to an individual, as
                necessary to satisfy a Covered Entity's obligations under 45
                C.F.R. §§ 164.524 and 164.526.
              </li>
            </ul>
            <LegalCallout>
              <strong style={{ color: "var(--brand-navy-deep)" }}>
                No BAA, no PHI.
              </strong>{" "}
              If you have not executed a BAA with CosmaNeura, please do not use
              Cosmas to process PHI. To request a BAA, email{" "}
              <A href="mailto:info@cosmaneura.com">info@cosmaneura.com</A>.
            </LegalCallout>
          </LegalSection>

          <LegalSection
            id="s5"
            num="§ 05"
            title="AI Training & Product Improvement"
          >
            <p className="my-3">
              Cosmas is an AI-driven product, and we believe being transparent
              about how we use data to improve it is a core commitment to our
              users.
            </p>
            <LegalSubheading>What we do</LegalSubheading>
            <p className="my-3">
              We use information processed through Cosmas to:
            </p>
            <ul className="my-2 pl-[22px]">
              <li className="my-1.5">
                Test and measure model accuracy, safety, and clinical utility;
              </li>
              <li className="my-1.5">
                Fine-tune, prompt-engineer, and evaluate the models, workflows,
                and features that power Cosmas;
              </li>
              <li className="my-1.5">
                Build new features that serve our users (for example, new note
                templates, new specialty workflows, new integrations);
              </li>
              <li className="my-1.5">
                Generate de-identified analytics about how clinicians use
                Cosmas.
              </li>
            </ul>
            <LegalSubheading>How we do it responsibly</LegalSubheading>
            <ul className="my-2 pl-[22px]">
              <li className="my-1.5">
                <strong style={{ color: "var(--brand-navy-deep)" }}>
                  PHI is treated as PHI.
                </strong>{" "}
                Where content includes PHI, we only use it for improvement
                purposes to the extent permitted by our BAA with you and by
                HIPAA, including the "proper management and administration" and
                "data aggregation" provisions at 45 C.F.R. § 164.504(e)(2)(i)(B)
                and § 164.504(e)(2)(i)(A), or after de-identification per 45
                C.F.R. § 164.514.
              </li>
              <li className="my-1.5">
                <strong style={{ color: "var(--brand-navy-deep)" }}>
                  De-identification first.
                </strong>{" "}
                Wherever feasible, we de-identify content before using it to
                train or evaluate models.
              </li>
              <li className="my-1.5">
                <strong style={{ color: "var(--brand-navy-deep)" }}>
                  Access controls.
                </strong>{" "}
                Access to raw, identifiable content is limited to authorized
                personnel under strict need-to-know rules and is logged.
              </li>
              <li className="my-1.5">
                <strong style={{ color: "var(--brand-navy-deep)" }}>
                  No training on data from users who opt out.
                </strong>{" "}
                You may opt out of having your Customer Content used for
                product-improvement purposes (see § 11).
              </li>
              <li className="my-1.5">
                <strong style={{ color: "var(--brand-navy-deep)" }}>
                  Not every third-party model provider.
                </strong>{" "}
                Where we route content to third-party AI providers to generate
                outputs, we only use providers that (i) have a BAA with us when
                PHI is involved, and (ii) contractually commit not to use your
                content to train their own models.
              </li>
            </ul>
          </LegalSection>

          <LegalSection id="s6" num="§ 06" title="How We Share Information">
            <p className="my-3">
              We share information only in the following, limited circumstances:
            </p>
            <ul className="my-2 pl-[22px]">
              <li className="my-1.5">
                <strong style={{ color: "var(--brand-navy-deep)" }}>
                  Service providers and subprocessors
                </strong>{" "}
                that help us operate Cosmas (hosting, security, meeting-bot
                services, transcription, AI model inference, analytics, and
                customer support). These providers process data on our behalf,
                under written contracts that restrict their use of the data and,
                where applicable, include a BAA.
              </li>
              <li className="my-1.5">
                <strong style={{ color: "var(--brand-navy-deep)" }}>
                  Your organization.
                </strong>{" "}
                If you use Cosmas through a clinic or employer account, that
                organization's administrators may access your account
                information, usage, and content.
              </li>
              <li className="my-1.5">
                <strong style={{ color: "var(--brand-navy-deep)" }}>
                  Legal and safety disclosures
                </strong>{" "}
                when we reasonably believe disclosure is required by law, legal
                process, or to protect the rights, property, or safety of
                CosmaNeura, our users, or the public.
              </li>
              <li className="my-1.5">
                <strong style={{ color: "var(--brand-navy-deep)" }}>
                  Business transfers.
                </strong>{" "}
                If CosmaNeura is involved in a merger, acquisition, financing,
                or sale of assets, information may be transferred as part of
                that transaction, subject to protections at least as strong as
                those in this Policy.
              </li>
              <li className="my-1.5">
                <strong style={{ color: "var(--brand-navy-deep)" }}>
                  With your direction or consent
                </strong>{" "}
                — for example, when you integrate Cosmas with another service
                and instruct us to send data to it.
              </li>
            </ul>
          </LegalSection>

          <LegalSection id="s7" num="§ 07" title="Subprocessors">
            <p className="my-3">
              A current list of CosmaNeura's material subprocessors (including
              meeting-capture, AI model, and cloud-hosting providers) is
              available on request from{" "}
              <A href="mailto:info@cosmaneura.com">info@cosmaneura.com</A>. We
              will notify customers in advance of material changes to this list,
              consistent with our BAAs and applicable data-protection
              agreements.
            </p>
          </LegalSection>

          <LegalSection id="s8" num="§ 08" title="What We Do Not Do">
            <LegalCallout>
              <strong style={{ color: "var(--brand-navy-deep)" }}>
                We will not:
              </strong>
              <ul className="mt-2.5 pl-[22px]">
                <li className="my-1.5">
                  Sell, rent, or lease your Customer Content or personal
                  information to any third party;
                </li>
                <li className="my-1.5">
                  Sell, rent, or license datasets, models, or insights derived
                  from your Customer Content to any third party;
                </li>
                <li className="my-1.5">
                  Use your Customer Content to deliver advertising to you or
                  anyone else;
                </li>
                <li className="my-1.5">
                  Use identifiable PHI to train models where our BAA or
                  applicable law prohibits it;
                </li>
                <li className="my-1.5">
                  Use your Customer Content to build products competitive with
                  your practice.
                </li>
              </ul>
              Product and model improvements developed with your Customer
              Content serve Cosmas and its users. We do not commercialize your
              data as a standalone asset.
            </LegalCallout>
          </LegalSection>

          <LegalSection id="s9" num="§ 09" title="Data Security">
            <p className="my-3">
              We maintain administrative, physical, and technical safeguards
              designed to protect information we process. These include:
            </p>
            <ul className="my-2 pl-[22px]">
              <li className="my-1.5">
                Encryption of data in transit (TLS 1.2+) and at rest;
              </li>
              <li className="my-1.5">
                Role-based access controls, multi-factor authentication for
                administrative access, and least-privilege principles;
              </li>
              <li className="my-1.5">
                Network isolation, continuous monitoring, and logging of access
                to sensitive systems;
              </li>
              <li className="my-1.5">
                Vulnerability management, regular patching, and independent
                security review as we scale;
              </li>
              <li className="my-1.5">
                A documented incident-response plan, including breach
                notification procedures consistent with HIPAA and applicable
                state laws.
              </li>
            </ul>
            <p className="my-3">
              No system is perfectly secure. If you believe your account or data
              has been compromised, contact us immediately at{" "}
              <A href="mailto:info@cosmaneura.com">info@cosmaneura.com</A>.
            </p>
          </LegalSection>

          <LegalSection id="s10" num="§ 10" title="Data Retention & Deletion">
            <p className="my-3">
              We retain Customer Content and account information for as long as
              needed to provide the Service to you, comply with our legal
              obligations, resolve disputes, and enforce our agreements.
              Specific retention periods are governed by your BAA and
              subscription agreement where applicable.
            </p>
            <p className="my-3">
              You may request deletion of your account and associated Customer
              Content by emailing{" "}
              <A href="mailto:info@cosmaneura.com">info@cosmaneura.com</A>. We
              will process deletion requests consistent with HIPAA, state
              record-retention requirements (which may require your{" "}
              <em>clinical</em> record to be retained by the clinician even if
              it is deleted from Cosmas), and reasonable technical limitations.
            </p>
            <p className="my-3">
              De-identified and aggregated data may be retained indefinitely
              because it no longer relates to an identifiable individual.
            </p>
          </LegalSection>

          <LegalSection id="s11" num="§ 11" title="Your Rights & Choices">
            <ul className="my-2 pl-[22px]">
              <li className="my-1.5">
                <strong style={{ color: "var(--brand-navy-deep)" }}>
                  Access, correction, and deletion.
                </strong>{" "}
                You may request access to, correction of, or deletion of your
                account information at any time by emailing{" "}
                <A href="mailto:info@cosmaneura.com">info@cosmaneura.com</A>.
              </li>
              <li className="my-1.5">
                <strong style={{ color: "var(--brand-navy-deep)" }}>
                  Opt out of product-improvement use.
                </strong>{" "}
                You may request that we stop using your Customer Content for AI
                training and product improvement by emailing{" "}
                <A href="mailto:info@cosmaneura.com">info@cosmaneura.com</A>.
                Honoring your request prevents future use for those purposes; we
                cannot retroactively remove your data's influence on
                already-trained models.
              </li>
              <li className="my-1.5">
                <strong style={{ color: "var(--brand-navy-deep)" }}>
                  Disconnect integrations.
                </strong>{" "}
                You may revoke Cosmas's access to Zoom or other connected
                platforms at any time from within the connected platform's
                settings.
              </li>
              <li className="my-1.5">
                <strong style={{ color: "var(--brand-navy-deep)" }}>
                  Marketing communications.
                </strong>{" "}
                You may unsubscribe from non-essential emails at any time.
                Transactional and security communications are not subject to
                unsubscribe.
              </li>
            </ul>
          </LegalSection>

          <LegalSection id="s12" num="§ 12" title="State Privacy Rights">
            <p className="my-3">
              Certain U.S. state privacy laws (including California's CCPA/CPRA,
              and similar laws in Virginia, Colorado, Connecticut, Utah, Texas,
              and other states) provide rights to residents of those states,
              including the right to know, access, delete, correct, and opt out
              of the "sale" or "sharing" of personal information for
              cross-context behavioral advertising.
            </p>
            <p className="my-3">
              <strong style={{ color: "var(--brand-navy-deep)" }}>
                We do not sell or share personal information as those terms are
                defined under those laws.
              </strong>{" "}
              Many categories of information processed through Cosmas are PHI
              subject to HIPAA and are therefore exempt from most state
              consumer-privacy laws. To exercise any applicable state privacy
              right, email{" "}
              <A href="mailto:info@cosmaneura.com">info@cosmaneura.com</A>. We
              may need to verify your identity before responding.
            </p>
          </LegalSection>

          <LegalSection id="s13" num="§ 13" title="International Transfers">
            <p className="my-3">
              Cosmas is designed primarily for users in the United States. If
              you access the Service from outside the U.S., your information
              will be transferred to, stored, and processed in the United
              States. By using Cosmas, you consent to that transfer.
            </p>
          </LegalSection>

          <LegalSection id="s14" num="§ 14" title="Children's Privacy">
            <p className="my-3">
              Cosmas is a professional tool and is not directed to children. We
              do not knowingly collect personal information directly from
              individuals under 13 in a consumer context. PHI of pediatric
              patients may be processed through Cosmas on behalf of a clinician,
              subject to HIPAA and the clinician's own patient-privacy
              obligations.
            </p>
          </LegalSection>

          <LegalSection id="s15" num="§ 15" title="Changes to This Policy">
            <p className="my-3">
              We may update this Privacy Policy from time to time. When we make
              material changes, we will update the "Effective date" and, where
              practicable, provide additional notice. Your continued use of the
              Service after the effective date of a revised version constitutes
              acceptance of the changes.
            </p>
          </LegalSection>

          <LegalSection id="s16" num="§ 16" title="Contact Us">
            <p className="my-3">
              If you have questions, concerns, or requests about this Privacy
              Policy or our handling of your information:
            </p>
            <ul className="my-2 pl-[22px]">
              <li className="my-1.5">
                Primary contact:{" "}
                <A href="mailto:info@cosmaneura.com">info@cosmaneura.com</A>
              </li>
              <li className="my-1.5">
                Technical &amp; developer:{" "}
                <A href="mailto:dev@cosmaneura.com">dev@cosmaneura.com</A>
              </li>
              <li className="my-1.5">CosmaNeura, Inc. — New Hampshire, USA</li>
            </ul>
            <p className="my-3">
              U.S. residents may also contact the Office for Civil Rights at the
              U.S. Department of Health and Human Services if they believe their
              HIPAA rights have been violated (
              <A href="https://www.hhs.gov/ocr">www.hhs.gov/ocr</A>).
            </p>
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
