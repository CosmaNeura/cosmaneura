import { useCallback, useEffect, useState } from "react"
import { useForm } from "react-hook-form"
import { href, redirect } from "react-router"
import { z } from "zod"

import { ArrowRight, Building2, UserRound } from "lucide-react"

import type { Route } from "./+types/onboarding"

import {
  PublicCheckoutStep,
  subscriptionStatusLabel,
  trialEndCopy,
} from "~/components/PublicCheckoutStep"
import { ViewAsAccountControl } from "~/components/ViewAsAccountControl"
import { ViewAsRouteGuard } from "~/components/ViewAsRouteGuard"
import { BrandLogo } from "~/components/brand/BrandLogo"
import { Icons } from "~/components/icons"
import { ImageDropzone } from "~/components/shared/ImageDropzone"
import { PracticeJoinPanel } from "~/components/shared/PracticeJoinPanel"
import { SimpleComboBox } from "~/components/shared/SimpleComboBox"
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
import { Label } from "~/components/ui/label"
import { ScrollArea } from "~/components/ui/scroll-area"
import { getClient, getSessionToken } from "~/configuration/clerk"
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
  WORKFORCE_CHECKS,
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
import {
  type BaaDocument,
  type JoinMismatch,
  type OrganizationAccessOpportunity,
  type PracticeStatus,
  type PublicCapabilityEntitlement,
  type PublicLegalDocuments,
  type PublicOnboardingState,
  type PublicPracticeInvitation,
  type PublicPracticeMember,
  acceptPracticeBaa,
  acceptPracticeLegal,
  acceptPublicPracticeInvitation,
  claimOrganizationAccess,
  createPractice,
  createPublicPracticeInvitation,
  deactivatePublicPracticeMember,
  getBaaCurrent,
  getCurrentLegalDocuments,
  getOrganizationAccessOpportunity,
  getPractice,
  getPublicCapabilities,
  getPublicOnboarding,
  getPublicPracticeInvitations,
  getPublicPracticeMembers,
  joinPractice,
  lookupPractice,
  practiceReady,
  reactivatePublicPracticeMember,
  revokePublicPracticeInvitation,
} from "~/lib/practice-api"
import {
  PUBLIC_INVITATION_TOKEN_PATTERN,
  type PublicInvitationLink,
  consumePublicInvitationLink,
  finishPublicInvitationFlow,
} from "~/lib/public-invitation-link"
import {
  type PendingPublicInvitationMutation,
  PublicInvitationMutationJournalError,
  discardCorruptPublicInvitationMutation,
  discardPublicInvitationMutation,
  loadPublicInvitationMutation,
  publicInvitationMutationScope,
  publicInvitationMutationStorageKey,
  reservePublicInvitationMutation,
} from "~/lib/public-invitation-mutation"
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

export { consumePublicInvitationLink, finishPublicInvitationFlow }
export { PublicCheckoutStep }

export function canLeaveOnboarding(data: {
  onboarding_completed: boolean
  is_admin: boolean
  practice_ready: boolean
}) {
  return data.onboarding_completed && data.practice_ready
}

export function shouldLeaveOnboarding(
  data: Parameters<typeof canLeaveOnboarding>[0],
  invitation: PublicInvitationLink,
) {
  return canLeaveOnboarding(data) && invitation.token === null
}

