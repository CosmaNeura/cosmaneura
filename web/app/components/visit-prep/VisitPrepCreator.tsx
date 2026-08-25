import { type FormEvent, useEffect, useRef, useState } from "react"

import { Copy, Link2, Loader2, ShieldX } from "lucide-react"

import { Button } from "~/components/ui/button"
import { Input } from "~/components/ui/input"
import { Label } from "~/components/ui/label"
import {
  type VisitPrepCommandStateResponse,
  type VisitPrepCreateRequest,
  type VisitPrepQuestionSetResponse,
  type VisitPrepQuestionSetVersionResponse,
  type VisitPrepSettingsData,
  visitPrepCopyLink,
  visitPrepCreate,
  visitPrepQuestionSetList,
  visitPrepRevoke,
} from "~/configuration/client"
import { toast } from "~/hooks/use-toast"
import { visitPrepScheduleFromLocal } from "~/lib/visit-prep-schedule"

type RecipientRole = "patient" | "caregiver"
type RevokeReason =
  | "physician_revoked"
  | "wrong_recipient"
  | "appointment_cancelled"
type BusyAction = "create" | "copy" | "replace" | "revoke" | null

type FormState = {
  firstName: string
  role: RecipientRole
  email: string
  phone: string
  physicianDisplayName: string
  scheduledLocal: string
  questionSetVersionId: string
}

const VISIT_PREP_TOKEN = /^vp1_[A-Za-z0-9_-]{43}$/u

function normalizedText(value: string): string {
  return value.replaceAll("\r\n", "\n").normalize("NFC").trim()
}

function publishedVersions(
  questionSets: VisitPrepQuestionSetResponse[],
): VisitPrepQuestionSetVersionResponse[] {
  return questionSets
    .flatMap((questionSet) => questionSet.versions)
    .filter((version) => version.published_at !== null)
    .sort((left, right) => {
      if (left.is_default !== right.is_default) return left.is_default ? -1 : 1
      const byName = left.name.localeCompare(right.name)
      return byName || right.version_number - left.version_number
    })
}

function createError(status: number | undefined): string {
  if (status === 403 || status === 404) {
    return "Visit Prep is unavailable in this session."
  }
  if (status === 409) {
    return "This request conflicts with the current Visit Prep state. Review it and try again."
  }
  if (status === 422) {
    return "Review the recipient and appointment details, then try again."
  }
  if (status === 503) return "Visit Prep is temporarily unavailable."
  return "The Visit Prep could not be created."
}

function commandError(
  status: number | undefined,
  action: "replace" | "revoke",
) {
  if (status === 403 || status === 404) {
    return "Visit Prep is unavailable in this session."
  }
  if (status === 409) {
    return action === "replace"
      ? "The link changed elsewhere. This page can no longer replace it."
      : "The Visit Prep changed elsewhere. This page can no longer revoke it."
  }
  if (status === 422) return "The Visit Prep request is no longer valid."
  if (status === 503) return "Visit Prep is temporarily unavailable."
  return action === "replace"
    ? "A replacement link could not be created."
    : "The Visit Prep could not be revoked."
}

function browserTimezoneName(): string | null {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || null
  } catch {
    return null
  }
}

function formatAppointment(value: string, timezoneName: string): string {
  try {
    return new Intl.DateTimeFormat(undefined, {
      timeZone: timezoneName,
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
      timeZoneName: "short",
    }).format(new Date(value))
  } catch {
    return "Appointment time unavailable"
  }
}

