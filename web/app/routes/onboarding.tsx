import { useEffect, useState } from "react"
import { useForm } from "react-hook-form"
import { href, redirect } from "react-router"
import { z } from "zod"

import { ArrowRight, Building2, UserRound } from "lucide-react"

import type { Route } from "./+types/onboarding"

import { ViewAsAccountControl } from "~/components/ViewAsAccountControl"
import { ViewAsRouteGuard } from "~/components/ViewAsRouteGuard"
import { BrandLogo } from "~/components/brand/BrandLogo"
import { Icons } from "~/components/icons"
import { ImageDropzone } from "~/components/shared/ImageDropzone"
import { PracticeJoinPanel } from "~/components/shared/PracticeJoinPanel"
import { SimpleComboBox } from "~/components/shared/SimpleComboBox"
import {
  CheckRow,
  Field,
  WorkforceAttestation,
  type WorkforceAttestationSubmission,
} from "~/components/shared/WorkforceAttestation"
import { Button } from "~/components/ui/button"
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "~/components/ui/form"
import { Input } from "~/components/ui/input"
import { ScrollArea } from "~/components/ui/scroll-area"
import { getClient } from "~/configuration/clerk"
import {
  completeOnboarding,
  settingsData,
  updateSettingsData,
} from "~/configuration/client"
import { toast } from "~/hooks/use-toast"
import { isAccountRecoveryRequired } from "~/lib/account-recovery"
import {
  BAA_DOC_ID,
  BAA_PDF_URL,
  BAA_VERSION,
  COMPANY,
  baaAuthorityLine,
  baaCheckboxLabel,
} from "~/lib/baa"
import { internalApiFetch } from "~/lib/billing-recovery"
import { type Channel, resolveChannel } from "~/lib/channel"
import {
  MCD_EMAIL_DOMAIN,
  extractEmailDomain,
  isMcdEmail,
  isPublicEmailDomain,
} from "~/lib/email-domain"
import {
  exampleNoteUploadErrorCopy,
  submitExampleNote,
} from "~/lib/example-note-upload"
import { actionablePendingInvitation } from "~/lib/pending-invitation"
import {
  type BaaDocument,
  type OrganizationAccessOpportunity,
  type PracticeStatus,
  acceptPracticeBaa,
  claimOrganizationAccess,
  createPractice,
  getBaaCurrent,
  getOrganizationAccessOpportunity,
  getPractice,
  joinPractice,
  lookupPractice,
  practiceReady,
} from "~/lib/practice-api"
import { cn } from "~/lib/utils"
import { checkViewAsStatus } from "~/lib/view-as-api"
import { requireEnv } from "~/utils/environment"
import { isZoomEnabled } from "~/utils/zoom-feature"

import { invariant } from "@epic-web/invariant"
import { zodResolver } from "@hookform/resolvers/zod"
import { motion } from "motion/react"
import { SiZoom } from "react-icons/si"

type Path = "undecided" | "owner" | "workforce"
type OptionalStep = "choice" | "bio" | "notes" | "zoom" | "allset"

export function canLeaveOnboarding(data: {
  onboarding_completed: boolean
  is_admin: boolean
  practice_ready: boolean
}) {
  return data.onboarding_completed && data.practice_ready
}

export async function clientLoader({ request }: Route.ClientLoaderArgs) {
  const viewAsCheck = await checkViewAsStatus()
  if (viewAsCheck.state !== "ready") {
    return { viewAsCheck }
  }
  if (viewAsCheck.status.active && viewAsCheck.status.diagnostic_code) {
    return {
      accountRecoveryRequired: false as const,
      viewAsCheck,
    }
  }

  const { data, error, response } = await settingsData()
  if (!data && isAccountRecoveryRequired(error, response?.status)) {
    return {
      accountRecoveryRequired: true as const,
      viewAsCheck,
    }
  }
  invariant(data, "settings data failed to load")

  const [practice, organizationAccess] = await Promise.all([
    getPractice(),
    getOrganizationAccessOpportunity(),
  ])
  if (!practice && actionablePendingInvitation()) {
    throw redirect(href("/invite"))
  }

  // The server's effective access decision includes exact-account grants. Do
  // not recompute authorization from the factual practice payload here.
  if (canLeaveOnboarding(data)) {
    throw redirect(href("/notes"))
  }

  const clerk = await getClient()
  const email = clerk?.user?.primaryEmailAddress?.emailAddress ?? null
  const channel = resolveChannel(
    new URL(request.url).searchParams.get("channel"),
  )

  return {
    accountRecoveryRequired: false as const,
    viewAsCheck,
    ...data,
    email,
    channel,
    practice,
    organizationAccess,
  }
}