export async function clientLoader({ request }: Route.ClientLoaderArgs) {
  const publicInvitation = consumePublicInvitationLink()
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

  const [practice, organizationAccess, publicOnboarding] = await Promise.all([
    getPractice(),
    getOrganizationAccessOpportunity(),
    getPublicOnboarding(),
  ])

  let legalDocuments: PublicLegalDocuments | null = null
  let legalDocumentsError: string | null = null
  let capabilities: PublicCapabilityEntitlement | null = null
  let capabilitiesError: string | null = null

  if (!publicOnboarding.managed_path) {
    const [legalResult, capabilitiesResult] = await Promise.allSettled([
      getCurrentLegalDocuments(),
      publicOnboarding.practice
        ? getPublicCapabilities()
        : Promise.resolve(null),
    ])
    if (legalResult.status === "fulfilled") {
      legalDocuments = legalResult.value
    } else {
      legalDocumentsError =
        legalResult.reason instanceof Error
          ? legalResult.reason.message
          : "Current legal documents are unavailable."
    }
    if (capabilitiesResult.status === "fulfilled") {
      capabilities = capabilitiesResult.value
    } else {
      capabilitiesError =
        capabilitiesResult.reason instanceof Error
          ? capabilitiesResult.reason.message
          : "Capability status is unavailable."
    }
  }

  // The server's effective access decision includes exact-account grants. Do
  // not recompute authorization from the factual practice payload here.
  if (shouldLeaveOnboarding(data, publicInvitation)) {
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
    publicOnboarding,
    legalDocuments,
    legalDocumentsError,
    capabilities,
    capabilitiesError,
    publicInvitation,
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
  const [publicOnboarding, setPublicOnboarding] =
    useState<PublicOnboardingState | null>(
      onboardingData?.publicOnboarding ?? null,
    )
  const [capabilities, setCapabilities] =
    useState<PublicCapabilityEntitlement | null>(
      onboardingData?.capabilities ?? null,
    )
  const [publicStateError, setPublicStateError] = useState<string | null>(null)
  const [capabilitiesError, setCapabilitiesError] = useState<string | null>(
    onboardingData?.capabilitiesError ?? null,
  )
  const zoomEnabled = isZoomEnabled(onboardingData?.email ?? null)

  async function refreshPublicState() {
    setPublicStateError(null)
    try {
      const next = await getPublicOnboarding()
      setPublicOnboarding(next)
      if (next.practice && !next.managed_path) {
        try {
          setCapabilities(await getPublicCapabilities())
          setCapabilitiesError(null)
        } catch (err) {
          setCapabilities(null)
          setCapabilitiesError(
            err instanceof Error
              ? err.message
              : "Capability status is unavailable.",
          )
        }
      }
    } catch (err) {
      setPublicStateError(
        err instanceof Error
          ? err.message
          : "Onboarding status is unavailable.",
      )
    }
  }

  function handlePractice(next: PracticeStatus) {
    setPractice(next)
    if (publicOnboarding && !publicOnboarding.managed_path) {
      void refreshPublicState()
    }
  }

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
        ) : onboardingData.publicInvitation.token && practice ? (
          <PublicInvitationAccountMismatch
            practiceName={practice.legal_name}
            invalidInvitation={false}
            onAbandon={() => finishPublicInvitationFlow()}
          />
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
        ) : publicOnboarding && !publicOnboarding.managed_path ? (
          <PublicSelfServiceFlow
            state={publicOnboarding}
            practice={practice}
            onPractice={handlePractice}
            channel={onboardingData.channel}
            email={onboardingData.email}
            biography={onboardingData.biography}
            zoomEnabled={zoomEnabled}
            legalDocuments={onboardingData.legalDocuments}
            legalDocumentsError={onboardingData.legalDocumentsError}
            capabilities={capabilities}
            capabilitiesError={capabilitiesError}
            refreshError={publicStateError}
            onState={setPublicOnboarding}
            onRefresh={refreshPublicState}
            invitation={onboardingData.publicInvitation}
          />
        ) : practiceReady(practice) ? (
          <OptionalSetup
            biography={onboardingData.biography}
            zoomEnabled={zoomEnabled}
            practice={practice}
          />
        ) : (
          <PracticeFlow
            practice={practice}
            onPractice={handlePractice}
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

export function publicOnboardingCanUsePhi(state: PublicOnboardingState) {
  return (
    state.credential_state === "approved" && state.phi_access_allowed === true
  )
}

function PublicSelfServiceFlow({
  state,
  practice,
  onPractice,
  channel,
  email,
  biography,
  zoomEnabled,
  legalDocuments,
  legalDocumentsError,
  capabilities,
  capabilitiesError,
  refreshError,
  onState,
  onRefresh,
  invitation,
}: {
  state: PublicOnboardingState
  practice: PracticeStatus | null
  onPractice: (practice: PracticeStatus) => void
  channel: Channel
  email: string | null
  biography: string | null
  zoomEnabled: boolean
  legalDocuments: PublicLegalDocuments | null
  legalDocumentsError: string | null
  capabilities: PublicCapabilityEntitlement | null
  capabilitiesError: string | null
  refreshError: string | null
  onState: (state: PublicOnboardingState) => void
  onRefresh: () => Promise<void>
  invitation: PublicInvitationLink
}) {
  if (!practice) {
    if (invitation.token || invitation.invalid) {
      return (
        <PublicInvitationAcceptance
          initialToken={invitation.token}
          invalidInitialToken={invitation.invalid}
          onAbandon={() => finishPublicInvitationFlow()}
        />
      )
    }
    return (
      <PracticeFlow
        practice={practice}
        onPractice={onPractice}
        channel={channel}
        email={email}
        publicInvitationMode
      />
    )
  }

  if (invitation.token || invitation.invalid) {
    return (
      <PublicInvitationAccountMismatch
        practiceName={practice.legal_name}
        invalidInvitation={invitation.invalid}
        onAbandon={() => finishPublicInvitationFlow()}
      />
    )
  }

  const owner = state.practice?.role === "owner"
  const workforce = state.practice?.role === "workforce"
  const legalComplete = state.terms_accepted && state.privacy_accepted
  const team = owner ? (
    <PublicPracticeTeam
      practiceName={practice.legal_name}
      ownerEmail={email}
      capabilities={capabilities}
      onEntitlementsRefresh={onRefresh}
    />
  ) : null

  if (workforce && (!state.baa_accepted || !legalComplete)) {
    return (
      <PublicOwnerSetupPending
        practiceName={practice.legal_name}
        currentStep={state.current_step}
        refreshError={refreshError}
        onRefresh={onRefresh}
      />
    )
  }

  if (!practice.baa_accepted && !state.baa_accepted) {
    return (
      <PracticeFlow
        practice={practice}
        onPractice={onPractice}
        channel={channel}
        email={email}
      />
    )
  }

  if (
    state.current_step === "legal" ||
    !state.terms_accepted ||
    !state.privacy_accepted
  ) {
    return (
      <PublicLegalAcceptance
        documents={legalDocuments}
        loadError={legalDocumentsError}
        practiceName={practice.legal_name}
        onAccepted={onState}
      />
    )
  }

  if (state.current_step === "checkout" || !state.checkout_started) {
    return (
      <>
        <PublicCheckoutStep state={state} />
        {team}
      </>
    )
  }

  if (!publicOnboardingCanUsePhi(state)) {
    return (
      <>
        <PublicCredentialStatus
          state={state}
          capabilities={capabilities}
          capabilitiesError={capabilitiesError}
          refreshError={refreshError}
          onRefresh={onRefresh}
        />
        {team}
      </>
    )
  }

  return (
    <>
      <OptionalSetup
        biography={biography}
        zoomEnabled={zoomEnabled}
        practice={practice}
      />
      {team}
    </>
  )
}

export function PublicInvitationAcceptance({
  initialToken,
  invalidInitialToken = false,
  onBack,
  onAbandon,
  onUseLegacyJoinCode,
}: {
  initialToken: string | null
  invalidInitialToken?: boolean
  onBack?: () => void
  onAbandon?: () => void
  onUseLegacyJoinCode?: () => void
}) {
  const [token, setToken] = useState(initialToken ?? "")
  const [checks, setChecks] = useState<Record<string, boolean>>({})
  const [name, setName] = useState("")
  const [error, setError] = useState<string | null>(
    invalidInitialToken
      ? "This invitation link is malformed. Ask the practice owner for a new link."
      : null,
  )
  const [busy, setBusy] = useState(false)
  const normalizedToken = token.trim()
  const ready =
    PUBLIC_INVITATION_TOKEN_PATTERN.test(normalizedToken) &&
    name.trim().length >= 2 &&
    WORKFORCE_CHECKS.every((item) => checks[item.id])

  async function acceptInvitation() {
    if (!ready) return
    setBusy(true)
    setError(null)
    try {
      await acceptPublicPracticeInvitation({
        token: normalizedToken,
        attestation_name: name.trim(),
        checks: WORKFORCE_CHECKS.map((item) => item.id).filter(
          (id) => checks[id],
        ),
      })
      // Replace, rather than append to, browser history so Back cannot reveal
      // the consumed invitation bearer fragment.
      finishPublicInvitationFlow()
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "This invitation could not be accepted.",
      )
      setBusy(false)
    }
  }

  return (
    <div className="max-w-xl">
      {onBack ? <BackButton onClick={onBack} /> : null}
      <StepKicker n="02" label="Practice invitation" />
      <DisplayTitle>Join your practice securely</DisplayTitle>
      <p className="text-muted-foreground mt-4 leading-relaxed">
        Sign in with the exact verified email that the practice owner invited.
        The invitation is single-practice and cannot be used as a reusable join
        code.
      </p>
      <div className="bg-card mt-8 grid gap-5 rounded-xl border p-6">
        <Field label="Invitation token" htmlFor="public-invitation-token">
          <Input
            id="public-invitation-token"
            type="password"
            value={token}
            onChange={(event) => {
              setToken(event.target.value)
              setError(null)
            }}
            autoComplete="off"
            spellCheck={false}
            data-sentry-mask
            data-ph-no-capture
          />
        </Field>
        {WORKFORCE_CHECKS.map((item) => (
          <CheckRow
            key={item.id}
            checked={Boolean(checks[item.id])}
            onChange={(next) =>
              setChecks((previous) => ({ ...previous, [item.id]: next }))
            }
          >
            {item.label}
          </CheckRow>
        ))}
        <Field label="Typed name" htmlFor="public-invitation-attestation">
          <Input
            id="public-invitation-attestation"
            value={name}
            onChange={(event) => setName(event.target.value)}
            autoComplete="name"
          />
        </Field>
        {error ? (
          <p role="alert" className="text-destructive text-sm">
            {error}
          </p>
        ) : null}
        <Button
          type="button"
          size="lg"
          disabled={!ready || busy}
          onClick={() => void acceptInvitation()}
        >
          {busy ? "Joining…" : "Attest and join practice"}
        </Button>
      </div>
      {onUseLegacyJoinCode ? (
        <button
          type="button"
          className="text-muted-foreground hover:text-foreground mt-5 text-sm underline underline-offset-2"
          onClick={onUseLegacyJoinCode}
        >
          Use a legacy practice join code
        </button>
      ) : null}
      {onAbandon ? (
        <button
          type="button"
          className="text-muted-foreground hover:text-foreground mt-5 ml-5 text-sm underline underline-offset-2"
          onClick={onAbandon}
        >
          Discard this invitation
        </button>
      ) : null}
    </div>
  )
}

export function PublicInvitationAccountMismatch({
  practiceName,
  invalidInvitation,
  onAbandon,
}: {
  practiceName: string
  invalidInvitation: boolean
  onAbandon: () => void
}) {
  return (
    <div className="max-w-xl">
      <StepKicker n="02" label="Practice invitation" />
      <DisplayTitle>Use the intended account</DisplayTitle>
      <p className="text-muted-foreground mt-4 leading-relaxed">
        {invalidInvitation
          ? "This invitation link is malformed. Ask the practice owner for a new link."
          : `This signed-in account already belongs to ${practiceName}, so the invitation was not applied. Sign in with the exact verified email the owner invited.`}
      </p>
      <p className="bg-muted/40 mt-6 rounded-xl border p-4 text-sm">
        No membership or seat was changed.
      </p>
      <Button
        type="button"
        variant="outline"
        className="mt-6"
        onClick={onAbandon}
      >
        Discard invitation and continue
      </Button>
    </div>
  )
}

function PublicOwnerSetupPending({
  practiceName,
  currentStep,
  refreshError,
  onRefresh,
}: {
  practiceName: string
  currentStep: PublicOnboardingState["current_step"]
  refreshError: string | null
  onRefresh: () => Promise<void>
}) {
  const [refreshing, setRefreshing] = useState(false)
  const waitingFor =
    currentStep === "baa"
      ? "the practice BAA"
      : "the current Terms of Service and Privacy Policy"

  async function refresh() {
    setRefreshing(true)
    try {
      await onRefresh()
    } finally {
      setRefreshing(false)
    }
  }

  return (
    <div className="max-w-xl">
      <StepKicker n="03" label="Owner setup" />
      <DisplayTitle>Your owner is finishing setup</DisplayTitle>
      <p className="text-muted-foreground mt-4 leading-relaxed">
        You joined {practiceName} as workforce. A practice owner must accept
        {` ${waitingFor}`} before onboarding can continue. You are not being
        asked to accept an owner agreement.
      </p>
      <p className="bg-muted/40 mt-6 rounded-xl border p-4 text-sm">
        Patient-data access remains locked while you wait.
      </p>
      {refreshError ? (
        <p role="alert" className="text-destructive mt-4 text-sm">
          {refreshError}
        </p>
      ) : null}
      <Button
        type="button"
        variant="outline"
        className="mt-6"
        disabled={refreshing}
        onClick={() => void refresh()}
      >
        {refreshing ? "Refreshing…" : "Refresh practice status"}
      </Button>
    </div>
  )
}

type GeneratedInvitationLink = {
  email: string
  url: string
  idempotencyKey: string
}

export function publicInvitationUrl(token: string) {
  const url = new URL(href("/onboarding"), window.location.origin)
  url.hash = new URLSearchParams({ registrationToken: token }).toString()
  return url.toString()
}

function formatPublicOnboardingDate(value: string) {
  const date = new Date(value)
  if (Number.isNaN(date.valueOf())) return "Date unavailable"
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date)
}

