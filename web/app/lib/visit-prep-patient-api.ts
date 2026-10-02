/** The public contract is separate from the account-authenticated generated SDK. */
import { z } from "zod"

import { isCanonicalVisitPrepToken } from "./visit-prep-browser"

const choice = z
  .object({
    value: z.string().min(1).max(80),
    label: z.string().min(1).max(500),
    hint: z.string().max(2_000).nullable(),
  })
  .strict()

const question = z
  .object({
    key: z.string().regex(/^[a-z][a-z0-9_]{0,63}$/),
    position: z.number().int().min(1).max(5),
    prompt: z.string().min(1).max(2_000),
    helper_text: z.string().max(2_000).nullable(),
    kind: z.enum(["story", "list", "choice"]),
    choices: z.array(choice).max(20),
  })
  .strict()

export const visitPrepPatientState = z
  .object({
    status: z.enum([
      "confirmation_required",
      "in_progress",
      "ready_to_complete",
      "completed",
    ]),
    physician_display_name: z.string().max(200).nullable(),
    practice_display_name: z.string().max(200).nullable(),
    scheduled_at: z.string().datetime({ offset: true }).nullable(),
    timezone_name: z.string().max(100).nullable(),
    submitted_questions: z.number().int().min(0).max(5),
    maximum_questions: z.literal(5),
    current_question: question.nullable(),
  })
  .strict()
  .superRefine((state, context) => {
    const invalid =
      (state.status === "in_progress" && state.current_question === null) ||
      (state.status !== "in_progress" && state.current_question !== null) ||
      (state.current_question?.kind !== "choice" &&
        (state.current_question?.choices.length ?? 0) > 0) ||
      (state.current_question?.kind === "choice" &&
        state.current_question.choices.length === 0) ||
      (state.current_question?.key === "story" &&
        state.current_question.kind !== "story") ||
      (state.status === "confirmation_required" &&
        (state.submitted_questions !== 0 ||
          state.physician_display_name !== null ||
          state.practice_display_name !== null ||
          state.scheduled_at !== null ||
          state.timezone_name !== null))
    if (invalid)
      context.addIssue({
        code: z.ZodIssueCode.custom,
        message: "Invalid Visit Prep state",
      })
  })

const mutation = z
  .object({
    interview: visitPrepPatientState,
    disposition: z.enum(["confirmed", "accepted", "replayed", "completed"]),
  })
  .strict()
const reported = z.object({ disposition: z.literal("reported") }).strict()

const contributionBasis = z
  .object({
    original_response_count: z.number().int().min(0).max(5),
    event_sequence: z.number().int().min(0),
  })
  .strict()
  .refine(
    (value) => value.original_response_count > 0 || value.event_sequence === 0,
  )
const responseKind = z.enum(["answered", "skipped"])
const reviewTopic = z
  .object({
    question,
    response_kind: responseKind.nullable(),
    answer: z.string().min(1).max(2_000).nullable(),
    initial_response_kind: responseKind.nullable(),
    initial_answer: z.string().min(1).max(2_000).nullable(),
    changed: z.boolean(),
    recorded_at: z.string().datetime({ offset: true }).nullable(),
  })
  .strict()
  .superRefine((topic, context) => {
    const validAnswer = (kind: string | null, answer: string | null) =>
      kind === "answered" ? answer !== null : answer === null
    if (
      !validAnswer(topic.response_kind, topic.answer) ||
      !validAnswer(topic.initial_response_kind, topic.initial_answer) ||
      (topic.question.kind !== "choice" &&
        topic.question.choices.length !== 0) ||
      (topic.question.kind === "choice" &&
        topic.question.choices.length === 0) ||
      topic.changed !==
        (topic.response_kind !== topic.initial_response_kind ||
          topic.answer !== topic.initial_answer)
    )
      context.addIssue({
        code: z.ZodIssueCode.custom,
        message: "Invalid Visit Prep topic",
      })
  })
export const visitPrepPatientContribution = z
  .object({
    basis: contributionBasis,
    actor_role: z.enum(["patient", "caregiver"]),
    original_interview_completed: z.boolean(),
    current_answers_complete: z.boolean(),
    current_answers_reviewed: z.boolean(),
    topics: z.array(reviewTopic).min(1).max(5),
  })
  .strict()
  .superRefine((value, context) => {
    const keys = value.topics.map((topic) => topic.question.key)
    const complete = value.topics.every((topic) => topic.response_kind !== null)
    if (
      new Set(keys).size !== keys.length ||
      value.topics.some(
        (topic, index) =>
          index > 0 &&
          topic.question.position <= value.topics[index - 1].question.position,
      ) ||
      !value.topics.some(
        (topic) =>
          topic.question.key === "story" && topic.question.kind === "story",
      ) ||
      value.current_answers_complete !== complete ||
      (value.current_answers_reviewed && !value.current_answers_complete)
    )
      context.addIssue({
        code: z.ZodIssueCode.custom,
        message: "Invalid Visit Prep contribution",
      })
  })