function verifiedAccessUrl(value: string): string | null {
  const configuredHost = import.meta.env.VITE_INTAKE_CHAT_DOMAIN?.trim()
  if (!configuredHost) return null

  try {
    const url = new URL(value)
    const fragment = url.hash.slice(1)
    const fragmentParameters = new URLSearchParams(fragment)
    const accessTokens = fragmentParameters.getAll("access_token")
    const onlyFragmentKey = [...fragmentParameters.keys()].every(
      (key) => key === "access_token",
    )
    const token = accessTokens[0]

    if (
      url.protocol !== "https:" ||
      url.username ||
      url.password ||
      url.host.toLowerCase() !== configuredHost.toLowerCase() ||
      url.pathname !== "/visit-prep" ||
      url.search ||
      accessTokens.length !== 1 ||
      !onlyFragmentKey ||
      !token ||
      !VISIT_PREP_TOKEN.test(token) ||
      fragment !== `access_token=${token}`
    ) {
      return null
    }
    return value
  } catch {
    return null
  }
}

function initialForm(physicianDisplayName: string): FormState {
  return {
    firstName: "",
    role: "patient",
    email: "",
    phone: "",
    physicianDisplayName: normalizedText(physicianDisplayName),
    scheduledLocal: "",
    questionSetVersionId: "",
  }
}

function validateAndBuildRequest(
  form: FormState,
): { request: VisitPrepCreateRequest } | { error: string } {
  const firstName = normalizedText(form.firstName)
  const physicianDisplayName = normalizedText(form.physicianDisplayName)
  const email = normalizedText(form.email).toLowerCase()
  const phone = normalizedText(form.phone)

  if (!firstName || firstName.length > 120) {
    return { error: "Enter the recipient's first name." }
  }
  if (!physicianDisplayName || physicianDisplayName.length > 160) {
    return { error: "Enter the physician name shown to the recipient." }
  }
  if (!email && !phone) {
    return { error: "Enter an email address or mobile number." }
  }
  if (email && (!/^\S+@\S+\.\S+$/u.test(email) || email.length > 320)) {
    return { error: "Enter a valid email address." }
  }
  if (phone && !/^\+[1-9][0-9]{7,14}$/u.test(phone)) {
    return {
      error: "Use E.164 format for mobile numbers, such as +14155550123.",
    }
  }
  if (!form.questionSetVersionId) {
    return { error: "Choose a published question set." }
  }

  const schedule = visitPrepScheduleFromLocal(form.scheduledLocal)
  if (!schedule.ok) {
    if (schedule.reason === "ambiguous") {
      return {
        error:
          "This time occurs twice during a clock change. Choose a time outside the repeated hour.",
      }
    }
    if (schedule.reason === "not_future") {
      return { error: "Choose a future appointment time." }
    }
    if (schedule.reason === "timezone") {
      return { error: "Your browser timezone could not be verified." }
    }
    return { error: "Enter a valid appointment date and time." }
  }

  return {
    request: {
      recipient: {
        first_name: firstName,
        role: form.role,
        email: email || null,
        phone: phone || null,
      },
      physician_display_name: physicianDisplayName,
      scheduled_at: schedule.scheduledAt,
      timezone_name: schedule.timezoneName,
      question_set_version_id: form.questionSetVersionId,
    },
  }
}

function CreatorUnavailable({
  eligible,
  canManage,
}: {
  eligible: boolean
  canManage: boolean
}) {
  return (
    <section className="space-y-2" aria-labelledby="visit-prep-create-title">
      <h2 id="visit-prep-create-title" className="text-lg font-semibold">
        Create Visit Prep
      </h2>
      <p className="text-muted-foreground text-sm">
        {eligible && !canManage
          ? "Visit Prep creation is read-only while using support view."
          : "Visit Prep is not available for this account."}
      </p>
    </section>
  )
}