function publicMemberLabel(member: PublicPracticeMember) {
  if (member.role === "owner") return "Practice owner"
  return "Workforce clinician"
}

function publicIdentityEmail(email: string | null | undefined) {
  return email?.trim() || "Email unavailable"
}

export function PublicPracticeTeam({
  practiceName,
  ownerEmail,
  capabilities,
  onEntitlementsRefresh,
}: {
  practiceName: string
  ownerEmail: string | null
  capabilities: PublicCapabilityEntitlement | null
  onEntitlementsRefresh: () => Promise<void>
}) {
  const [invitations, setInvitations] = useState<
    PublicPracticeInvitation[] | null
  >(null)
  const [members, setMembers] = useState<PublicPracticeMember[] | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [invitationEmail, setInvitationEmail] = useState("")
  const [expiresInDays, setExpiresInDays] = useState("7")
  const [pendingRequest, setPendingRequest] =
    useState<PendingPublicInvitationMutation | null>(null)
  const [generatedLink, setGeneratedLink] =
    useState<GeneratedInvitationLink | null>(null)
  const [copied, setCopied] = useState(false)
  const [busyAction, setBusyAction] = useState<string | null>(null)
  const [journalError, setJournalError] = useState<string | null>(null)
  const [corruptJournalSnapshot, setCorruptJournalSnapshot] = useState<
    string | null
  >(null)
  const canonicalPracticeId = capabilities?.actor.practice_id
  const invitationMutationScope = canonicalPracticeId
    ? publicInvitationMutationScope({
        practiceId: canonicalPracticeId,
        practiceName,
        ownerEmail,
      })
    : null

  const loadTeam = useCallback(async () => {
    setLoadError(null)
    try {
      const [nextInvitations, nextMembers] = await Promise.all([
        getPublicPracticeInvitations(),
        getPublicPracticeMembers(),
      ])
      setInvitations(nextInvitations)
      setMembers(nextMembers)
    } catch (err) {
      setInvitations(null)
      setMembers(null)
      setLoadError(
        err instanceof Error
          ? err.message
          : "Practice seat information is unavailable.",
      )
    }
  }, [])

  useEffect(() => {
    void loadTeam()
  }, [loadTeam])

  useEffect(() => {
    if (!invitationMutationScope) {
      setCorruptJournalSnapshot(null)
      setJournalError(
        "The signed-in practice identity is unavailable, so invitation creation remains locked.",
      )
      return
    }
    const key = publicInvitationMutationStorageKey(invitationMutationScope)
    const recover = () => {
      try {
        const saved = loadPublicInvitationMutation(invitationMutationScope)
        setPendingRequest(saved)
        if (saved) {
          setInvitationEmail(saved.email)
          setExpiresInDays(String(saved.expiresInDays))
        }
        setCorruptJournalSnapshot(null)
        setJournalError(null)
      } catch (err) {
        setPendingRequest(null)
        setGeneratedLink(null)
        setCopied(false)
        setCorruptJournalSnapshot(
          err instanceof PublicInvitationMutationJournalError
            ? err.invalidSnapshot
            : null,
        )
        setJournalError(
          err instanceof Error
            ? err.message
            : "The saved invitation request is unavailable.",
        )
      }
    }
    const onStorage = (event: StorageEvent) => {
      if (event.key === key) recover()
    }
    recover()
    window.addEventListener("storage", onStorage)
    return () => window.removeEventListener("storage", onStorage)
  }, [invitationMutationScope])

  async function refreshTeamAndEntitlements() {
    await Promise.all([loadTeam(), onEntitlementsRefresh()])
  }

  async function createInvitation(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const email = invitationEmail.trim().toLowerCase()
    const days = Number.parseInt(expiresInDays, 10)
    if (!email || !Number.isInteger(days) || days < 1 || days > 30) return
    if (!invitationMutationScope) {
      setJournalError(
        "The signed-in practice identity is unavailable, so invitation creation remains locked.",
      )
      return
    }

    setBusyAction("create-invitation")
    setActionError(null)
    setJournalError(null)
    setCopied(false)
    try {
      const request = await reservePublicInvitationMutation(
        invitationMutationScope,
        email,
        days,
      )
      // Reservation is durable and cross-tab serialized before the request is
      // allowed onto the wire. A lost response or reload therefore retries the
      // same logical mutation with the same idempotency key.
      setPendingRequest(request)
      const invitation = await createPublicPracticeInvitation(
        { email: request.email, expires_in_days: request.expiresInDays },
        request.idempotencyKey,
      )
      if (invitation.invite_token) {
        setGeneratedLink({
          email: request.email,
          url: publicInvitationUrl(invitation.invite_token),
          idempotencyKey: request.idempotencyKey,
        })
      } else {
        setGeneratedLink(null)
        setActionError(
          "The invitation was recorded, but its secure link is unavailable. Revoke it and issue a new invitation before sharing access.",
        )
      }
      await refreshTeamAndEntitlements()
    } catch (err) {
      try {
        const saved = loadPublicInvitationMutation(invitationMutationScope)
        setPendingRequest(saved)
        if (saved) {
          setInvitationEmail(saved.email)
          setExpiresInDays(String(saved.expiresInDays))
        }
      } catch {
        // The primary error below remains the actionable one.
      }
      setActionError(
        err instanceof Error
          ? err.message
          : "The invitation could not be created.",
      )
    } finally {
      setBusyAction(null)
    }
  }

  async function copyInvitationLink() {
    if (!generatedLink || !invitationMutationScope) return
    try {
      await navigator.clipboard.writeText(generatedLink.url)
    } catch {
      setActionError(
        "The link could not be copied automatically. Select and copy it from the field.",
      )
      return
    }
    setCopied(true)
    try {
      const cleared = await discardPublicInvitationMutation(
        invitationMutationScope,
        generatedLink.idempotencyKey,
      )
      if (!cleared) {
        const saved = loadPublicInvitationMutation(invitationMutationScope)
        setPendingRequest(saved)
        setGeneratedLink(null)
        setCopied(false)
        if (saved) {
          setInvitationEmail(saved.email)
          setExpiresInDays(String(saved.expiresInDays))
        }
        setActionError(
          "The link was copied, but another tab saved a newer invitation request. That request remains available to retry.",
        )
        return
      }
      setPendingRequest(null)
    } catch (err) {
      setActionError(
        err instanceof Error
          ? `The link was copied, but the saved retry could not be cleared: ${err.message}`
          : "The link was copied, but the saved retry could not be cleared.",
      )
    }
  }

  async function finishGeneratedInvitation() {
    if (!generatedLink || !invitationMutationScope) return
    setBusyAction("finish-invitation")
    setActionError(null)
    try {
      const cleared = await discardPublicInvitationMutation(
        invitationMutationScope,
        generatedLink.idempotencyKey,
      )
      if (!cleared) {
        const saved = loadPublicInvitationMutation(invitationMutationScope)
        setPendingRequest(saved)
        setGeneratedLink(null)
        setCopied(false)
        if (saved) {
          setInvitationEmail(saved.email)
          setExpiresInDays(String(saved.expiresInDays))
        }
        setActionError(
          "Another tab saved a newer invitation request. It was not discarded and remains available to retry.",
        )
        return
      }
      setPendingRequest(null)
      setGeneratedLink(null)
      setInvitationEmail("")
      setExpiresInDays("7")
      setCopied(false)
    } catch (err) {
      setActionError(
        err instanceof Error
          ? err.message
          : "The saved invitation request could not be cleared.",
      )
    } finally {
      setBusyAction(null)
    }
  }

  async function discardPendingInvitation() {
    if (!pendingRequest || !invitationMutationScope) return
    if (
      !window.confirm(
        "Discard this saved request? If the server received an earlier attempt, revoke the invitation from the list before issuing another one.",
      )
    ) {
      return
    }
    setBusyAction("discard-invitation")
    setActionError(null)
    try {
      const cleared = await discardPublicInvitationMutation(
        invitationMutationScope,
        pendingRequest.idempotencyKey,
      )
      if (!cleared) {
        const saved = loadPublicInvitationMutation(invitationMutationScope)
        setPendingRequest(saved)
        setGeneratedLink(null)
        setCopied(false)
        if (saved) {
          setInvitationEmail(saved.email)
          setExpiresInDays(String(saved.expiresInDays))
        }
        setActionError(
          "Another tab saved a newer invitation request. It was not discarded and remains available to retry.",
        )
        return
      }
      setPendingRequest(null)
      setGeneratedLink(null)
      setInvitationEmail("")
      setExpiresInDays("7")
      setCopied(false)
    } catch (err) {
      setActionError(
        err instanceof Error
          ? err.message
          : "The saved invitation request could not be discarded.",
      )
    } finally {
      setBusyAction(null)
    }
  }

  async function discardUnrecognizedInvitationJournal() {
    if (!invitationMutationScope || corruptJournalSnapshot === null) return
    if (
      !window.confirm(
        "Discard the unrecognized saved invitation request from this browser?",
      )
    ) {
      return
    }
    setBusyAction("discard-invitation")
    try {
      const cleared = await discardCorruptPublicInvitationMutation(
        invitationMutationScope,
        corruptJournalSnapshot,
      )
      if (!cleared) {
        setJournalError(
          "The saved invitation request changed in another tab and was not discarded. Review the current request before continuing.",
        )
        return
      }
      setPendingRequest(null)
      setGeneratedLink(null)
      setInvitationEmail("")
      setExpiresInDays("7")
      setCopied(false)
      setCorruptJournalSnapshot(null)
      setJournalError(null)
    } catch (err) {
      setJournalError(
        err instanceof Error
          ? err.message
          : "The saved invitation request could not be discarded.",
      )
    } finally {
      setBusyAction(null)
    }
  }

  async function revokeInvitation(invitation: PublicPracticeInvitation) {
    if (
      !window.confirm(
        "Revoke this invitation? Its secure link will stop working and cannot be reactivated.",
      )
    ) {
      return
    }
    setBusyAction(`invitation-${invitation.invitation_id}`)
    setActionError(null)
    try {
      await revokePublicPracticeInvitation(
        invitation.invitation_id,
        "Revoked by the practice owner during public onboarding.",
      )
      await refreshTeamAndEntitlements()
    } catch (err) {
      setActionError(
        err instanceof Error ? err.message : "The invitation was not revoked.",
      )
    } finally {
      setBusyAction(null)
    }
  }

  async function setMemberActive(
    member: PublicPracticeMember,
    active: boolean,
  ) {
    if (
      !active &&
      !window.confirm(
        "Deactivate this clinician seat? Their practice access will be locked until an owner reactivates it.",
      )
    ) {
      return
    }
    setBusyAction(`member-${member.member_id}`)
    setActionError(null)
    try {
      if (active) {
        await reactivatePublicPracticeMember(member.member_id)
      } else {
        await deactivatePublicPracticeMember(member.member_id)
      }
      await refreshTeamAndEntitlements()
    } catch (err) {
      setActionError(
        err instanceof Error
          ? err.message
          : "The clinician seat was not changed.",
      )
    } finally {
      setBusyAction(null)
    }
  }

  const activeSeats = capabilities?.seats.active_clinician_seats
  const maximumSeats = capabilities?.seats.maximum_active_clinician_seats ?? 20
  const invitationDraftLocked =
    pendingRequest !== null || generatedLink !== null

  return (
    <section
      aria-labelledby="public-practice-team-title"
      className="mt-12 max-w-3xl border-t pt-10"
    >
      <StepKicker n="07" label="Practice seats" />
      <h2
        id="public-practice-team-title"
        className="mt-3 text-3xl font-semibold text-[var(--brand-navy-deep)]"
        style={{ fontFamily: "'Cormorant Garamond', serif" }}
      >
        Manage {practiceName}
      </h2>
      <p className="text-muted-foreground mt-3 leading-relaxed">
        {activeSeats === undefined
          ? `Up to ${maximumSeats} active clinician seats are allowed.`
          : `${activeSeats} of ${maximumSeats} active clinician seats are in use.`}{" "}
        Inactive and revoked memberships do not count toward billing quantity.
      </p>

      <form
        onSubmit={(event) => void createInvitation(event)}
        className="bg-card mt-6 grid gap-4 rounded-xl border p-5"
      >
        <h3 className="font-semibold">Invite a workforce clinician</h3>
        <div className="grid gap-4 sm:grid-cols-[minmax(0,1fr)_8rem]">
          <Field label="Verified work email" htmlFor="public-invitation-email">
            <Input
              id="public-invitation-email"
              type="email"
              required
              value={invitationEmail}
              disabled={invitationDraftLocked}
              onChange={(event) => {
                setInvitationEmail(event.target.value)
                setGeneratedLink(null)
                setCopied(false)
              }}
              autoComplete="off"
              data-sentry-mask
              data-ph-no-capture
            />
          </Field>
          <Field label="Expires (days)" htmlFor="public-invitation-expiry">
            <Input
              id="public-invitation-expiry"
              type="number"
              min={1}
              max={30}
              required
              value={expiresInDays}
              disabled={invitationDraftLocked}
              onChange={(event) => {
                setExpiresInDays(event.target.value)
                setGeneratedLink(null)
                setCopied(false)
              }}
            />
          </Field>
        </div>
        <Button
          type="submit"
          disabled={
            busyAction !== null ||
            !invitationEmail.trim() ||
            !invitationMutationScope ||
            generatedLink !== null
          }
        >
          {busyAction === "create-invitation"
            ? "Creating secure link…"
            : pendingRequest
              ? "Retry saved invitation"
              : "Create invitation"}
        </Button>
        {pendingRequest && !generatedLink ? (
          <div className="bg-muted/40 rounded-lg border p-4 text-sm">
            <p>
              This invitation request is saved in this browser. Retrying after a
              reload or in another tab reuses the same idempotency key.
            </p>
            <Button
              type="button"
              variant="outline"
              className="mt-3"
              disabled={busyAction !== null}
              onClick={() => void discardPendingInvitation()}
            >
              {busyAction === "discard-invitation"
                ? "Discarding…"
                : "Discard saved request"}
            </Button>
          </div>
        ) : null}
        {generatedLink ? (
          <div
            className="bg-muted/40 grid gap-3 rounded-lg border p-4"
            data-sentry-mask
            data-ph-no-capture
          >
            <p className="text-sm font-medium">
              Share this one-practice link with {generatedLink.email}. It is
              shown only for this request.
            </p>
            <Input
              aria-label="Secure workforce invitation link"
              value={generatedLink.url}
              readOnly
              onFocus={(event) => event.currentTarget.select()}
              data-sentry-mask
              data-ph-no-capture
            />
            <Button
              type="button"
              variant="outline"
              onClick={() => void copyInvitationLink()}
            >
              {copied ? "Copied" : "Copy secure link"}
            </Button>
            <Button
              type="button"
              variant="outline"
              disabled={busyAction !== null}
              onClick={() => void finishGeneratedInvitation()}
            >
              {busyAction === "finish-invitation"
                ? "Finishing…"
                : "I saved this link"}
            </Button>
          </div>
        ) : null}
      </form>

      {journalError ? (
        <div className="mt-4">
          <p role="alert" className="text-destructive text-sm">
            {journalError}
          </p>
          {invitationMutationScope && corruptJournalSnapshot !== null ? (
            <Button
              type="button"
              variant="outline"
              className="mt-3"
              disabled={busyAction !== null}
              onClick={() => void discardUnrecognizedInvitationJournal()}
            >
              Discard saved invitation data
            </Button>
          ) : null}
        </div>
      ) : null}
      {actionError ? (
        <p role="alert" className="text-destructive mt-4 text-sm">
          {actionError}
        </p>
      ) : null}
      {loadError ? (
        <p role="alert" className="text-destructive mt-4 text-sm">
          {loadError}
        </p>
      ) : null}

      <PublicPracticeTeamLists
        invitations={invitations}
        members={members}
        loading={!loadError && (invitations === null || members === null)}
        busyAction={busyAction}
        onRevokeInvitation={revokeInvitation}
        onSetMemberActive={setMemberActive}
      />
    </section>
  )
}