export default function OnboardingPage({ loaderData }: Route.ComponentProps) {
  const onboardingData =
    "practice" in loaderData && !loaderData.accountRecoveryRequired
      ? loaderData
      : null
  const [practice, setPractice] = useState<PracticeStatus | null>(
    onboardingData?.practice ?? null,
  )
  const zoomEnabled = isZoomEnabled(onboardingData?.email ?? null)

  return (
    <ViewAsRouteGuard
      check={loaderData.viewAsCheck}
      className="bg-background min-h-screen"
    >
      <header className="border-b">
        <div className="mx-auto flex h-16 max-w-5xl items-center justify-between px-5">
          <BrandLogo variant="dark" size="sm" />
          <div className="flex items-center gap-3">
            {onboardingData?.email ? (
              <span className="text-muted-foreground hidden text-sm sm:inline">
                {onboardingData.email}
              </span>
            ) : null}
            <ViewAsAccountControl />
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-5xl px-5 py-10">
        {!onboardingData ? (
          <AccountRecoveryRequired />
        ) : onboardingData.organizationAccess?.state === "claim" ? (
          <OrganizationAccessFlow
            opportunity={onboardingData.organizationAccess}
            onboardingCompleted={onboardingData.onboarding_completed}
          />
        ) : onboardingData.organizationAccess?.state === "pending" ||
          onboardingData.organizationAccess?.state === "support" ||
          (onboardingData.organizationAccess?.state === "ready" &&
            !practiceReady(practice)) ? (
          <ManagedOrganizationAccessNotice
            disposition={onboardingData.organizationAccess}
          />
        ) : practice?.access_revoked ? (
          <OrganizationAccessRevoked practice={practice} />
        ) : practiceReady(practice) ? (
          <OptionalSetup
            biography={onboardingData.biography}
            zoomEnabled={zoomEnabled}
            practice={practice}
          />
        ) : (
          <PracticeFlow
            practice={practice}
            onPractice={setPractice}
            channel={onboardingData.channel}
            email={onboardingData.email}
          />
        )}
      </main>
    </ViewAsRouteGuard>
  )
}

export function AccountRecoveryRequired() {
  return (
    <div className="max-w-xl">
      <StepKicker n="01" label="Account recovery" />
      <DisplayTitle>Your records are safe</DisplayTitle>
      <p className="text-muted-foreground mt-4 leading-relaxed">
        This email is linked to a previous sign-in. Contact Cosmaneura support
        so we can securely reconnect this sign-in to your existing account.
      </p>
      <Button asChild size="lg" className="mt-8">
        <a href="mailto:info@cosmaneura.com">Contact support</a>
      </Button>
    </div>
  )
}

export function OrganizationAccessFlow({
  opportunity,
  onboardingCompleted,
}: {
  opportunity: OrganizationAccessOpportunity
  onboardingCompleted: boolean
}) {
  const [accessCode, setAccessCode] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function onClaim(e: React.FormEvent) {
    e.preventDefault()
    const code = accessCode.trim()
    if (!code) return

    setBusy(true)
    setError(null)
    try {
      const practice = await claimOrganizationAccess(code)
      if (!practiceReady(practice)) {
        throw new Error("Organization access is not active yet.")
      }
      if (!onboardingCompleted) await completeOnboarding()
      window.location.href = href("/notes")
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Could not activate organization access",
      )
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="max-w-xl">
      <StepKicker n="01" label="Organization access" />
      <DisplayTitle>
        Join {opportunity.organization_name ?? "your organization"}
      </DisplayTitle>
      <p className="text-muted-foreground mt-4 leading-relaxed">
        Your verified @{opportunity.email_domain} account is eligible. Enter the
        access code provided by your organization to continue to your Cosmas
        dashboard.
      </p>
      <p className="text-muted-foreground mt-3 text-sm leading-relaxed">
        Your organization already has its agreement with Cosmaneura on file; you
        are not being asked to sign or certify that agreement.
      </p>
      <form onSubmit={(e) => void onClaim(e)} className="mt-8 grid gap-4">
        <Field
          label="Organization access code"
          htmlFor="organization-access-code"
        >
          <Input
            id="organization-access-code"
            type="password"
            value={accessCode}
            onChange={(e) => setAccessCode(e.target.value.toUpperCase())}
            autoCapitalize="characters"
            autoComplete="off"
            spellCheck={false}
            data-sentry-mask
            data-ph-no-capture
            aria-describedby={error ? "organization-access-error" : undefined}
          />
        </Field>
        <Button type="submit" size="lg" disabled={!accessCode.trim() || busy}>
          {busy ? (
            <>
              <Icons.spinner className="mr-2 h-4 w-4 animate-spin" />
              Activating…
            </>
          ) : (
            "Continue to Cosmas"
          )}
        </Button>
      </form>
      {error ? (
        <p
          id="organization-access-error"
          role="alert"
          className="text-destructive mt-4 text-sm"
        >
          {error}
        </p>
      ) : null}
    </div>
  )
}

export function ManagedOrganizationAccessNotice({
  disposition,
}: {
  disposition: OrganizationAccessOpportunity
}) {
  const pending = disposition.state === "pending"

  return (
    <div className="max-w-xl">
      <StepKicker n="01" label="Organization access" />
      <DisplayTitle>
        {pending
          ? "Your organization is setting up access"
          : "We need to review your organization access"}
      </DisplayTitle>
      <p className="text-muted-foreground mt-4 leading-relaxed">
        {pending
          ? "We recognized this as an organization-managed work account, but your individual roster access is not ready yet."
          : "We could not safely match this sign-in to one active organization assignment. Your records remain safe, and no new agreement is required from you here."}
      </p>
      <p className="text-muted-foreground mt-3 text-sm leading-relaxed">
        An email domain identifies the correct setup path, but does not grant
        patient access by itself. Ask your organization administrator to confirm
        your roster assignment, or contact Cosmaneura support.
      </p>
      <Button asChild size="lg" className="mt-8">
        <a href="mailto:info@cosmaneura.com?subject=Organization%20access%20review">
          Contact support
        </a>
      </Button>
    </div>
  )
}

