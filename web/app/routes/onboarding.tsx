import { useMemo, useState } from "react"
import { useForm } from "react-hook-form"
import { href, redirect } from "react-router"
import { z } from "zod"

import { ArrowRight, Building2, UserRound } from "lucide-react"

import type { Route } from "./+types/onboarding"

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
import { getClient } from "~/configuration/clerk"
import {
  completeOnboarding,
  generateSignedExampleNoteUploadUrl,
  settingsData,
  updateSettingsData,
  uploadExampleNote,
} from "~/configuration/client"
import { toast } from "~/hooks/use-toast"
import { uploadFileToAzure } from "~/lib/azure-upload"
import {
  BAA_VERSION,
  COMPANY,
  OWNER_CHECKS,
  WORKFORCE_CHECKS,
  baaSections,
} from "~/lib/baa"
import { type Channel, resolveChannel } from "~/lib/channel"
import { MCD_EMAIL_DOMAIN, isMcdEmail } from "~/lib/email-domain"
import {
  type JoinMismatch,
  type PracticeStatus,
  acceptPracticeBaa,
  createPractice,
  getPractice,
  joinPractice,
  lookupPractice,
  practiceReady,
} from "~/lib/practice-api"
import { cn } from "~/lib/utils"
import { requireEnv } from "~/utils/environment"
import { isZoomEnabled } from "~/utils/zoom-feature"

import { UserButton } from "@clerk/clerk-react"
import { invariant } from "@epic-web/invariant"
import { zodResolver } from "@hookform/resolvers/zod"
import { motion } from "motion/react"
import { SiZoom } from "react-icons/si"

type Path = "undecided" | "owner" | "workforce"
type OptionalStep = "choice" | "bio" | "notes" | "zoom" | "allset"

export async function clientLoader({ request }: Route.ClientLoaderArgs) {
  const { data } = await settingsData()
  invariant(data, "settings data failed to load")

  if (data.onboarding_completed) {
    throw redirect(href("/notes"))
  }

  const clerk = await getClient()
  const email = clerk?.user?.primaryEmailAddress?.emailAddress ?? null
  const channel = resolveChannel(
    new URL(request.url).searchParams.get("channel"),
  )
  const practice = await getPractice()

  return { ...data, email, channel, practice }
}