export function PublicPracticeTeamLists({
  invitations,
  members,
  loading,
  busyAction,
  onRevokeInvitation,
  onSetMemberActive,
}: {
  invitations: PublicPracticeInvitation[] | null
  members: PublicPracticeMember[] | null
  loading: boolean
  busyAction: string | null
  onRevokeInvitation: (invitation: PublicPracticeInvitation) => Promise<void>
  onSetMemberActive: (
    member: PublicPracticeMember,
    active: boolean,
  ) => Promise<void>
}) {
  return (
    <div className="mt-8 grid gap-6 md:grid-cols-2">
      <section aria-labelledby="public-members-title">
        <h3 id="public-members-title" className="font-semibold">
          Clinician seats
        </h3>
        <div className="mt-3 grid gap-3">
          {loading ? (
            <p className="text-muted-foreground text-sm">Loading seats…</p>
          ) : null}
          {members?.map((member) => (
            <div
              key={member.member_id}
              className="bg-card rounded-lg border p-4"
              data-sentry-mask
              data-ph-no-capture
            >
              <div className="flex items-start justify-between gap-3">
                <div>
                  <p className="font-medium">{publicMemberLabel(member)}</p>
                  <p
                    className="text-muted-foreground mt-1 text-sm"
                    data-sentry-mask
                    data-ph-no-capture
                  >
                    {publicIdentityEmail(member.email)}
                  </p>
                  <p className="text-muted-foreground mt-1 text-sm capitalize">
                    {member.access_revoked
                      ? "revoked"
                      : `${member.seat_state} seat`}{" "}
                    · credential {member.credential_state.replace("_", " ")}
                  </p>
                </div>
                {member.role === "workforce" && !member.access_revoked ? (
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    disabled={busyAction !== null}
                    aria-label={`${
                      member.seat_state === "active"
                        ? "Deactivate"
                        : "Reactivate"
                    } clinician seat for ${publicIdentityEmail(member.email)}`}
                    onClick={() =>
                      void onSetMemberActive(
                        member,
                        member.seat_state === "inactive",
                      )
                    }
                  >
                    {busyAction === `member-${member.member_id}`
                      ? "Saving…"
                      : member.seat_state === "active"
                        ? "Deactivate"
                        : "Reactivate"}
                  </Button>
                ) : null}
              </div>
            </div>
          ))}
          {members?.length === 0 ? (
            <p className="text-muted-foreground text-sm">
              No clinician memberships were returned.
            </p>
          ) : null}
        </div>
      </section>

      <section aria-labelledby="public-invitations-title">
        <h3 id="public-invitations-title" className="font-semibold">
          Invitations
        </h3>
        <div className="mt-3 grid gap-3">
          {loading ? (
            <p className="text-muted-foreground text-sm">
              Loading invitations…
            </p>
          ) : null}
          {invitations?.map((invitation) => (
            <div
              key={invitation.invitation_id}
              className="bg-card rounded-lg border p-4"
              data-sentry-mask
              data-ph-no-capture
            >
              <div className="flex items-start justify-between gap-3">
                <div>
                  <p
                    className="font-medium"
                    data-sentry-mask
                    data-ph-no-capture
                  >
                    {publicIdentityEmail(invitation.invitee_email)}
                  </p>
                  <p className="text-muted-foreground mt-1 text-sm capitalize">
                    {invitation.status} workforce invitation
                  </p>
                  <p className="text-muted-foreground mt-1 text-xs">
                    Expires {formatPublicOnboardingDate(invitation.expires_at)}
                  </p>
                </div>
                {invitation.status === "pending" ? (
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    disabled={busyAction !== null}
                    aria-label={`Revoke invitation for ${publicIdentityEmail(
                      invitation.invitee_email,
                    )}`}
                    onClick={() => void onRevokeInvitation(invitation)}
                  >
                    {busyAction === `invitation-${invitation.invitation_id}`
                      ? "Revoking…"
                      : "Revoke"}
                  </Button>
                ) : null}
              </div>
            </div>
          ))}
          {invitations?.length === 0 ? (
            <p className="text-muted-foreground text-sm">
              No invitations have been issued.
            </p>
          ) : null}
        </div>
      </section>
    </div>
  )
}