function OrganizationAccessRevoked({ practice }: { practice: PracticeStatus }) {
  return (
    <div className="max-w-xl">
      <StepKicker n="01" label="Organization access" />
      <DisplayTitle>Access is no longer active</DisplayTitle>
      <p className="text-muted-foreground mt-4 leading-relaxed">
        Your access to {practice.legal_name} has been removed. Your existing
        records have not been deleted. Contact your organization administrator
        or Cosmaneura support if this should be reviewed.
      </p>
    </div>
  )
}

function PracticeFlow({
  practice,
  onPractice,
  channel,
  email,
}: {
  practice: PracticeStatus | null
  onPractice: (practice: PracticeStatus) => void
  channel: Channel
  email: string | null
}) {
  const [path, setPath] = useState<Path>(() => {
    if (practice?.role === "owner") return "owner"
    if (practice?.role === "workforce") return "workforce"
    return "undecided"
  })
  const [mismatch, setMismatch] = useState(false)

  function goSolo(wrongDomain = false) {
    setMismatch(wrongDomain)
    setPath("owner")
  }

  if (path === "undecided") {
    return (
      <ChoosePath
        onChoose={setPath}
        onSolo={() => goSolo()}
        channel={channel}
        email={email}
      />
    )
  }
  if (path === "owner") {
    return (
      <OwnerFlow
        practice={practice}
        onPractice={onPractice}
        onBack={() => {
          setMismatch(false)
          setPath("undecided")
        }}
        channel={channel}
        mismatch={mismatch}
        email={email}
      />
    )
  }
  return (
    <WorkforceFlow
      onPractice={onPractice}
      onBack={() => setPath("undecided")}
      onSolo={() => goSolo(true)}
      channel={channel}
    />
  )
}

function ChoosePath({
  onChoose,
  onSolo,
  channel,
  email,
}: {
  onChoose: (path: Path) => void
  onSolo: () => void
  channel: Channel
  email: string | null
}) {
  const mcd = channel === "mcd"
  const mcdBlocked = mcd && !isMcdEmail(email)

  if (mcdBlocked) {
    return (
      <div className="max-w-xl">
        <StepKicker n="01" label="MyCatholicDoctor" />
        <DisplayTitle>This inbox isn’t on the MCD domain</DisplayTitle>
        <p className="text-muted-foreground mt-4">
          You’re signed in as{" "}
          <span className="font-medium text-[var(--brand-navy-deep)]">
            {email}
          </span>
          . Joining or founding a MyCatholicDoctor practice requires an{" "}
          <span className="font-medium text-[var(--brand-navy-deep)]">
            @{MCD_EMAIL_DOMAIN}
          </span>{" "}
          address. Cosmas will not attach you to that network from a personal
          inbox.
        </p>
        <div className="bg-card mt-8 rounded-xl border p-6">
          <p className="text-sm leading-relaxed">
            If you practice independently, start your own Cosmas practice on
            this email. If you are on MCD, sign out (top right) and create the
            account with the address MCD has on file.
          </p>
          <Button type="button" size="lg" className="mt-6" onClick={onSolo}>
            Start my own Cosmas practice
            <ArrowRight className="size-4" />
          </Button>
        </div>
      </div>
    )
  }

  return (
    <div className="max-w-3xl">
      <StepKicker n="01" label={mcd ? "MyCatholicDoctor" : "Identity"} />
      <DisplayTitle>How will this Cosmas account be used?</DisplayTitle>
      <p className="text-muted-foreground mt-4 max-w-xl">
        {mcd
          ? `Same Cosmas. Join only works from an @${MCD_EMAIL_DOMAIN} inbox. The BAA is with Cosmaneura Inc and the MCD practice — not with each clinician.`
          : "The BAA is with Cosmaneura Inc and the practice — not with each clinician. Owners accept once. Invited staff attest under that agreement, using the practice work email."}
      </p>
      <div className="mt-10 grid gap-4 sm:grid-cols-2">
        <PathCard
          icon={<Building2 className="size-5 text-[var(--brand-gold)]" />}
          title={mcd ? "I lead this MCD practice" : "This is my practice"}
          body={
            mcd
              ? "Name the covered entity on the network and accept the Cosmas BAA on its behalf."
              : "Name the covered entity and accept the Cosmas BAA on its behalf."
          }
          onClick={() => onChoose("owner")}
        />
        <PathCard
          icon={<UserRound className="size-5 text-[var(--brand-gold)]" />}
          title={mcd ? "MCD invited me" : "I’ve been invited"}
          body={
            mcd
              ? `Join with a practice code from your @${MCD_EMAIL_DOMAIN} email. You do not sign a second BAA.`
              : "Join with a practice code from the same work-email domain. You do not sign a second BAA."
          }
          onClick={() => onChoose("workforce")}
        />
      </div>
    </div>
  )
}

function PathCard({
  icon,
  title,
  body,
  onClick,
}: {
  icon: React.ReactNode
  title: string
  body: string
  onClick: () => void
}) {
  return (
    <button
      type="button"
      aria-label={title}
      onClick={onClick}
      className="bg-card rounded-xl border p-6 text-left transition-colors hover:border-[var(--brand-navy-deep)]"
    >
      <span className="grid size-10 place-items-center rounded-lg bg-[var(--brand-cream)]">
        {icon}
      </span>
      <h2 className="mt-4 text-lg font-semibold text-[var(--brand-navy-deep)]">
        {title}
      </h2>
      <p className="text-muted-foreground mt-2 text-sm leading-relaxed">
        {body}
      </p>
      <p className="mt-5 inline-flex items-center gap-1 text-sm font-semibold text-[var(--brand-navy-deep)]">
        Continue <ArrowRight className="size-4" />
      </p>
    </button>
  )
}