const contributionReceipt = z
  .object({
    disposition: z.enum(["accepted", "replayed"]),
    basis: contributionBasis,
    current_answers_complete: z.boolean(),
    current_answers_reviewed: z.boolean(),
  })
  .strict()
  .refine(
    (value) =>
      !value.current_answers_reviewed || value.current_answers_complete,
  )

export type PatientContribution = z.infer<typeof visitPrepPatientContribution>
export type PatientContributionReceipt = z.infer<typeof contributionReceipt>
export type PatientConfirmation = {
  first_name: string
  role: "patient" | "caregiver"
}

const consentTime = z.string().datetime({ offset: true })
const consentSequence = z.number().int().min(0).max(2_147_483_647)
export const visitPrepPatientEmailConsent = z
  .object({
    contract: z.literal("visit_prep.patient_email_consent.v1"),
    actor_role: z.enum(["patient", "caregiver"]),
    disclosure_version: z.literal("visit_prep_email_consent_v1"),
    grant_disclosure: z.string().min(1).max(8_000),
    grant_disclosure_fingerprint: z.string().regex(/^[0-9a-f]{64}$/),
    withdrawal_disclosure: z.string().min(1).max(8_000),
    withdrawal_disclosure_fingerprint: z.string().regex(/^[0-9a-f]{64}$/),
    status: z.enum(["not_recorded", "grant_recorded", "withdrawn", "expired"]),
    event_sequence: consentSequence,
    recorded_at: consentTime.nullable(),
    expires_at: consentTime.nullable(),
    maximum_expires_at: consentTime,
    can_grant: z.boolean(),
    checked_at: consentTime,
    identity_assurance: z.literal("invitation_bearer"),
    sending_authority: z.literal("not_qualified"),
    delivery_status: z.literal("not_connected"),
  })
  .strict()
  .superRefine((value, context) => {
    const checked = Date.parse(value.checked_at)
    const maximum = Date.parse(value.maximum_expires_at)
    const expiry =
      value.expires_at === null ? null : Date.parse(value.expires_at)
    if (
      value.grant_disclosure_fingerprint ===
        value.withdrawal_disclosure_fingerprint ||
      (value.event_sequence === 0
        ? value.status !== "not_recorded" ||
          value.recorded_at !== null ||
          expiry !== null
        : value.status === "not_recorded" || value.recorded_at === null) ||
      (value.recorded_at !== null && Date.parse(value.recorded_at) > checked) ||
      (value.status === "withdrawn" && expiry !== null) ||
      (["grant_recorded", "expired"].includes(value.status) &&
        expiry === null) ||
      (value.status === "grant_recorded" &&
        Math.min(expiry!, maximum) <= checked) ||
      (value.status === "expired" && Math.min(expiry!, maximum) > checked) ||
      (value.can_grant && maximum <= checked)
    )
      context.addIssue({
        code: z.ZodIssueCode.custom,
        message: "Invalid email choice",
      })
  })
export const visitPrepPatientEmailConsentReceipt = z
  .object({
    event_sequence: consentSequence.min(1),
    current_event_sequence: consentSequence.min(1),
    event_kind: z.enum(["granted", "withdrawn"]),
    expires_at: consentTime.nullable(),
    recorded_at: consentTime,
    disposition: z.enum(["recorded", "replayed"]),
  })
  .strict()
  .refine(
    (value) =>
      value.current_event_sequence >= value.event_sequence &&
      (value.event_kind === "withdrawn"
        ? value.expires_at === null
        : value.expires_at !== null) &&
      (value.disposition !== "recorded" ||
        value.current_event_sequence === value.event_sequence),
  )
export type PatientEmailConsent = z.infer<typeof visitPrepPatientEmailConsent>
export type PatientEmailConsentDecision = PatientConfirmation & {
  expected_event_sequence: number
  event_kind: "granted" | "withdrawn"
  disclosure_fingerprint: string
  expires_at: string | null
  recipient_control_attested: boolean
  caregiver_authority_attested: boolean
}
export type PatientContributionChange = PatientConfirmation &
  PatientResponse & {
    basis: PatientContribution["basis"]
  }
export type PatientContributionReview = PatientConfirmation & {
  basis: PatientContribution["basis"]
}

export type PatientInterviewState = z.infer<typeof visitPrepPatientState>
export type PatientInterviewQuestion = NonNullable<
  PatientInterviewState["current_question"]