export function PublicLegalAcceptance({
  documents,
  loadError,
  practiceName,
  onAccepted,
}: {
  documents: PublicLegalDocuments | null
  loadError: string | null
  practiceName: string
  onAccepted: (state: PublicOnboardingState) => void
}) {
  const [signatureName, setSignatureName] = useState("")
  const [signatureTitle, setSignatureTitle] = useState("Physician / owner")
  const [authorityConfirmed, setAuthorityConfirmed] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const terms = documents?.documents.find((doc) => doc.kind === "terms")
  const privacy = documents?.documents.find((doc) => doc.kind === "privacy")
  const exactDocumentsAvailable = Boolean(
    documents?.contract_version === "public-legal-documents.v1" &&
      documents.documents.length === 2 &&
      terms &&
      privacy,
  )
  const ready =
    exactDocumentsAvailable &&
    authorityConfirmed &&
    signatureName.trim().length >= 2 &&
    signatureTitle.trim().length >= 2

  async function onAccept() {
    if (!terms || !privacy || !ready) return
    setBusy(true)
    setError(null)
    try {
      const next = await acceptPracticeLegal({
        signature_name: signatureName.trim(),
        signature_title: signatureTitle.trim(),
        authority_confirmed: true,
        documents: [
          {
            kind: terms.kind,
            version: terms.version,
            sha256: terms.sha256,
          },
          {
            kind: privacy.kind,
            version: privacy.version,
            sha256: privacy.sha256,
          },
        ],
      })
      onAccepted(next)
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Could not record the legal acceptance.",
      )
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="max-w-2xl">
      <StepKicker n="04" label="Legal terms" />
      <DisplayTitle>Review the current terms</DisplayTitle>
      <p className="text-muted-foreground mt-4 leading-relaxed">
        Review the exact documents below for {practiceName}. Their versions and
        content hashes will be recorded with your name, title, authority, and
        acceptance time.
      </p>
      {exactDocumentsAvailable && terms && privacy ? (
        <div className="bg-card mt-6 grid gap-3 rounded-xl border p-5">
          {[terms, privacy].map((doc) => (
            <div key={doc.kind} className="rounded-lg border px-4 py-3">
              <a
                href={doc.path}
                target="_blank"
                rel="noreferrer"
                className="font-semibold text-[var(--brand-navy-deep)] underline underline-offset-2"
              >
                {doc.kind === "terms" ? "Terms of Service" : "Privacy Policy"}
              </a>
              <p className="text-muted-foreground mt-1 text-sm">
                Version {doc.version}
              </p>
              <p className="text-muted-foreground mt-1 font-mono text-xs break-all">
                SHA-256 {doc.sha256}
              </p>
            </div>
          ))}
        </div>
      ) : (
        <div
          role="alert"
          className="border-destructive/40 bg-destructive/5 text-destructive mt-6 rounded-xl border p-4 text-sm"
        >
          {loadError ??
            "The exact current Terms and Privacy documents are unavailable. Acceptance stays locked."}
        </div>
      )}
      <div className="mt-6 grid max-w-xl gap-4">
        <Field label="Accepting individual’s name" htmlFor="legal-signer-name">
          <Input
            id="legal-signer-name"
            value={signatureName}
            onChange={(event) => setSignatureName(event.target.value)}
            autoComplete="name"
          />
        </Field>
        <Field label="Title" htmlFor="legal-signer-title">
          <Input
            id="legal-signer-title"
            value={signatureTitle}
            onChange={(event) => setSignatureTitle(event.target.value)}
          />
        </Field>
        <CheckRow checked={authorityConfirmed} onChange={setAuthorityConfirmed}>
          I confirm that I am authorized to accept these Terms of Service and
          Privacy Policy for {practiceName}.
        </CheckRow>
        {error ? (
          <p role="alert" className="text-destructive text-sm">
            {error}
          </p>
        ) : null}
        <Button
          type="button"
          size="lg"
          disabled={!ready || busy}
          onClick={() => void onAccept()}
        >
          {busy ? "Recording…" : "Accept current Terms and Privacy"}
        </Button>
      </div>
    </div>
  )
}