function OwnerFlow({
  practice,
  onPractice,
  onBack,
  channel,
  mismatch,
  email,
}: {
  practice: PracticeStatus | null
  onPractice: (practice: PracticeStatus) => void
  onBack: () => void
  channel: Channel
  mismatch: boolean
  email: string | null
}) {
  if (!practice) {
    return (
      <PracticeForm
        onPractice={onPractice}
        onBack={onBack}
        channel={channel}
        mismatch={mismatch}
        email={email}
      />
    )
  }
  return (
    <BaaClickwrap
      practice={practice}
      onPractice={onPractice}
      onDecline={onBack}
    />
  )
}

export function PracticeForm({
  onPractice,
  onBack,
  channel,
  mismatch,
  email,
}: {
  onPractice: (practice: PracticeStatus) => void
  onBack: () => void
  channel: Channel
  mismatch: boolean
  email: string | null
}) {
  const mcdOk = channel === "mcd" && isMcdEmail(email)
  const formChannel: Channel = mcdOk
    ? "mcd"
    : channel === "mcd"
      ? "catholic"
      : channel
  const [legalName, setLegalName] = useState("")
  const [city, setCity] = useState("")
  const [region, setRegion] = useState("")
  const [isSolo, setIsSolo] = useState(formChannel !== "mcd")
  const [domainConfirmed, setDomainConfirmed] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const controllingDomain = mcdOk ? MCD_EMAIL_DOMAIN : extractEmailDomain(email)
  const publicEmailDomain = isPublicEmailDomain(controllingDomain)
  // Not `controllingDomain`, which substitutes the MCD domain: the mismatch
  // banner has to report the inbox the caller actually signed in with.
  const signedInDomain = extractEmailDomain(email)
  async function onSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!isSolo && !domainConfirmed) {
      setError(`Confirm @${controllingDomain} before continuing.`)
      return
    }
    setBusy(true)
    setError(null)
    try {
      const next = await createPractice({
        legal_name: legalName,
        city,
        region,
        is_solo: isSolo,
        channel: formChannel,
        pastoral_pack: false,
      })
      onPractice(next)
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Could not save the practice",
      )
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="max-w-xl">
      <BackButton onClick={onBack} />
      {mismatch ? (
        <div className="mt-4 rounded-xl border border-[var(--brand-gold)] bg-[var(--brand-cream)] px-4 py-3 text-sm text-[var(--brand-navy-deep)]">
          <p className="font-medium">That code did not accept this inbox.</p>
          <p className="mt-1 opacity-80">
            Joining requires the work address that practice recognizes
            {signedInDomain ? ` — you’re on @${signedInDomain}` : ""}. Sign in
            with your work email, or name your own covered entity below.
          </p>
        </div>
      ) : channel === "mcd" && !mcdOk ? (
        <div className="mt-4 rounded-xl border border-[var(--brand-gold)] bg-[var(--brand-cream)] px-4 py-3 text-sm text-[var(--brand-navy-deep)]">
          This is a Cosmas practice on your email, not an MCD tenant. The
          conscience and pastoral pack will be available soon.
        </div>
      ) : null}
      <StepKicker n="02" label="Covered entity" />
      <DisplayTitle>Name the practice</DisplayTitle>
      <p className="text-muted-foreground mt-3">
        This legal name appears on the BAA with {COMPANY}.{" "}
        {publicEmailDomain
          ? "This account can create a solo practice."
          : "Solo or group — one agreement either way. Workforce will have to sign in from the same email domain as this account."}
        {formChannel === "mcd"
          ? " MyCatholicDoctor is the network. Cosmas is still the product."
          : ""}
      </p>
      {controllingDomain ? (
        <div className="bg-card mt-5 rounded-lg border px-4 py-3 text-sm">
          <span className="font-medium text-[var(--brand-navy-deep)]">
            @{controllingDomain}
          </span>{" "}
          will be the controlling email domain for this practice.
        </div>
      ) : null}
      <form onSubmit={(e) => void onSubmit(e)} className="mt-8 grid gap-5">
        <Field label="Legal name of the covered entity" htmlFor="legal">
          <Input
            id="legal"
            required
            minLength={3}
            value={legalName}
            onChange={(e) => setLegalName(e.target.value)}
            placeholder={
              formChannel === "mcd"
                ? "MyCatholicDoctor — your city"
                : "Riverside Family Practice, PLLC"
            }
          />
        </Field>
        <div className="grid gap-5 sm:grid-cols-2">
          <Field label="City" htmlFor="city">
            <Input
              id="city"
              value={city}
              onChange={(e) => setCity(e.target.value)}
            />
          </Field>
          <Field label="State" htmlFor="region">
            <Input
              id="region"
              value={region}
              onChange={(e) => setRegion(e.target.value)}
            />
          </Field>
        </div>
        <fieldset className="grid gap-2">
          <legend className="text-sm font-medium">Who practices here?</legend>
          <label className={choiceClass(isSolo)}>
            <input
              type="radio"
              className="sr-only"
              checked={isSolo}
              onChange={() => {
                setIsSolo(true)
                setDomainConfirmed(false)
              }}
            />
            Solo — I am the only clinician on this BAA
          </label>
          <label
            className={cn(
              choiceClass(!isSolo),
              publicEmailDomain && "cursor-not-allowed opacity-60",
            )}
          >
            <input
              type="radio"
              className="sr-only"
              checked={!isSolo}
              disabled={publicEmailDomain}
              onChange={() => setIsSolo(false)}
            />
            Group — I will invite workforce under this same BAA
          </label>
          {publicEmailDomain ? (
            <p className="text-muted-foreground mt-1 text-xs">
              Shared email domains such as @{controllingDomain} can create a
              solo practice. Use a practice-owned work email to invite a group.
            </p>
          ) : null}
        </fieldset>
        {!isSolo && controllingDomain ? (
          <label className="bg-card flex gap-3 rounded-lg border px-4 py-3 text-sm leading-snug">
            <input
              type="checkbox"
              className="mt-0.5 size-4 accent-[var(--brand-navy-deep)]"
              checked={domainConfirmed}
              onChange={(e) => setDomainConfirmed(e.target.checked)}
            />
            <span>
              I confirm that @{controllingDomain} is the practice’s work email
              domain. Clinicians using another domain will not be able to join.
            </span>
          </label>
        ) : null}
        <label className="bg-muted/40 flex gap-3 rounded-lg border px-4 py-3 text-sm leading-snug">
          <input
            type="checkbox"
            disabled
            className="mt-0.5 size-4 accent-[var(--brand-navy-deep)]"
          />
          <span>
            <span className="font-medium">Coming soon:</span> the conscience and
            pastoral pack, including NFP terminology and parish correspondence
            under per-event approval.
          </span>
        </label>
        {error ? <p className="text-destructive text-sm">{error}</p> : null}
        <Button
          type="submit"
          disabled={busy || (!isSolo && !domainConfirmed)}
          size="lg"
        >
          {busy ? "Saving…" : "Continue to the BAA"}
        </Button>
      </form>
    </div>
  )
}

