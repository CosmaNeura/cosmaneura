import { z } from "zod"

const MAX_RESPONSE_BYTES = 256 * 1024
const MAX_MESSAGES_BYTES = 192 * 1024
const opaqueRef = z.string().regex(/^[A-Za-z0-9._~-]{8,160}$/)
const clientTurnId = z
  .string()
  .regex(
    /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
  )
const timestamp = z
  .string()
  .regex(
    /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/,
  )
  .refine((value) => Number.isFinite(Date.parse(value)))
// The receiver counts Unicode code points, not JavaScript UTF-16 code units.
const text = (maximum: number) =>
  z
    .string()
    .min(1)
    .max(maximum * 2)
    .refine((value) => Array.from(value).length <= maximum)
const suppressions = [
  "disclaimer",
  "intent_badge",
  "doctrinal_badge",
  "citations",
  "structured_cards",
  "feature_actions",
  "guided_paths",
] as const
const presentation = z
  .object({
    mode: z.enum(["standard", "emergency", "sensitive_disclosure"]),
    suppress: z.array(z.enum(suppressions)).max(7),
    human_contact: z
      .object({
        label: text(120),
        action_id: z.string().regex(/^[A-Za-z0-9._~-]{8,128}$/),
        fallback_text: text(240),
      })
      .strict()
      .nullable(),
  })
  .strict()
  .refine(
    (value) =>
      new Set(value.suppress).size === value.suppress.length &&
      (value.mode !== "sensitive_disclosure" ||
        (value.human_contact !== null &&
          value.suppress.length === suppressions.length)),
  )
const message = z
  .object({
    turn_ref: opaqueRef,
    client_turn_id: clientTurnId,
    role: z.enum(["user", "assistant"]),
    content: text(20_000),
    recorded_at: timestamp,
    presentation: presentation.nullable(),
  })
  .strict()
  .refine((value) => (value.role === "user") === (value.presentation === null))
const turn = z
  .object({
    turn_ref: opaqueRef,
    client_turn_id: clientTurnId,
    state: z.enum(["pending", "completed", "stale"]),
    outcome: z.enum(["completed", "refused", "unavailable"]).nullable(),
    recorded_at: timestamp,
  })
  .strict()
  .refine((value) => (value.state === "completed") === (value.outcome !== null))
const historyEnvelope = z
  .object({
    schema_version: z.literal("1.0.0"),
    history: z
      .object({
        thread_ref: opaqueRef,
        messages: z.array(message).max(12),
        next_cursor: z.null(),
      })
      .strict(),
    turns: z.array(turn).max(6),
  })
  .strict()
  .refine((value) => {
    if (
      new TextEncoder().encode(JSON.stringify(value.history.messages)).length >
      MAX_MESSAGES_BYTES
    )
      return false
    const ids = new Set<string>(),
      refs = new Set<string>()
    let position = 0
    for (const status of value.turns) {
      if (ids.has(status.client_turn_id) || refs.has(status.turn_ref))
        return false
      ids.add(status.client_turn_id)
      refs.add(status.turn_ref)
      const next = value.history.messages[position]
      const roles =
        status.state === "completed"
          ? ["user", "assistant"]
          : next?.turn_ref === status.turn_ref
            ? ["user"]
            : []
      for (const role of roles) {
        const item = value.history.messages[position++]
        if (
          !item ||
          item.turn_ref !== status.turn_ref ||
          item.client_turn_id !== status.client_turn_id ||
          item.role !== role
        )
          return false
      }
    }
    return position === value.history.messages.length
  })

// Shared structural validation only. Citation display uses a separate read lane.
export const sovereignHistoryMessageSchema = message
export const sovereignHistoryTurnSchema = turn
export function parseSovereignHistory(value: unknown): SovereignHistory {
  const parsed = historyEnvelope.safeParse(value)
  if (!parsed.success) throw new Error("Recent conversation is unavailable")
  return parsed.data
}

export type SovereignHistory = z.infer<typeof historyEnvelope>
export class HistoryAccessDeniedError extends Error {}

export async function loadSovereignHistory({
  origin,
  getToken,
  signal,
  assertCurrent,
}: {
  origin: string
  getToken: () => Promise<string>
  signal: AbortSignal
  assertCurrent: () => void
}): Promise<SovereignHistory> {
  const check = () => {
    signal.throwIfAborted()
    assertCurrent()
  }
  check()
  const token = await getToken()
  check()
  const response = await fetch(`${origin}/embed/history`, {
    method: "POST",
    credentials: "omit",
    cache: "no-store",
    redirect: "error",
    referrerPolicy: "no-referrer",
    signal,
    headers: {
      Accept: "application/json",
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
      "X-Cosmaneura-Embed-Version": "1.0",
    },
    // The current mount is the only authority/encounter selector. Never send
    // browser messages, a record ID, a thread reference, or a history cursor.
    body: JSON.stringify({
      schema_version: "1.0.0",
      mode: "recent",
      client_turn_id: null,
    }),
  })
  const cancelBody = () => {
    void response.body?.cancel().catch(() => undefined)
  }
  try {
    check()
  } catch (error) {
    cancelBody()
    throw error
  }
  if (response.status === 401 || response.status === 403) {
    cancelBody()
    throw new HistoryAccessDeniedError("Assistant access is unavailable")
  }
  const length = response.headers.get("Content-Length")
  if (
    !response.ok ||
    response.headers.get("X-Cosmaneura-Embed-Version") !== "1.0" ||
    response.headers.get("Content-Type")?.split(";")[0].trim().toLowerCase() !==
      "application/json" ||
    (length !== null &&
      (!/^\d+$/.test(length) || Number(length) > MAX_RESPONSE_BYTES)) ||
    !response.body
  ) {
    cancelBody()
    throw new Error("Recent conversation is unavailable")
  }
  const reader = response.body.getReader()
  const cancel = () => {
    void reader.cancel().catch(() => undefined)
  }
  signal.addEventListener("abort", cancel, { once: true })
  let size = 0,
    body = ""
  const decoder = new TextDecoder("utf-8", { fatal: true })
  try {
    check()
    for (;;) {
      const chunk = await reader.read()
      check()
      if (chunk.done) break
      size += chunk.value.byteLength
      if (size > MAX_RESPONSE_BYTES)
        throw new Error("Recent conversation is unavailable")
      body += decoder.decode(chunk.value, { stream: true })
    }
    body += decoder.decode()
    const parsed = historyEnvelope.safeParse(JSON.parse(body))
    if (!parsed.success) throw new Error("Recent conversation is unavailable")
    check()
    return parsed.data
  } catch {
    // Never propagate schema diagnostics or response content to the UI/logs.
    check()
    throw new Error("Recent conversation is unavailable")
  } finally {
    signal.removeEventListener("abort", cancel)
    cancel()
  }
}