export function PublicCredentialStatus({
  state,
  capabilities,
  capabilitiesError,
  refreshError,
  onRefresh,
}: {
  state: PublicOnboardingState
  capabilities: PublicCapabilityEntitlement | null
  capabilitiesError: string | null
  refreshError: string | null
  onRefresh: () => Promise<void>
}) {
  const [refreshing, setRefreshing] = useState(false)
  const credential = credentialStatusCopy(state)

  async function refresh() {
    setRefreshing(true)
    try {
      await onRefresh()
    } finally {
      setRefreshing(false)
    }
  }

  return (
    <div className="max-w-3xl">
      <StepKicker n="06" label="Access review" />
      <DisplayTitle>{credential.title}</DisplayTitle>
      <p className="text-muted-foreground mt-4 max-w-2xl leading-relaxed">
        {credential.description}
      </p>
      <div className="mt-8 grid gap-4 md:grid-cols-2">
        <section
          aria-labelledby="billing-status-title"
          className="bg-card rounded-xl border p-5"
        >
          <h2 id="billing-status-title" className="font-semibold">
            Billing and trial
          </h2>
          <p className="mt-3 text-lg font-medium">
            {subscriptionStatusLabel(state.subscription_state)}
          </p>
          <p className="text-muted-foreground mt-2 text-sm">
            {trialEndCopy(state)}
          </p>
          <p className="text-muted-foreground mt-3 text-xs">
            The billing clock is separate from credential approval.
          </p>
        </section>
        <section
          aria-labelledby="credential-status-title"
          className="bg-card rounded-xl border p-5"
        >
          <h2 id="credential-status-title" className="font-semibold">
            Credential review
          </h2>
          <p className="mt-3 text-lg font-medium">{credential.label}</p>
          <p className="text-muted-foreground mt-2 text-sm">
            PHI access: locked
          </p>
          <p className="text-muted-foreground mt-3 text-xs">
            Credential review cannot be self-approved.
          </p>
        </section>
      </div>
      <CapabilityStatus
        capabilities={capabilities}
        error={capabilitiesError}
        phiAccessAllowed={publicOnboardingCanUsePhi(state)}
      />
      {refreshError ? (
        <p role="alert" className="text-destructive mt-4 text-sm">
          {refreshError}
        </p>
      ) : null}
      <Button
        type="button"
        variant="outline"
        className="mt-6"
        disabled={refreshing}
        onClick={() => void refresh()}
      >
        {refreshing ? "Refreshing…" : "Refresh review status"}
      </Button>
    </div>
  )
}