export function BaaClickwrap({
  practice,
  onPractice,
  onDecline,
}: {
  practice: PracticeStatus
  onPractice: (practice: PracticeStatus) => void
  onDecline: () => void
}) {
  const [doc, setDoc] = useState<BaaDocument | null>(null)
  const [docError, setDocError] = useState<string | null>(null)
  const [agreed, setAgreed] = useState(false)
  const [signatureName, setSignatureName] = useState("")
  const [signatureTitle, setSignatureTitle] = useState("Physician / owner")
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let cancelled = false
    getBaaCurrent()
      .then((next) => {
        if (cancelled) return
        if (next.doc_id !== BAA_DOC_ID || next.version !== BAA_VERSION) {
          setDocError(
            "The agreement version could not be verified. Reload before accepting.",
          )
          return
        }
        setDoc(next)
      })
      .catch((err) => {
        if (!cancelled)
          setDocError(
            err instanceof Error ? err.message : "Could not load the agreement",
          )
      })
    return () => {
      cancelled = true
    }
  }, [])

  const ready =
    agreed &&
    doc !== null &&
    signatureName.trim().length >= 2 &&
    signatureTitle.trim().length >= 2

  async function onAccept() {
    if (doc === null) return
    setBusy(true)
    setError(null)
    try {
      const next = await acceptPracticeBaa({
        doc_id: doc.doc_id,
        version: doc.version,
        sha256: doc.sha256,
        signature_name: signatureName,
        signature_title: signatureTitle,
        checks: ["baa"],
      })
      onPractice(next)
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not accept the BAA")
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="grid gap-8 lg:grid-cols-[1fr_22rem]">
      <div>
        <StepKicker n="03" label="Agreement" />
        <DisplayTitle>Business Associate Agreement</DisplayTitle>
        <p className="text-muted-foreground mt-3">
          Covered entity:{" "}
          <span className="font-medium text-[var(--brand-navy-deep)]">
            {practice.legal_name}
          </span>
          . Business associate: {COMPANY}. {BAA_DOC_ID} v{BAA_VERSION} —
          approved by counsel.{" "}
          <a
            href={BAA_PDF_URL}
            target="_blank"
            rel="noreferrer"
            className="underline underline-offset-2"
          >
            Download PDF
          </a>
        </p>
        <ScrollArea className="cn-baa-container bg-card mt-6 h-[37.5rem] rounded-xl border p-5">
          {doc ? (
            // canonical, hash-verified legal text served by the backend —
            // rendered verbatim, styled only from outside (.cn-baa)
            <div dangerouslySetInnerHTML={{ __html: doc.html }} />
          ) : docError ? (
            <p className="text-destructive text-sm">{docError}</p>
          ) : (
            <p className="text-muted-foreground text-sm">
              Loading the agreement…
            </p>
          )}
        </ScrollArea>
      </div>
      <aside className="bg-card h-fit rounded-xl border p-5">
        <p className="text-xs font-semibold tracking-[0.18em] text-[var(--brand-gold)] uppercase">
          Acceptance
        </p>
        <div className="mt-4 grid gap-3">
          <Field label="Accepting individual’s name" htmlFor="sig">
            <Input
              id="sig"
              value={signatureName}
              onChange={(e) => setSignatureName(e.target.value)}
              placeholder="Full legal name"
            />
          </Field>
          <Field label="Title" htmlFor="title">
            <Input
              id="title"
              value={signatureTitle}
              onChange={(e) => setSignatureTitle(e.target.value)}
            />
          </Field>
        </div>
        <div className="mt-4">
          <CheckRow checked={agreed} onChange={setAgreed}>
            {baaCheckboxLabel(practice.legal_name)}
          </CheckRow>
        </div>
        {error ? (
          <p className="text-destructive mt-3 text-sm">{error}</p>
        ) : null}
        <p className="text-muted-foreground mt-4 text-xs leading-snug">
          {baaAuthorityLine(practice.legal_name)}
        </p>
        <Button
          type="button"
          disabled={!ready || busy}
          onClick={() => void onAccept()}
          className="mt-2 w-full"
          size="lg"
        >
          {busy ? "Recording…" : "I Agree"}
        </Button>
        <button
          type="button"
          onClick={onDecline}
          className="text-muted-foreground hover:text-foreground mt-3 w-full text-center text-sm"
        >
          I’m not authorized to sign this
        </button>
        <p className="text-muted-foreground mt-2 text-xs leading-snug">
          Not the right person? Ask the practice owner or an authorized
          representative to sign in and accept — nothing is recorded until they
          do.
        </p>
      </aside>
    </div>
  )
}

