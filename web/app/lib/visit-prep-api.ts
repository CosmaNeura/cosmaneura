/** Thin Visit Prep clients — avoids regenerating the OpenAPI SDK in this slice. */
import { z } from "zod"

import { getClient } from "~/configuration/clerk"
import { internalApiFetch } from "~/lib/billing-recovery"
import { requireEnv } from "~/utils/environment"

const TOKEN = /^vp1_[A-Za-z0-9_-]{43}$/
export const isCanonicalVisitPrepToken = (v: string) => TOKEN.test(v)
export const isVisitPrepPatientPath = (p: string) =>
  p === "/visit-prep" || p.startsWith("/visit-prep/")

let credential: string | null = null
export function captureVisitPrepCredential(
  b: Pick<Window, "location" | "history">,
) {
  if (!isVisitPrepPatientPath(b.location.pathname)) return
  const { pathname, search, hash } = b.location
  b.history.replaceState(b.history.state, "", pathname)
  credential = null
  if (pathname !== "/visit-prep" || search) return
  const m = /^#access_token=(vp1_[A-Za-z0-9_-]{43})$/.exec(hash)
  if (m && isCanonicalVisitPrepToken(m[1])) credential = m[1]
}
export const readVisitPrepCredential = () => credential
export const forgetVisitPrepCredential = () => {
  credential = null
}
if (typeof window !== "undefined") {
  captureVisitPrepCredential(window)
  window.addEventListener("pagehide", forgetVisitPrepCredential)
}

const question = z.object({
  key: z.string(),
  position: z.number(),
  prompt: z.string(),
  helper_text: z.string().nullable(),
  kind: z.enum(["story", "list", "choice"]),
  choices: z.array(
    z.object({
      value: z.string(),
      label: z.string(),
      hint: z.string().nullable().optional(),
    }),
  ),
})
export const interviewState = z.object({
  status: z.enum([
    "confirmation_required",
    "in_progress",
    "ready_to_complete",
    "completed",
  ]),
  physician_display_name: z.string().nullable(),
  practice_display_name: z.string().nullable(),
  scheduled_at: z.string().nullable(),
  timezone_name: z.string().nullable(),
  submitted_questions: z.number(),
  maximum_questions: z.literal(5),
  current_question: question.nullable(),
})
export type InterviewState = z.infer<typeof interviewState>

export class VisitPrepPatientFailure extends Error {
  constructor(
    readonly code: "unavailable" | "invalid" | "conflict" | "temporary",
  ) {
    super(code)
    this.name = "VisitPrepPatientFailure"
  }
}
const fail = (s: number) =>
  new VisitPrepPatientFailure(
    [403, 404].includes(s)
      ? "unavailable"
      : s === 409
        ? "conflict"
        : [413, 422].includes(s)
          ? "invalid"
          : "temporary",
  )

export function createVisitPrepPatientClient(
  baseUrl: string,
  accessToken: string,
  fetcher: typeof fetch = fetch,
) {
  if (!isCanonicalVisitPrepToken(accessToken))
    throw new VisitPrepPatientFailure("unavailable")
  const origin = new URL(baseUrl === "/" ? window.location.origin : baseUrl)
  let bearer: string | null = accessToken
  async function request<T>(
    path: string,
    schema: z.ZodType<T>,
    body?: object,
    key?: string,
  ) {
    if (!bearer) throw new VisitPrepPatientFailure("unavailable")
    const res = await fetcher(
      new URL(`/external/v1/visit-prep${path}`, origin),
      {
        method: path === "" ? "GET" : "POST",
        headers: {
          Authorization: `Bearer ${bearer}`,
          ...(body ? { "Content-Type": "application/json" } : {}),
          ...(key ? { "Idempotency-Key": key } : {}),
        },
        body: body ? JSON.stringify(body) : undefined,
        credentials: "include",
        cache: "no-store",
      },
    )
    if (!res.ok) throw fail(res.status)
    const parsed = schema.safeParse(await res.json())
    if (!parsed.success) throw new VisitPrepPatientFailure("temporary")
    return parsed.data
  }
  const mutation = z.object({
    interview: interviewState,
    disposition: z.string(),
  })
  return {
    resume: () => request("", interviewState),
    confirm: async (first_name: string, role: "patient" | "caregiver") =>
      (await request("/confirm", mutation, { first_name, role })).interview,
    submit: async (
      payload: {
        question_key: string
        response_kind: "answered" | "skipped"
        answer: string | null
      },
      key: string,
    ) => (await request("/responses", mutation, payload, key)).interview,
    complete: async () => (await request("/complete", mutation)).interview,
    dispose: () => {
      bearer = null
    },
  }
}

export async function providerJson<T>(
  path: string,
  init?: { method?: string; json?: unknown },
): Promise<T> {
  const clerk = await getClient()
  const token = await clerk!.session!.getToken()
  const response = await internalApiFetch(
    `${requireEnv("VITE_PYTHON_URL").replace(/\/$/, "")}${path}`,
    {
      method: init?.method ?? "GET",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
      },
      body: init?.json === undefined ? undefined : JSON.stringify(init.json),
    },
  )
  if (!response.ok)
    throw new Error(`Visit Prep request failed (${response.status})`)
  return (await response.json()) as T
}

export type PrepPayload = {
  id: string
  access_url: string | null
  access_token?: string | null
  lifecycle_state: string
  question_set_version_id: string
  can_send_email: boolean
  can_send_sms: boolean
}
export type PackagePayload = {
  contract: string
  package_id: string
  raw_answers_always_available: boolean
  topics: Array<{
    question_key: string
    prompt: string
    response_kind: string | null
    answer: string | null
    recorded_at: string | null
  }>
  synthesis: {
    status: string
    brief: {
      headline: { text: string } | null
      summary: Array<{ text: string }>
    }
  }
}