export function CapabilityStatus({
  capabilities,
  error,
  phiAccessAllowed,
}: {
  capabilities: PublicCapabilityEntitlement | null
  error: string | null
  phiAccessAllowed: boolean
}) {
  const entries = capabilities
    ? ([
        ["SOAP Note", capabilities.capabilities.soap_note],
        ["Intake Prep", capabilities.capabilities.intake_prep],
        ["Session Prep (read-only)", capabilities.capabilities.session_prep],
        ["Scribe (web/PWA first)", capabilities.capabilities.scribe],
        ["CAB (advisory only)", capabilities.capabilities.cab],
      ] as const)
    : []

  return (
    <section aria-labelledby="capability-status-title" className="mt-8">
      <h2 id="capability-status-title" className="text-lg font-semibold">
        Capability access
      </h2>
      {capabilities ? (
        <div className="mt-3 grid gap-2 sm:grid-cols-2">
          {entries.map(([label, decision]) => (
            <div key={label} className="bg-card rounded-lg border px-4 py-3">
              <p className="font-medium">{label}</p>
              <p className="text-muted-foreground mt-1 text-sm">
                {capabilityDecisionLabel(decision, phiAccessAllowed)}
              </p>
            </div>
          ))}
        </div>
      ) : (
        <p role="status" className="text-muted-foreground mt-3 text-sm">
          {error ??
            "Capability status is unavailable, so patient-data access remains locked."}
        </p>
      )}
      <p className="text-muted-foreground mt-4 text-xs leading-relaxed">
        Pilot scope: Intake Prep uses exactly five versioned questions whose
        wording and order are managed by authorized practice users; Session Prep
        is read-only; Scribe starts on web/PWA, with supported desktop and
        native-mobile clients later; and CAB is advisory only, with no EHR write
        or claim submission.
      </p>
    </section>
  )
}