function WorkforceFlow({
  onPractice,
  onBack,
  onSolo,
  channel,
}: {
  onPractice: (practice: PracticeStatus) => void
  onBack: () => void
  onSolo: () => void
  channel: Channel
}) {
  const [code, setCode] = useState("")
  const [found, setFound] = useState<PracticeStatus | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function onLookup(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    setFound(null)
    try {
      const res = await lookupPractice(code)
      if (res.mismatch) {
        onSolo()
        return
      }
      if (!res.practice) {
        setError("No practice matches that code.")
        return
      }
      setFound(res.practice)
    } catch (err) {
      setError(err instanceof Error ? err.message : "Lookup failed")
    } finally {
      setBusy(false)
    }
  }

  async function onJoin(attestation: WorkforceAttestationSubmission) {
    if (!found?.join_code) return
    setBusy(true)
    setError(null)
    try {
      const next = await joinPractice({
        join_code: found.join_code,
        ...attestation,
      })
      onPractice(next)
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not join")
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="max-w-xl">
      <BackButton onClick={onBack} />
      <StepKicker n="02" label="Workforce" />
      <DisplayTitle>Join a practice</DisplayTitle>
      <p className="text-muted-foreground mt-3">
        You attest under the practice’s BAA with {COMPANY}. The join code is not
        enough — this account’s email must be on the practice domain. You do not
        become a separate party.
      </p>
      <form onSubmit={(e) => void onLookup(e)} className="mt-8 flex gap-2">
        <Input
          value={code}
          onChange={(e) => setCode(e.target.value.toUpperCase())}
          placeholder="Join code"
          aria-label="Practice join code"
        />
        <Button type="submit" disabled={busy}>
          Find
        </Button>
      </form>
      <p className="text-muted-foreground mt-2 text-xs">
        {channel === "mcd"
          ? `Join only works from an @${MCD_EMAIL_DOMAIN} inbox that matches the practice.`
          : "Ask the practice owner for the join code. Your work email must match their domain."}
      </p>
      {found ? (
        <div className="bg-card mt-6 rounded-xl border p-5">
          <p className="text-xs font-semibold tracking-[0.18em] text-[var(--brand-gold)] uppercase">
            Covered entity
          </p>
          <p
            className="mt-1 text-2xl"
            style={{ fontFamily: "'Cormorant Garamond', serif" }}
          >
            {found.legal_name}
          </p>
          <p className="text-muted-foreground mt-1 text-sm">
            @{found.email_domain}
            {found.baa_accepted ? " · BAA on file" : " · BAA not yet accepted"}
          </p>
          {!found.baa_accepted ? (
            <p className="text-destructive mt-4 text-sm">
              This practice has not accepted the {COMPANY} BAA. Capture stays
              locked.
            </p>
          ) : (
            <WorkforceAttestation
              legalName={found.legal_name}
              busy={busy}
              onAttest={(attestation) => void onJoin(attestation)}
            />
          )}
        </div>
      ) : null}
      {error ? <p className="text-destructive mt-4 text-sm">{error}</p> : null}
    </div>
  )
}

function OptionalSetup({
  biography,
  zoomEnabled,
  practice,
}: {
  biography: string | null
  zoomEnabled: boolean
  practice: PracticeStatus
}) {
  const [step, setStep] = useState<OptionalStep>("choice")

  if (step === "choice") {
    return (
      <div className="mx-auto flex max-w-lg flex-col items-center gap-6 text-center">
        <StepKicker n="04" label="Optional" />
        <DisplayTitle>Optional setup</DisplayTitle>
        <p className="text-muted-foreground max-w-md text-base">
          The Cosmaneura Inc BAA is in force. Biography, an example note, and
          Zoom can wait — they do not block Cosmas.
        </p>
        <div className="flex w-full flex-col gap-3 sm:flex-row sm:justify-center">
          <Button size="lg" onClick={() => setStep("allset")}>
            Start using Cosmas
          </Button>
          <Button size="lg" variant="outline" onClick={() => setStep("bio")}>
            Set these up now
          </Button>
        </div>
      </div>
    )
  }
  if (step === "bio") {
    return (
      <BiographyStep
        biography={biography}
        onComplete={() => setStep("notes")}
        onSkip={() => setStep("notes")}
      />
    )
  }
  if (step === "notes") {
    return (
      <NotesStep
        onComplete={() => setStep("zoom")}
        onSkip={() => setStep("zoom")}
      />
    )
  }
  if (step === "zoom") {
    return (
      <ZoomStep
        onComplete={() => setStep("allset")}
        zoomEnabled={zoomEnabled}
      />
    )
  }
  return <AllSetStep practice={practice} />
}

function BiographyStep({
  biography,
  onComplete,
  onSkip,
}: {
  biography: string | null
  onComplete: () => void
  onSkip: () => void
}) {
  const formSchema = z.object({
    biography: z.string().min(1, "Biography is required"),
  })

  const form = useForm({
    resolver: zodResolver(formSchema),
    defaultValues: { biography: biography ?? "" },
  })

  async function onSubmit(values: z.infer<typeof formSchema>) {
    await updateSettingsData({ body: values })
    onComplete()
  }

  return (
    <div className="mx-auto flex max-w-lg flex-col items-center gap-6 text-center">
      <div className="space-y-1">
        <h2 className="text-2xl font-semibold">Tell us about yourself</h2>
        <p className="text-muted-foreground text-sm">
          This helps Cosmas personalize your patient intake and note generation.
          You can skip this and add it later in Settings.
        </p>
      </div>
      <Form {...form}>
        <form
          onSubmit={form.handleSubmit(onSubmit)}
          className="w-full space-y-4"
        >
          <FormField
            control={form.control}
            name="biography"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Biography</FormLabel>
                <FormControl>
                  <textarea
                    className="w-full rounded-md border p-2"
                    rows={5}
                    placeholder="I provide comprehensive dermatologic care for patients of all ages, focusing on medical, surgical, and cosmetic dermatology..."
                    {...field}
                  />
                </FormControl>
                <FormDescription>
                  Describe your specialty, focus areas, and approach to care.
                </FormDescription>
                <FormMessage />
              </FormItem>
            )}
          />
          <Button
            type="submit"
            className="w-full"
            disabled={form.formState.isSubmitting}
          >
            {form.formState.isSubmitting ? (
              <>
                <Icons.spinner className="mr-2 h-4 w-4 animate-spin" />
                Saving...
              </>
            ) : (
              "Save & Continue"
            )}
          </Button>
          <Button
            type="button"
            variant="ghost"
            className="w-full"
            onClick={onSkip}
          >
            Skip for now
          </Button>
        </form>
      </Form>
    </div>
  )
}