export function VisitPrepCreator({
  access,
  physicianDisplayName = "",
}: {
  access: VisitPrepSettingsData
  physicianDisplayName?: string
}) {
  const [form, setForm] = useState(() => initialForm(physicianDisplayName))
  const [versions, setVersions] = useState<
    VisitPrepQuestionSetVersionResponse[]
  >([])
  const [loading, setLoading] = useState(access.eligible && access.can_manage)
  const [loadError, setLoadError] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const [busyAction, setBusyAction] = useState<BusyAction>(null)
  const [createdPrep, setCreatedPrep] =
    useState<VisitPrepCommandStateResponse | null>(null)
  const [linkAvailable, setLinkAvailable] = useState(false)
  const [linkWasCopied, setLinkWasCopied] = useState(false)
  const [revoked, setRevoked] = useState(false)
  const [revokeReason, setRevokeReason] =
    useState<RevokeReason>("physician_revoked")
  const accessUrlRef = useRef<string | null>(null)
  const idempotencyKeyRef = useRef<string | null>(null)
  const operationEpochRef = useRef(0)
  const detectedTimezone = browserTimezoneName()

  useEffect(() => {
    operationEpochRef.current += 1
    if (!access.eligible || !access.can_manage) {
      accessUrlRef.current = null
      idempotencyKeyRef.current = null
      setLinkAvailable(false)
      setLinkWasCopied(false)
      setCreatedPrep(null)
      setBusyAction(null)
      setActionError(null)
      setVersions([])
      setRevokeReason("physician_revoked")
      setForm((current) => ({
        ...current,
        firstName: "",
        email: "",
        phone: "",
        scheduledLocal: "",
        questionSetVersionId: "",
      }))
    }
    return () => {
      operationEpochRef.current += 1
      accessUrlRef.current = null
      idempotencyKeyRef.current = null
    }
  }, [access.can_manage, access.eligible])

  useEffect(() => {
    if (!physicianDisplayName) return
    setForm((current) =>
      current.physicianDisplayName
        ? current
        : {
            ...current,
            physicianDisplayName: normalizedText(physicianDisplayName),
          },
    )
  }, [physicianDisplayName])

  useEffect(() => {
    if (!access.eligible || !access.can_manage) return

    let active = true
    async function loadQuestionSets() {
      setLoading(true)
      setLoadError(false)
      try {
        const { data } = await visitPrepQuestionSetList()
        if (!active) return
        if (!data) {
          setLoadError(true)
          return
        }
        const available = publishedVersions(data.question_sets)
        setVersions(available)
        setForm((current) => ({
          ...current,
          questionSetVersionId: available.some(
            (version) => version.id === current.questionSetVersionId,
          )
            ? current.questionSetVersionId
            : (available[0]?.id ?? ""),
        }))
      } catch {
        if (active) setLoadError(true)
      } finally {
        if (active) setLoading(false)
      }
    }
    void loadQuestionSets()
    return () => {
      active = false
      accessUrlRef.current = null
    }
  }, [access.can_manage, access.eligible])

  function updateForm<Key extends keyof FormState>(
    key: Key,
    value: FormState[Key],
  ) {
    setForm((current) => ({ ...current, [key]: value }))
    idempotencyKeyRef.current = null
    setActionError(null)
  }

  async function createPrep(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const validated = validateAndBuildRequest(form)
    if ("error" in validated) {
      setActionError(validated.error)
      return
    }

    idempotencyKeyRef.current ??= crypto.randomUUID()

    setBusyAction("create")
    setActionError(null)
    const operationEpoch = operationEpochRef.current
    try {
      const { data, response } = await visitPrepCreate({
        body: validated.request,
        headers: { "Idempotency-Key": idempotencyKeyRef.current },
      })
      if (operationEpoch !== operationEpochRef.current) return
      if (!data) {
        setActionError(createError(response?.status))
        return
      }

      const accessUrl = data.access_url
        ? verifiedAccessUrl(data.access_url)
        : null
      accessUrlRef.current = accessUrl
      setLinkAvailable(accessUrl !== null)
      setLinkWasCopied(false)
      setCreatedPrep(data.visit_prep)
      setRevoked(!data.visit_prep.credential_active)
      setForm((current) => ({
        ...current,
        firstName: "",
        email: "",
        phone: "",
        scheduledLocal: "",
      }))
      idempotencyKeyRef.current = null
      if (data.access_url !== null && !accessUrl) {
        setActionError(
          "The returned link could not be verified. Create a replacement link.",
        )
      }
      toast({
        title:
          data.disposition === "replayed"
            ? "Visit Prep recovered"
            : "Visit Prep created",
      })
    } catch {
      if (operationEpoch === operationEpochRef.current) {
        setActionError(createError(undefined))
      }
    } finally {
      if (operationEpoch === operationEpochRef.current) setBusyAction(null)
    }
  }

  async function copyLink() {
    const accessUrl = accessUrlRef.current
    if (!accessUrl) {
      setActionError("No link is available to copy. Create a replacement link.")
      return
    }
    setBusyAction("copy")
    setActionError(null)
    const operationEpoch = operationEpochRef.current
    try {
      await navigator.clipboard.writeText(accessUrl)
      if (operationEpoch !== operationEpochRef.current) return
      accessUrlRef.current = null
      setLinkAvailable(false)
      setLinkWasCopied(true)
      toast({ title: "Visit Prep link copied" })
    } catch {
      if (operationEpoch === operationEpochRef.current) {
        setActionError("The link could not be copied. Try again in this tab.")
      }
    } finally {
      if (operationEpoch === operationEpochRef.current) setBusyAction(null)
    }
  }

  async function replaceLink() {
    if (!createdPrep || revoked) return
    if (
      !window.confirm(
        "Create a replacement link? The previous Visit Prep link will stop working.",
      )
    ) {
      return
    }

    accessUrlRef.current = null
    setLinkAvailable(false)
    setLinkWasCopied(false)
    setBusyAction("replace")
    setActionError(null)
    const operationEpoch = operationEpochRef.current
    try {
      const { data, response } = await visitPrepCopyLink({
        path: { prep_id: createdPrep.id },
        body: {
          expected_credential_generation: createdPrep.credential_generation,
        },
      })
      if (operationEpoch !== operationEpochRef.current) return
      if (!data) {
        setActionError(commandError(response?.status, "replace"))
        return
      }
      setCreatedPrep(data.visit_prep)
      const accessUrl = data.access_url
        ? verifiedAccessUrl(data.access_url)
        : null
      if (!accessUrl) {
        setActionError(
          "The replacement link could not be verified. Create another replacement link.",
        )
        return
      }
      accessUrlRef.current = accessUrl
      setLinkAvailable(true)
      setLinkWasCopied(false)
      toast({ title: "Replacement Visit Prep link ready" })
    } catch {
      if (operationEpoch === operationEpochRef.current) {
        setActionError(commandError(undefined, "replace"))
      }
    } finally {
      if (operationEpoch === operationEpochRef.current) setBusyAction(null)
    }
  }

  async function revokePrep() {
    if (!createdPrep || revoked) return
    if (
      !window.confirm("Revoke this Visit Prep link? This cannot be undone.")
    ) {
      return
    }

    accessUrlRef.current = null
    setLinkAvailable(false)
    setLinkWasCopied(false)
    setBusyAction("revoke")
    setActionError(null)
    const operationEpoch = operationEpochRef.current
    try {
      const { data, response } = await visitPrepRevoke({
        path: { prep_id: createdPrep.id },
        body: {
          expected_credential_generation: createdPrep.credential_generation,
          reason: revokeReason,
        },
      })
      if (operationEpoch !== operationEpochRef.current) return
      if (!data) {
        setActionError(commandError(response?.status, "revoke"))
        return
      }
      setCreatedPrep(data.visit_prep)
      setRevoked(true)
      toast({ title: "Visit Prep revoked" })
    } catch {
      if (operationEpoch === operationEpochRef.current) {
        setActionError(commandError(undefined, "revoke"))
      }
    } finally {
      if (operationEpoch === operationEpochRef.current) setBusyAction(null)
    }
  }

  if (!access.eligible || !access.can_manage) {
    return (
      <CreatorUnavailable
        eligible={access.eligible}
        canManage={access.can_manage}
      />
    )
  }

  return (
    <section
      className="space-y-5"
      aria-labelledby="visit-prep-create-title"
      data-sentry-block=""
      data-sentry-mask=""
      data-ph-no-capture=""
    >
      <div className="space-y-1">
        <h2 id="visit-prep-create-title" className="text-lg font-semibold">
          Create Visit Prep
        </h2>
        <p className="text-muted-foreground max-w-2xl text-sm">
          Create a five-question prep, copy its private link, and send it using
          your usual secure channel. CosmaNeura delivery is not enabled yet.
        </p>
      </div>

      {loading ? (
        <p className="text-muted-foreground flex items-center gap-2 text-sm">
          <Loader2 className="size-4 animate-spin" aria-hidden="true" />
          Loading published question sets…
        </p>
      ) : loadError ? (
        <p role="alert" className="text-destructive text-sm">
          Published question sets could not be loaded. Refresh to try again.
        </p>
      ) : versions.length === 0 ? (
        <p role="status" className="text-muted-foreground text-sm">
          Publish a five-question set in Settings before creating a Visit Prep.
        </p>
      ) : createdPrep ? (
        <div className="max-w-2xl space-y-4 rounded-md border p-4">
          <div className="space-y-1">
            <h3 className="font-medium">
              {revoked ? "Visit Prep revoked" : "Visit Prep ready"}
            </h3>
            <p className="text-muted-foreground text-sm">
              Appointment:{" "}
              {formatAppointment(
                createdPrep.scheduled_at,
                createdPrep.timezone_name,
              )}
            </p>
            {!revoked ? (
              <p className="text-muted-foreground text-sm">
                The private link expires 72 hours after the appointment.
              </p>
            ) : null}
          </div>

          {!revoked ? (
            <>
              {linkAvailable ? (
                <div className="flex flex-wrap gap-2">
                  <Button
                    type="button"
                    onClick={copyLink}
                    disabled={busyAction !== null}
                  >
                    {busyAction === "copy" ? (
                      <Loader2
                        className="size-4 animate-spin"
                        aria-hidden="true"
                      />
                    ) : (
                      <Copy className="size-4" aria-hidden="true" />
                    )}
                    Copy private link
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    onClick={replaceLink}
                    disabled={busyAction !== null}
                  >
                    <Link2 className="size-4" aria-hidden="true" />
                    Replace link
                  </Button>
                </div>
              ) : (
                <div className="space-y-2">
                  <p className="text-muted-foreground text-sm">
                    {linkWasCopied
                      ? "The link was copied and is no longer held in this tab. Create a replacement only if you need to invalidate it."
                      : "The one-time link is not available in this tab. Create a replacement to invalidate the prior link."}
                  </p>
                  <Button
                    type="button"
                    variant="outline"
                    onClick={replaceLink}
                    disabled={busyAction !== null}
                  >
                    <Link2 className="size-4" aria-hidden="true" />
                    Create replacement link
                  </Button>
                </div>
              )}

              <div className="space-y-2 border-t pt-4">
                <Label htmlFor="visit-prep-revoke-reason">Revoke reason</Label>
                <div className="flex flex-wrap items-end gap-2">
                  <select
                    id="visit-prep-revoke-reason"
                    className="border-input bg-background h-9 min-w-56 rounded-md border px-3 text-sm"
                    value={revokeReason}
                    onChange={(event) =>
                      setRevokeReason(event.target.value as RevokeReason)
                    }
                    disabled={busyAction !== null}
                  >
                    <option value="physician_revoked">No longer needed</option>
                    <option value="wrong_recipient">Wrong recipient</option>
                    <option value="appointment_cancelled">
                      Appointment cancelled
                    </option>
                  </select>
                  <Button
                    type="button"
                    variant="destructive"
                    onClick={revokePrep}
                    disabled={busyAction !== null}
                  >
                    <ShieldX className="size-4" aria-hidden="true" />
                    Revoke Visit Prep
                  </Button>
                </div>
              </div>
            </>
          ) : null}

          <Button type="button" variant="outline" disabled>
            Send unavailable
          </Button>
          <p className="text-muted-foreground text-xs">
            For this dark-release layer, these controls remain available only in
            this tab. Visit Prep history arrives with the dashboard layer.
          </p>
        </div>
      ) : (
        <form
          className="max-w-2xl space-y-4"
          onSubmit={createPrep}
          autoComplete="off"
          noValidate
        >
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="visit-prep-first-name">
                Recipient first name
              </Label>
              <Input
                id="visit-prep-first-name"
                autoComplete="off"
                spellCheck={false}
                maxLength={120}
                value={form.firstName}
                disabled={busyAction !== null}
                onChange={(event) =>
                  updateForm("firstName", event.target.value)
                }
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="visit-prep-role">Recipient</Label>
              <select
                id="visit-prep-role"
                className="border-input bg-background h-9 w-full rounded-md border px-3 text-sm"
                value={form.role}
                disabled={busyAction !== null}
                onChange={(event) =>
                  updateForm("role", event.target.value as RecipientRole)
                }
              >
                <option value="patient">Patient</option>
                <option value="caregiver">Caregiver</option>
              </select>
            </div>
            <div className="space-y-2">
              <Label htmlFor="visit-prep-email">Email</Label>
              <Input
                id="visit-prep-email"
                type="email"
                autoComplete="off"
                spellCheck={false}
                maxLength={320}
                value={form.email}
                disabled={busyAction !== null}
                onChange={(event) => updateForm("email", event.target.value)}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="visit-prep-phone">Mobile number (optional)</Label>
              <Input
                id="visit-prep-phone"
                type="tel"
                autoComplete="off"
                spellCheck={false}
                maxLength={16}
                placeholder="+14155550123"
                value={form.phone}
                disabled={busyAction !== null}
                onChange={(event) => updateForm("phone", event.target.value)}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="visit-prep-appointment">Appointment</Label>
              <Input
                id="visit-prep-appointment"
                type="datetime-local"
                aria-describedby="visit-prep-appointment-timezone"
                value={form.scheduledLocal}
                disabled={busyAction !== null}
                onChange={(event) =>
                  updateForm("scheduledLocal", event.target.value)
                }
              />
              <p
                id="visit-prep-appointment-timezone"
                className="text-muted-foreground text-xs"
              >
                {detectedTimezone
                  ? `Appointment timezone: ${detectedTimezone}`
                  : "Appointment timezone unavailable"}
              </p>
            </div>
            <div className="space-y-2">
              <Label htmlFor="visit-prep-question-set">Question set</Label>
              <select
                id="visit-prep-question-set"
                className="border-input bg-background h-9 w-full rounded-md border px-3 text-sm"
                value={form.questionSetVersionId}
                disabled={busyAction !== null}
                onChange={(event) =>
                  updateForm("questionSetVersionId", event.target.value)
                }
              >
                {versions.map((version) => (
                  <option key={version.id} value={version.id}>
                    {version.name} · v{version.version_number}
                    {version.is_default ? " · Default" : ""}
                  </option>
                ))}
              </select>
            </div>
          </div>

          <div className="space-y-2">
            <Label htmlFor="visit-prep-physician-name">
              Physician name shown to recipient
            </Label>
            <Input
              id="visit-prep-physician-name"
              autoComplete="name"
              maxLength={160}
              value={form.physicianDisplayName}
              disabled={busyAction !== null}
              onChange={(event) =>
                updateForm("physicianDisplayName", event.target.value)
              }
            />
          </div>

          {actionError ? (
            <p role="alert" className="text-destructive text-sm">
              {actionError}
            </p>
          ) : null}

          <div className="flex flex-wrap items-center gap-2">
            <Button type="submit" disabled={busyAction !== null}>
              {busyAction === "create" ? (
                <Loader2 className="size-4 animate-spin" aria-hidden="true" />
              ) : null}
              Create Visit Prep
            </Button>
            <Button type="button" variant="outline" disabled>
              Send unavailable
            </Button>
          </div>
        </form>
      )}

      {actionError && createdPrep ? (
        <p role="alert" className="text-destructive text-sm">
          {actionError}
        </p>
      ) : null}
    </section>
  )
}
