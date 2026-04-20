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
  { href: "#overview", label: "Overview" },
  { href: "#prerequisites", label: "Prerequisites" },
  { href: "#install", label: "Adding the app" },
  { href: "#using", label: "Using the app" },
  { href: "#scopes", label: "Permissions & scopes" },
  { href: "#data", label: "Data handling" },
  { href: "#remove", label: "Removing the app" },
  { href: "#troubleshooting", label: "Troubleshooting" },
  { href: "#support", label: "Support" },
]

const SCOPE_HEADERS = ["Scope", "Why Cosmas requests it"]
const SCOPE_ROWS = [
  [
    "<strong>user:read</strong>",
    "Read your basic Zoom profile (name, email, user ID) to link your Cosmas account to your Zoom account.",
  ],
  [
    "<strong>meeting:read</strong>",
    "Read scheduled and past meeting metadata (topic, start time, participants) so scheduled telehealth visits appear in Cosmas.",
  ],
  [
    "<strong>meeting:write</strong>",
    "Allow our bot to join meetings you host so that Cosmas can transcribe and generate clinical notes.",
  ],
  [
    "<strong>recording:read</strong>",
    "Access cloud recordings of meetings you host when you opt in to post-visit note generation from recorded sessions.",
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

export default function Documentation() {
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
        eyebrow="Documentation"
        title="Cosmas for Zoom"
        meta="A step-by-step guide to installing, using, and removing the Cosmas Zoom app."
      />

      <main className="py-14 pb-20">
        <div className="mx-auto max-w-[820px] px-8">
          <p
            className="mb-10 pl-[22px] text-lg leading-relaxed"
            style={{ borderLeft: "3px solid var(--brand-gold)" }}
          >
            Cosmas is an AI medical scribe for Zoom telehealth visits. Once
            connected to your Zoom account, our bot joins the meetings you host,
            transcribes the conversation, and generates a structured clinical
            note you can review, edit, and publish. This guide explains how to
            add the app, use it during visits, and remove it at any time.
          </p>

          <LegalToc items={TOC} />

          <LegalSection id="overview" num="§ 01" title="Overview">
            <p className="my-3">
              The Cosmas Zoom app is used by licensed clinicians and their
              clinical-practice staff. After you authorize Cosmas with your Zoom
              account, Cosmas is able to:
            </p>
            <ul className="my-3 pl-[22px]">
              <li className="my-1.5">
                Display your scheduled and past Zoom meetings inside the Cosmas
                schedule view so you can pick which visits to document.
              </li>
              <li className="my-1.5">
                Send a capture bot (powered by Recall.ai) into the meetings you
                host so the conversation can be transcribed in real time.
              </li>
              <li className="my-1.5">
                Generate a draft SOAP-style clinical note from the transcript
                and send it back to you for review and publication to your EHR.
              </li>
            </ul>
            <LegalCallout>
              <strong style={strongStyle}>HIPAA note.</strong> If you will
              process Protected Health Information (PHI) through Cosmas, a
              signed Business Associate Agreement (BAA) with CosmaNeura is
              required before you conduct real visits. Email{" "}
              <A href="mailto:info@cosmaneura.com?subject=BAA%20request">
                info@cosmaneura.com
              </A>{" "}
              with "BAA request" in the subject.
            </LegalCallout>
          </LegalSection>

          <LegalSection id="prerequisites" num="§ 02" title="Prerequisites">
            <ul className="my-3 pl-[22px]">
              <li className="my-1.5">
                A Cosmas account at{" "}
                <A href="https://app.cosmaneura.com">app.cosmaneura.com</A>. If
                you do not have one, email{" "}
                <A href="mailto:info@cosmaneura.com">info@cosmaneura.com</A> to
                request access.
              </li>
              <li className="my-1.5">
                A Zoom user account (Pro tier or above is recommended for cloud
                recording features).
              </li>
              <li className="my-1.5">
                Permission, under your Zoom account's security policies, to
                authorize third-party Marketplace apps. Some enterprise accounts
                require an administrator to pre-approve the Cosmas app from the
                Zoom Marketplace.
              </li>
              <li className="my-1.5">
                A current browser (Chrome, Edge, Firefox, or Safari).
              </li>
            </ul>
          </LegalSection>

          <LegalSection id="install" num="§ 03" title="Adding the app">
            <p className="my-3">
              You can add the Cosmas app to your Zoom account in two ways. Both
              flows end at the same authorization screen and produce the same
              result.
            </p>

            <LegalSubheading>From the Zoom Marketplace</LegalSubheading>
            <ol className="my-3 pl-[22px]">
              <li className="my-1.5">
                Sign in to the{" "}
                <A href="https://marketplace.zoom.us">Zoom Marketplace</A> with
                the Zoom account you use for patient visits.
              </li>
              <li className="my-1.5">
                Search for <strong style={strongStyle}>Cosmas</strong> and open
                the app listing.
              </li>
              <li className="my-1.5">
                Click <strong style={strongStyle}>Add</strong> and review the
                requested permissions (see § 05).
              </li>
              <li className="my-1.5">
                Click <strong style={strongStyle}>Allow</strong>. Zoom will
                redirect you to Cosmas to confirm the connection.
              </li>
              <li className="my-1.5">
                When you see "Zoom connected" in your Cosmas settings, you are
                done.
              </li>
            </ol>

            <LegalSubheading>From inside Cosmas</LegalSubheading>
            <ol className="my-3 pl-[22px]">
              <li className="my-1.5">
                Sign in to{" "}
                <A href="https://app.cosmaneura.com">app.cosmaneura.com</A>.
              </li>
              <li className="my-1.5">
                Open <strong style={strongStyle}>Settings</strong> and locate
                the <strong style={strongStyle}>Zoom</strong> section.
              </li>
              <li className="my-1.5">
                Click <strong style={strongStyle}>Connect Zoom</strong>. You
                will be redirected to Zoom to sign in (if needed) and authorize
                the app.
              </li>
              <li className="my-1.5">
                Click <strong style={strongStyle}>Allow</strong>. Zoom will send
                you back to Cosmas automatically.
              </li>
            </ol>

            <LegalCallout>
              <strong style={strongStyle}>
                Only the meeting host needs to install.
              </strong>{" "}
              Co-hosts, panelists, and patients do not install anything. Cosmas
              operates on behalf of the clinician who authorized the app for
              visits that clinician hosts.
            </LegalCallout>
          </LegalSection>

          <LegalSection id="using" num="§ 04" title="Using the app">
            <LegalSubheading>1. Schedule your visit in Zoom</LegalSubheading>
            <p className="my-3">
              Schedule telehealth visits the way you normally do — from your
              Zoom client, calendar, or EHR. No special meeting settings are
              required. Within a few minutes of scheduling, the meeting will
              appear in your Cosmas{" "}
              <strong style={strongStyle}>Schedule</strong> view.
            </p>

            <LegalSubheading>
              2. Choose which visits to document
            </LegalSubheading>
            <p className="my-3">
              In Cosmas, open the meeting in your Schedule and toggle{" "}
              <strong style={strongStyle}>
                "Send Cosmas bot to this visit"
              </strong>
              . You can enable it for an individual visit, or set your default
              to automatically join every scheduled visit (Settings →
              Automation).
            </p>

            <LegalSubheading>3. Start your visit</LegalSubheading>
            <p className="my-3">
              Start the meeting in Zoom as you normally would. A few minutes
              before the scheduled time, the Cosmas bot (labeled{" "}
              <em>Cosmas Notetaker</em> in the participants list) will request
              to join. As the host, you may admit it from the waiting room. The
              bot does not speak, turn on video, or interact with the meeting in
              any way — it listens.
            </p>
            <LegalCallout>
              <strong style={strongStyle}>Patient notification.</strong> It is
              your professional responsibility to inform the patient that the
              visit is being recorded and transcribed for documentation
              purposes, consistent with the consent rules in your jurisdiction
              and with your practice's policies.
            </LegalCallout>

            <LegalSubheading>4. End the visit</LegalSubheading>
            <p className="my-3">
              End the meeting in Zoom. The bot leaves with the meeting. Within a
              few minutes, a draft clinical note appears in Cosmas under{" "}
              <strong style={strongStyle}>Notes</strong>, linked to the patient
              and visit.
            </p>

            <LegalSubheading>5. Review, edit, and publish</LegalSubheading>
            <p className="my-3">
              Open the note, review and edit it, then publish it to your EHR (or
              copy it out). Transcripts are retained per your retention settings
              and your BAA — see the{" "}
              <A href="/privacy-policy">Privacy Policy</A>.
            </p>
          </LegalSection>

          <LegalSection id="scopes" num="§ 05" title="Permissions & scopes">
            <p className="my-3">
              When you authorize the Cosmas Zoom app, you grant Cosmas the
              following OAuth scopes against your Zoom account. Cosmas only uses
              them for the purposes described below.
            </p>
            <LegalTable headers={SCOPE_HEADERS} rows={SCOPE_ROWS} />
            <p className="my-3">
              Cosmas never posts as you, modifies your meetings, invites other
              participants, changes your Zoom account settings, or accesses
              meetings you are not the host of.
            </p>
          </LegalSection>

          <LegalSection id="data" num="§ 06" title="Data handling">
            <p className="my-3">
              Cosmas processes meeting audio and transcripts to generate
              clinical notes. Data is transmitted over TLS, encrypted at rest,
              and stored on HIPAA-compliant infrastructure in the United States.
              Access is restricted to the clinician who authorized the app and
              members of their practice whom they grant access.
            </p>
            <p className="my-3">
              Full details — including retention, deletion, subprocessors, and
              your rights — are in the{" "}
              <A href="/privacy-policy">Privacy Policy</A>.
            </p>
          </LegalSection>

          <LegalSection id="remove" num="§ 07" title="Removing the app">
            <p className="my-3">
              You can disconnect Cosmas from Zoom at any time. There are two
              independent steps, and you should perform both if you want the
              connection fully revoked.
            </p>

            <LegalSubheading>1. Disconnect in Cosmas</LegalSubheading>
            <ol className="my-3 pl-[22px]">
              <li className="my-1.5">
                Sign in to{" "}
                <A href="https://app.cosmaneura.com">app.cosmaneura.com</A>.
              </li>
              <li className="my-1.5">
                Open <strong style={strongStyle}>Settings → Zoom</strong>.
              </li>
              <li className="my-1.5">
                Click <strong style={strongStyle}>Disconnect Zoom</strong>.
                Cosmas will immediately delete its copy of your Zoom OAuth
                tokens and stop sending the bot to future meetings.
              </li>
            </ol>

            <LegalSubheading>
              2. Uninstall from Zoom Marketplace
            </LegalSubheading>
            <ol className="my-3 pl-[22px]">
              <li className="my-1.5">
                Sign in to the{" "}
                <A href="https://marketplace.zoom.us">Zoom Marketplace</A>.
              </li>
              <li className="my-1.5">
                Go to <strong style={strongStyle}>Manage → Added Apps</strong>{" "}
                (or use the search bar).
              </li>
              <li className="my-1.5">
                Find the <strong style={strongStyle}>Cosmas</strong> app and
                click <strong style={strongStyle}>Remove</strong>.
              </li>
              <li className="my-1.5">
                Confirm by clicking <strong style={strongStyle}>Remove</strong>{" "}
                in the dialog. Zoom will revoke the tokens and notify Cosmas,
                which immediately removes any remaining credential state.
              </li>
            </ol>

            <LegalCallout>
              <strong style={strongStyle}>Deleting your data.</strong>{" "}
              Uninstalling stops the connection going forward but does not by
              itself delete the notes and transcripts already produced. To
              request deletion of the data Cosmas has stored on your behalf,
              email{" "}
              <A href="mailto:info@cosmaneura.com?subject=Account%20deletion%20request">
                info@cosmaneura.com
              </A>{" "}
              from the address associated with your Cosmas account. We will
              process the request consistent with HIPAA, state record- retention
              rules, and your BAA.
            </LegalCallout>
          </LegalSection>

          <LegalSection id="troubleshooting" num="§ 08" title="Troubleshooting">
            <LegalSubheading>The bot never joined my meeting</LegalSubheading>
            <p className="my-3">
              The most common cause is a Zoom account permissions mismatch — for
              example, a calendar or Zoom integration configured at the
              individual-user level in a way that prevents external apps from
              joining on your behalf. Verify that the meeting is in your Cosmas
              Schedule view and that "Send Cosmas bot to this visit" is enabled.
              If it is and the bot still did not join, email{" "}
              <A href="mailto:dev@cosmaneura.com">dev@cosmaneura.com</A> with
              the Zoom meeting ID and the scheduled time.
            </p>

            <LegalSubheading>
              I authorized Zoom but Cosmas says "not connected"
            </LegalSubheading>
            <p className="my-3">
              Try signing out of Cosmas and signing back in, then re-running the
              connect flow from{" "}
              <strong style={strongStyle}>Settings → Zoom</strong>. If it still
              fails, your Zoom administrator may have blocked third-party
              Marketplace apps. Ask them to pre-approve Cosmas.
            </p>

            <LegalSubheading>The note was not generated</LegalSubheading>
            <p className="my-3">
              Note generation requires the bot to have successfully captured
              audio. If the host never admitted the bot from the waiting room,
              or if the meeting was very short, a note may not be produced.
              Contact <A href="mailto:dev@cosmaneura.com">dev@cosmaneura.com</A>{" "}
              with the meeting ID if you believe this is in error.
            </p>
          </LegalSection>

          <LegalSection id="support" num="§ 09" title="Support">
            <p className="my-3">
              Full support information, response times, and BAA requests are on
              our <A href="/support">Support page</A>. For urgent
              Zoom-integration issues, email{" "}
              <A href="mailto:dev@cosmaneura.com">dev@cosmaneura.com</A>. For
              everything else — onboarding, billing, BAAs, feature requests —
              email <A href="mailto:info@cosmaneura.com">info@cosmaneura.com</A>
              .
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