const PREDEFINED_NOTE_TYPES = [
  "Telehealth SOAP Note",
  "Dermatology Office Visit",
  "Annual Wellness Visit",
  "Psychiatry Follow-up Note",
  "Urgent Care Visit",
  "Post-Op Follow-up",
  "Cardiology Consultation",
  "Pediatric Well Visit",
  "Pain Management Note",
  "Physical Therapy Evaluation",
]

function NotesStep({
  onComplete,
  onSkip,
}: {
  onComplete: () => void
  onSkip: () => void
}) {
  const formSchema = z.object({ note_type: z.string().min(1) })
  const [file, setFile] = useState<File | null>(null)

  const form = useForm({ resolver: zodResolver(formSchema) })

  async function onSubmit(values: z.infer<typeof formSchema>) {
    invariant(file, "a file must be selected before submitting")

    const uploadResult = await submitExampleNote(file, values.note_type)

    if (!uploadResult.ok) {
      toast({
        ...exampleNoteUploadErrorCopy(uploadResult.stage),
        variant: "destructive",
      })
      return
    }

    setFile(null)
    toast({
      title: "Note uploaded",
      description:
        "Your example note has been received and will be processed shortly.",
    })
    onComplete()
  }

  return (
    <div className="mx-auto flex max-w-lg flex-col items-center gap-6 text-center">
      <div className="space-y-1">
        <h2 className="text-2xl font-semibold">Upload an example note</h2>
        <p className="text-muted-foreground text-sm">
          Optional. Upload a high-quality example of a visit note you've written
          so Cosmas can learn your style.
        </p>
      </div>
      <Form {...form}>
        <form
          onSubmit={form.handleSubmit(onSubmit)}
          className="w-full space-y-4"
        >
          <ImageDropzone onDropFile={setFile}>
            {!file ? (
              <div className="flex items-center gap-4 font-semibold">
                <p>Upload Example Visit Note</p>
                <p className="text-muted-foreground text-xs">
                  Click or drag a PDF (10MB or smaller)
                </p>
              </div>
            ) : (
              <div className="flex flex-col gap-1 font-semibold">
                <p>{file.name}</p>
                <p className="text-muted-foreground text-xs">
                  Press Upload to continue
                </p>
              </div>
            )}
          </ImageDropzone>
          <FormField
            control={form.control}
            name="note_type"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Note Type</FormLabel>
                <FormControl>
                  <SimpleComboBox
                    items={PREDEFINED_NOTE_TYPES.map((label) => ({
                      value: label,
                      label,
                    }))}
                    onSelectedItemChange={(item) =>
                      item && field.onChange(item.label)
                    }
                    {...field}
                  />
                </FormControl>
                <FormDescription>
                  Select the type of note you're uploading.
                </FormDescription>
                <FormMessage />
              </FormItem>
            )}
          />
          <Button
            type="submit"
            className="w-full"
            disabled={!file || form.formState.isSubmitting}
          >
            {form.formState.isSubmitting ? (
              <>
                <Icons.spinner className="mr-2 h-4 w-4 animate-spin" />
                Uploading...
              </>
            ) : (
              "Upload & Continue"
            )}
          </Button>
          <Button
            type="button"
            variant="ghost"
            className="w-full"
            onClick={onSkip}
          >
            Skip for now
          </Button>
        </form>
      </Form>
    </div>
  )
}