function capabilityDecisionLabel(
  decision: PublicCapabilityEntitlement["capabilities"]["soap_note"],
  phiAccessAllowed: boolean,
) {
  if (decision.decision !== "allowed") return "Locked"
  if (decision.data_mode === "real_phi") {
    return phiAccessAllowed ? "Available for patient care" : "Locked"
  }
  if (decision.data_mode === "synthetic_only") return "Synthetic data only"
  return "Locked"
}

function credentialStatusCopy(state: PublicOnboardingState) {
  if (state.credential_state === "pending") {
    return {
      title: "Credential review is pending",
      label: "Pending",
      description:
        "Your billing trial continues while review is pending. Patient-data capabilities stay locked until an authorized reviewer approves your credentials.",
    }
  }
  if (state.credential_state === "needs_review") {
    return {
      title: "Credential review needs attention",
      label: "Needs review",
      description:
        "An authorized reviewer must resolve the credential record. Patient-data capabilities remain locked in the meantime.",
    }
  }
  if (state.credential_state === "rejected") {
    return {
      title: "Credentials were not approved",
      label: "Not approved",
      description:
        "Patient-data capabilities remain locked. Contact Cosmaneura support if the credential record should be reviewed.",
    }
  }
  return {
    title: "Access checks are not complete",
    label: "Approved; access still locked",
    description:
      "Credential approval is recorded, but another server-side access check has not passed. Patient-data capabilities remain locked.",
  }
}

function PracticeFlow({
  practice,
  onPractice,
  channel,
  email,
  publicInvitationMode = false,
}: {
  practice: PracticeStatus | null
  onPractice: (practice: PracticeStatus) => void
  channel: Channel
  email: string | null
  publicInvitationMode?: boolean
}) {
  const [path, setPath] = useState<Path>(() => {
    if (practice?.role === "owner") return "owner"
    if (practice?.role === "workforce") return "workforce"
    return "undecided"
  })
  const [mismatch, setMismatch] = useState<JoinMismatch | null>(null)
  const [legacyWorkforce, setLegacyWorkforce] = useState(false)

  function goSolo(reason?: JoinMismatch | null) {
    if (reason) setMismatch(reason)
    setPath("owner")
  }

  if (path === "undecided") {
    return (
      <ChoosePath
        onChoose={setPath}
        onSolo={() => goSolo(null)}
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
          setMismatch(null)
          setPath("undecided")
        }}
        channel={channel}
        mismatch={mismatch}
        email={email}
      />
    )
  }
  if (publicInvitationMode && !legacyWorkforce) {
    return (
      <PublicInvitationAcceptance
        initialToken={null}
        onBack={() => {
          setLegacyWorkforce(false)
          setPath("undecided")
        }}
        onUseLegacyJoinCode={() => setLegacyWorkforce(true)}
      />
    )
  }
  return (
    <WorkforceFlow
      onPractice={onPractice}
      onBack={() => {
        setLegacyWorkforce(false)
        setPath("undecided")
      }}
      onSolo={goSolo}
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
  mismatch: JoinMismatch | null
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
  mismatch: JoinMismatch | null
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
          <p className="font-medium">
            {mismatch.practice_name} did not accept this inbox.
          </p>
          <p className="mt-1 opacity-80">
            Joining requires an @{mismatch.required_domain} address
            {mismatch.signed_in_domain
              ? ` — you’re on @${mismatch.signed_in_domain}`
              : ""}
            . Name your own covered entity below.
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

function BaaClickwrap({
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
        if (!cancelled) setDoc(next)
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
    setBusy(true)
    setError(null)
    try {
      const next = await acceptPracticeBaa({
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
  onSolo: (reason: JoinMismatch) => void
  channel: Channel
}) {
  const [code, setCode] = useState("")
  const [found, setFound] = useState<PracticeStatus | null>(null)
  const [checks, setChecks] = useState<Record<string, boolean>>({})
  const [name, setName] = useState("")
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
        onSolo(res.mismatch)
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

  async function onJoin() {
    if (!found?.join_code) return
    setBusy(true)
    setError(null)
    try {
      const next = await joinPractice({
        join_code: found.join_code,
        attestation_name: name,
        checks: WORKFORCE_CHECKS.map((item) => item.id).filter(
          (id) => checks[id],
        ),
      })
      onPractice(next)
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not join")
    } finally {
      setBusy(false)
    }
  }

  const ready =
    Boolean(found?.baa_accepted) &&
    WORKFORCE_CHECKS.every((item) => checks[item.id]) &&
    name.trim().length >= 2

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
            <div className="mt-5 grid gap-4">
              {WORKFORCE_CHECKS.map((item) => (
                <CheckRow
                  key={item.id}
                  checked={Boolean(checks[item.id])}
                  onChange={(next) =>
                    setChecks((prev) => ({ ...prev, [item.id]: next }))
                  }
                >
                  {item.label}
                </CheckRow>
              ))}
              <Field label="Typed name" htmlFor="attestation">
                <Input
                  id="attestation"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                />
              </Field>
              <Button
                type="button"
                disabled={!ready || busy}
                onClick={() => void onJoin()}
                size="lg"
              >
                {busy ? "Joining…" : `Attest and join ${found.legal_name}`}
              </Button>
            </div>
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
    const token = await getSessionToken()
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

function Field({
  label,
  htmlFor,
  children,
}: {
  label: string
  htmlFor: string
  children: React.ReactNode
}) {
  return (
    <div className="grid gap-1.5 text-left">
      <Label htmlFor={htmlFor}>{label}</Label>
      {children}
    </div>
  )
}

function CheckRow({
  checked,
  onChange,
  children,
}: {
  checked: boolean
  onChange: (next: boolean) => void
  children: React.ReactNode
}) {
  return (
    <label className="flex gap-3 text-left text-sm leading-snug">
      <input
        type="checkbox"
        className="mt-0.5 size-4 accent-[var(--brand-navy-deep)]"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
      />
      <span>{children}</span>
    </label>
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