>
export type PatientResponse = {
  question_key: string
  response_kind: "answered" | "skipped"
  answer: string | null
}
export type PatientFailureCode =
  | "unavailable"
  | "invalid"
  | "conflict"
  | "limited"
  | "temporary"

export class VisitPrepPatientFailure extends Error {
  constructor(readonly code: PatientFailureCode) {
    super("Visit Prep request unavailable")
    this.name = "VisitPrepPatientFailure"
  }
}

function safeBaseUrl(value: string): URL {
  try {
    const base = new URL(value === "/" ? window.location.origin : value)
    if (
      base.username ||
      base.password ||
      base.search ||
      base.hash ||
      base.pathname !== "/"
    )
      throw new Error()
    const local = ["localhost", "127.0.0.1", "[::1]"].includes(base.hostname)
    if (
      base.protocol !== "https:" &&
      !(import.meta.env.DEV && base.protocol === "http:" && local)
    )
      throw new Error()
    return base
  } catch {
    throw new VisitPrepPatientFailure("temporary")
  }
}

/** Holds its credential privately; methods return only validated public state. */
export function createVisitPrepPatientClient(
  baseUrl: string,
  accessToken: string,
  fetcher: typeof fetch = fetch,
) {
  if (!isCanonicalVisitPrepToken(accessToken))
    throw new VisitPrepPatientFailure("unavailable")
  const base = safeBaseUrl(baseUrl)
  let bearer: string | null = accessToken
  const controllers = new Set<AbortController>()

  async function request<T>(
    path: string,
    schema: z.ZodType<T>,
    body?: object,
    key?: string,
  ): Promise<T> {
    if (bearer === null) throw new VisitPrepPatientFailure("unavailable")
    const controller = new AbortController()
    controllers.add(controller)
    const timeout = window.setTimeout(() => controller.abort(), 15_000)
    try {
      const response = await fetcher(
        new URL(`/external/v1/visit-prep${path}`, base),
        {
          method: path === "" ? "GET" : "POST",
          headers: {
            Authorization: `Bearer ${bearer}`,
            ...(body ? { "Content-Type": "application/json" } : {}),
            ...(key ? { "Idempotency-Key": key } : {}),
          },
          ...(body ? { body: JSON.stringify(body) } : {}),
          // Existing signed cookies let Core deny active support view-as.
          // Account identity is never used as patient authority.
          credentials: "include",
          cache: "no-store",
          redirect: "error",
          referrerPolicy: "no-referrer",
          signal: controller.signal,
        },
      )
      if (!response.ok) {
        const code: PatientFailureCode = [403, 404].includes(response.status)
          ? "unavailable"
          : response.status === 409
            ? "conflict"
            : response.status === 429
              ? "limited"
              : [413, 422].includes(response.status)
                ? "invalid"
                : "temporary"
        // Never parse or expose arbitrary error bodies/status text.
        throw new VisitPrepPatientFailure(code)
      }
      const parsed = schema.safeParse(await response.json())
      if (!parsed.success || bearer === null)
        throw new VisitPrepPatientFailure("temporary")
      return parsed.data
    } catch (error) {
      if (error instanceof VisitPrepPatientFailure) throw error
      throw new VisitPrepPatientFailure("temporary")
    } finally {
      window.clearTimeout(timeout)
      controllers.delete(controller)
    }
  }

  return {
    resume: () => request("", visitPrepPatientState),
    confirm: async (first_name: string, role: "patient" | "caregiver") =>
      (await request("/confirm", mutation, { first_name, role })).interview,
    submit: async (payload: PatientResponse, key: string) =>
      (await request("/responses", mutation, payload, key)).interview,
    complete: async () => (await request("/complete", mutation)).interview,
    reportWrongRecipient: async () => {
      await request("/wrong-recipient", reported)
    },
    readContribution: (confirmation: PatientConfirmation) =>
      request("/contribution", visitPrepPatientContribution, confirmation),
    correctContribution: (payload: PatientContributionChange, key: string) =>
      request("/contribution/response", contributionReceipt, payload, key),
    reviewContribution: (payload: PatientContributionReview, key: string) =>
      request("/contribution/review", contributionReceipt, payload, key),
    readEmailConsent: (confirmation: PatientConfirmation) =>
      request("/email-consent", visitPrepPatientEmailConsent, confirmation),
    decideEmailConsent: (payload: PatientEmailConsentDecision, key: string) =>
      request(
        "/email-consent/decision",
        visitPrepPatientEmailConsentReceipt,
        payload,
        key,
      ),
    dispose: () => {
      bearer = null
      controllers.forEach((controller) => controller.abort())
      controllers.clear()
    },
  }
}

export type VisitPrepPatientClient = ReturnType<
  typeof createVisitPrepPatientClient
>