export default function OnboardingPage({ loaderData }: Route.ComponentProps) {
  const [practice, setPractice] = useState<PracticeStatus | null>(
    loaderData.practice,
  )
  const zoomEnabled = isZoomEnabled(loaderData.email)

  return (
    <div className="bg-background min-h-screen">
      <header className="border-b">
        <div className="mx-auto flex h-16 max-w-5xl items-center justify-between px-5">
          <BrandLogo variant="dark" size="sm" />
          <div className="flex items-center gap-3">
            {loaderData.email ? (
              <span className="text-muted-foreground hidden text-sm sm:inline">
                {loaderData.email}
              </span>
            ) : null}
            <UserButton />
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-5xl px-5 py-10">
        {practiceReady(practice) ? (
          <OptionalSetup
            biography={loaderData.biography}
            zoomEnabled={zoomEnabled}
            practice={practice}
          />
        ) : (
          <PracticeFlow
            practice={practice}
            onPractice={setPractice}
            channel={loaderData.channel}
            email={loaderData.email}
          />
        )}
      </main>
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
  const [mismatch, setMismatch] = useState<JoinMismatch | null>(null)

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
  return (
    <WorkforceFlow
      onPractice={onPractice}
      onBack={() => setPath("undecided")}
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
  return <BaaClickwrap practice={practice} onPractice={onPractice} />
}

function PracticeForm({
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
  const [npi, setNpi] = useState("")
  const [isSolo, setIsSolo] = useState(formChannel !== "mcd")
  const [pastoralPack, setPastoralPack] = useState(formChannel !== "direct")
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const next = await createPractice({
        legal_name: legalName,
        city,
        region,
        npi,
        is_solo: isSolo,
        channel: formChannel,
        pastoral_pack: pastoralPack,
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
          This is a Cosmas practice on your email, not an MCD tenant. Pastoral
          pack can stay on.
        </div>
      ) : null}
      <StepKicker n="02" label="Covered entity" />
      <DisplayTitle>Name the practice</DisplayTitle>
      <p className="text-muted-foreground mt-3">
        This legal name appears on the BAA with {COMPANY}. Solo or group — one
        agreement either way. Workforce will have to sign in from the same email
        domain as this account.
        {formChannel === "mcd"
          ? " MyCatholicDoctor is the network. Cosmas is still the product."
          : ""}
      </p>
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
        <Field label="Organizational NPI (optional)" htmlFor="npi">
          <Input
            id="npi"
            inputMode="numeric"
            maxLength={10}
            value={npi}
            onChange={(e) =>
              setNpi(e.target.value.replace(/\D/g, "").slice(0, 10))
            }
          />
        </Field>
        <fieldset className="grid gap-2">
          <legend className="text-sm font-medium">Who practices here?</legend>
          <label className={choiceClass(isSolo)}>
            <input
              type="radio"
              className="sr-only"
              checked={isSolo}
              onChange={() => setIsSolo(true)}
            />
            Solo — I am the only clinician on this BAA
          </label>
          <label className={choiceClass(!isSolo)}>
            <input
              type="radio"
              className="sr-only"
              checked={!isSolo}
              onChange={() => setIsSolo(false)}
            />
            Group — I will invite workforce under this same BAA
          </label>
        </fieldset>
        <label className="bg-card flex gap-3 rounded-lg border px-4 py-3 text-sm leading-snug">
          <input
            type="checkbox"
            className="mt-0.5 size-4 accent-[var(--brand-navy-deep)]"
            checked={pastoralPack}
            onChange={(e) => setPastoralPack(e.target.checked)}
          />
          <span>
            Enable the conscience and pastoral pack (NFP terminology, parish
            correspondence under per-event approval). Off by default for
            practices that are not Catholic.
          </span>
        </label>
        {error ? <p className="text-destructive text-sm">{error}</p> : null}
        <Button type="submit" disabled={busy} size="lg">
          {busy ? "Saving…" : "Continue to the BAA"}
        </Button>
      </form>
    </div>
  )
}

function BaaClickwrap({
  practice,
  onPractice,
}: {
  practice: PracticeStatus
  onPractice: (practice: PracticeStatus) => void
}) {
  const sections = useMemo(
    () => baaSections(practice.legal_name),
    [practice.legal_name],
  )
  const [checks, setChecks] = useState<Record<string, boolean>>({})
  const [signatureName, setSignatureName] = useState("")
  const [signatureTitle, setSignatureTitle] = useState("Physician / owner")
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const ready =
    OWNER_CHECKS.every((item) => checks[item.id]) &&
    signatureName.trim().length >= 2 &&
    signatureTitle.trim().length >= 2

  async function onAccept() {
    setBusy(true)
    setError(null)
    try {
      const next = await acceptPracticeBaa({
        signature_name: signatureName,
        signature_title: signatureTitle,
        checks: OWNER_CHECKS.map((item) => item.id).filter((id) => checks[id]),
      })
      onPractice(next)
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not accept the BAA")
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="grid gap-8 lg:grid-cols-[1fr_20rem]">
      <div>
        <StepKicker n="03" label="Agreement" />
        <DisplayTitle>Business Associate Agreement</DisplayTitle>
        <p className="text-muted-foreground mt-3">
          Covered entity:{" "}
          <span className="font-medium text-[var(--brand-navy-deep)]">
            {practice.legal_name}
          </span>
          . Business associate: {COMPANY}. Product: Cosmas. Version{" "}
          {BAA_VERSION}.
        </p>
        <p className="mt-3 rounded-lg border border-[var(--brand-gold)] bg-[var(--brand-cream)] px-4 py-3 text-sm text-[var(--brand-navy-deep)]">
          Draft for Cosmas onboarding UX. Counsel must ratify before this is the
          operative production BAA.
        </p>
        <ScrollArea className="bg-card mt-6 h-[28rem] rounded-xl border p-5">
          <ol className="space-y-6 pr-4">
            {sections.map((section) => (
              <li key={section.num}>
                <h2
                  className="text-xl font-semibold"
                  style={{ fontFamily: "'Cormorant Garamond', serif" }}
                >
                  § {section.num}. {section.title}
                </h2>
                <p className="mt-2 text-sm leading-relaxed">{section.body}</p>
              </li>
            ))}
          </ol>
        </ScrollArea>
      </div>
      <aside className="bg-card h-fit rounded-xl border p-5">
        <p className="text-xs font-semibold uppercase tracking-[0.18em] text-[var(--brand-gold)]">
          Clickwrap
        </p>
        <div className="mt-4 grid gap-4">
          {OWNER_CHECKS.map((item) => (
            <CheckRow
              key={item.id}
              checked={Boolean(checks[item.id])}
              onChange={(next) =>
                setChecks((prev) => ({ ...prev, [item.id]: next }))
              }
            >
              {item.label.replace(
                "the practice named above",
                practice.legal_name,
              )}
            </CheckRow>
          ))}
        </div>
        <div className="mt-5 grid gap-3">
          <Field label="Typed signature" htmlFor="sig">
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
        {error ? (
          <p className="text-destructive mt-3 text-sm">{error}</p>
        ) : null}
        <Button
          type="button"
          disabled={!ready || busy}
          onClick={() => void onAccept()}
          className="mt-5 w-full"
          size="lg"
        >
          {busy ? "Recording…" : `Accept BAA for ${practice.legal_name}`}
        </Button>
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
    if (!found) return
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
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-[var(--brand-gold)]">
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

    const { data: sasSignature } = await generateSignedExampleNoteUploadUrl()
    invariant(sasSignature, "SAS signature should exist")

    const { stampedFileName, error: uploadError } = await uploadFileToAzure(
      file,
      sasSignature,
    )

    if (uploadError) {
      toast({
        title: "Failed to upload note. Please try again.",
        variant: "destructive",
      })
      return
    }

    await uploadExampleNote({
      query: { file_name: stampedFileName, note_type: values.note_type },
    })

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
                  Click or drag a PDF (under 10MB)
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
    const res = await fetch(`${base}/internal/v1/zoom-auth-url`, {
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
    <p className="text-xs font-semibold uppercase tracking-[0.28em] text-[var(--brand-gold)]">
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