function ZoomStep({
  onComplete,
  zoomEnabled,
}: {
  onComplete: () => void
  zoomEnabled: boolean
}) {
  const [loading, setLoading] = useState(false)

  async function connectZoom() {
    setLoading(true)
    const clerk = await getClient()
    const token = await clerk!.session!.getToken()
    const base = requireEnv("VITE_PYTHON_URL").replace(/\/$/, "")
    const res = await internalApiFetch(`${base}/internal/v1/zoom-auth-url`, {
      headers: { Authorization: `Bearer ${token}` },
      credentials: "include",
    })
    const { url } = (await res.json()) as { url: string }
    if (url) window.location.href = url
    setLoading(false)
  }

  if (!zoomEnabled) {
    return (
      <div className="mx-auto flex max-w-lg flex-col items-center gap-6 text-center">
        <div className="space-y-1">
          <div className="flex items-center justify-center gap-2">
            <h2 className="text-2xl font-semibold">Connect Zoom</h2>
            <span className="bg-muted text-muted-foreground rounded-full px-2 py-0.5 text-xs font-medium">
              Coming Soon
            </span>
          </div>
          <p className="text-muted-foreground text-sm">
            Zoom integration is coming soon. You can continue without it.
          </p>
        </div>
        <Button onClick={onComplete}>Continue</Button>
      </div>
    )
  }

  return (
    <div className="mx-auto flex max-w-lg flex-col items-center gap-6 text-center">
      <div className="space-y-1">
        <h2 className="text-2xl font-semibold">Connect Zoom</h2>
        <p className="text-muted-foreground text-sm">
          Optional. Connect your Zoom account so Cosmas can join meetings and
          generate visit notes after each session.
        </p>
      </div>
      <Button onClick={connectZoom} disabled={loading} size="lg">
        {loading ? (
          <>
            <Icons.spinner className="mr-2 h-4 w-4 animate-spin" />
            Connecting...
          </>
        ) : (
          <>
            <span className="mr-2 flex h-6 w-6 items-center justify-center rounded-sm border border-white">
              <SiZoom className="size-4 text-white" />
            </span>
            Connect Zoom
          </>
        )}
      </Button>
      <Button type="button" variant="ghost" onClick={onComplete}>
        Skip for now
      </Button>
    </div>
  )
}

function AllSetStep({ practice }: { practice: PracticeStatus }) {
  const [loading, setLoading] = useState(false)

  async function handleGoToCosmas() {
    setLoading(true)
    await completeOnboarding()
    window.location.href = href("/notes")
  }

  return (
    <div className="mx-auto flex max-w-lg flex-col items-center gap-6 text-center">
      <motion.div
        initial={{ scale: 0, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        transition={{ type: "spring", stiffness: 200, damping: 15, delay: 0.1 }}
        className="relative flex size-24 items-center justify-center rounded-full"
        style={{ background: "var(--brand-navy-deep)" }}
      >
        <motion.span
          initial={{ scale: 0.8, opacity: 0.6 }}
          animate={{ scale: 1.6, opacity: 0 }}
          transition={{ duration: 1.2, repeat: Infinity, ease: "easeOut" }}
          className="absolute inset-0 rounded-full"
          style={{ background: "var(--brand-navy-deep)" }}
        />
        <svg
          viewBox="0 0 52 52"
          className="size-12"
          fill="none"
          stroke="var(--brand-gold)"
          strokeWidth="4"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <motion.path
            d="M14 27 L23 36 L38 18"
            initial={{ pathLength: 0 }}
            animate={{ pathLength: 1 }}
            transition={{ duration: 0.5, delay: 0.4, ease: "easeOut" }}
          />
        </svg>
      </motion.div>

      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.7, duration: 0.4 }}
        className="flex w-full flex-col items-center gap-3"
      >
        <h2 className="text-3xl font-bold">You're all set!</h2>
        <p className="text-muted-foreground max-w-md text-base">
          Your practice is on the Cosmaneura Inc BAA. Cosmas is ready.
        </p>
        <PracticeJoinPanel practice={practice} />
        <Button
          size="lg"
          onClick={handleGoToCosmas}
          disabled={loading}
          className="mt-2"
        >
          {loading ? (
            <>
              <Icons.spinner className="mr-2 h-4 w-4 animate-spin" />
              Loading...
            </>
          ) : (
            "Go to Cosmas"
          )}
        </Button>
      </motion.div>
    </div>
  )
}

function StepKicker({ n, label }: { n: string; label: string }) {
  return (
    <p className="text-xs font-semibold tracking-[0.28em] text-[var(--brand-gold)] uppercase">
      {n} · {label}
    </p>
  )
}

function DisplayTitle({ children }: { children: React.ReactNode }) {
  return (
    <h1
      className="mt-3 text-4xl font-semibold text-[var(--brand-navy-deep)] sm:text-5xl"
      style={{ fontFamily: "'Cormorant Garamond', serif" }}
    >
      {children}
    </h1>
  )
}

function BackButton({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="text-muted-foreground hover:text-foreground text-sm"
    >
      Back
    </button>
  )
}

function choiceClass(active: boolean) {
  return cn(
    "flex min-h-11 cursor-pointer items-center rounded-lg border px-4 text-sm",
    active
      ? "border-[var(--brand-navy-deep)] bg-[var(--brand-cream)]"
      : "bg-card",
  )
}
