import { z } from "zod"

import {
  HistoryAccessDeniedError,
  parseSovereignHistory,
  sovereignHistoryMessageSchema,
  sovereignHistoryTurnSchema,
} from "./SovereignHistory"

const timestamp = z
  .string()
  .regex(
    /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/,
  )
  .refine((value) => Number.isFinite(Date.parse(value)))
const reference =
  /^conversation_final_phase_[0-9a-hjkmnp-tv-z]{26}\.([1-9][0-9]?)$/
export const displayCitationSchema = z
  .object({
    citation_ref: z.string().max(160).regex(reference),
    ordinal: z.number().int().min(1).max(64),
    source_at: timestamp,
    reviewed_at: timestamp,
    section: z
      .string()
      .min(1)
      .max(400)
      .refine((value) => Array.from(value).length <= 200),
  })
  .strict()
  .refine((value) => value.citation_ref.endsWith(`.${value.ordinal}`))
const citationMap = z
  .array(displayCitationSchema)
  .max(64)
  .refine((items) => {
    const prefix = items[0]?.citation_ref.slice(
      0,
      items[0].citation_ref.lastIndexOf("."),
    )
    return items.every(
      (item, index) =>
        item.ordinal === index + 1 &&
        item.citation_ref === `${prefix}.${index + 1}`,
    )
  })
const freshMessage = sovereignHistoryMessageSchema
  .safeExtend({ reviewed_citations: citationMap })
  .refine(
    (value) => value.role !== "user" || value.reviewed_citations.length === 0,
  )
const retainedMessage = sovereignHistoryMessageSchema
  .safeExtend({
    reviewed_citations: citationMap.nullable(),
    display_state: z.enum(["historical", "superseded"]).nullable(),
  })
  .refine((value) =>
    value.role === "user"
      ? value.reviewed_citations === null && value.display_state === null
      : value.reviewed_citations !== null && value.display_state !== null,
  )

function envelope<T extends typeof freshMessage | typeof retainedMessage>(
  version: "1.1.0" | "1.2.0",
  message: T,
) {
  return z
    .object({
      schema_version: z.literal(version),
      history: z
        .object({
          thread_ref: z.string(),
          next_cursor: z.null(),
          messages: z.array(message).max(12),
        })
        .strict(),
      turns: z.array(sovereignHistoryTurnSchema).max(6),
    })
    .strict()
    .superRefine((value, ctx) => {
      try {
        const legacy = parseSovereignHistory({
          ...value,
          schema_version: "1.0.0",
          history: {
            ...value.history,
            messages: value.history.messages.map((item) => ({
              turn_ref: item.turn_ref,
              client_turn_id: item.client_turn_id,
              role: item.role,
              content: item.content,
              recorded_at: item.recorded_at,
              presentation: item.presentation,
            })),
          },
        })
        if (
          new TextEncoder().encode(JSON.stringify(value.history.messages))
            .length >
          192 * 1024
        )
          throw new Error()
        for (const status of legacy.turns) {
          const assistant = value.history.messages.find(
            (item) =>
              item.role === "assistant" &&
              item.client_turn_id === status.client_turn_id,
          )
          if (
            assistant?.reviewed_citations?.length &&
            status.outcome !== "completed"
          )
            throw new Error()
        }
      } catch {
        ctx.addIssue({ code: "custom", message: "Display unavailable" })
      }
    })
}
const fresh = envelope("1.1.0", freshMessage)
const retained = envelope("1.2.0", retainedMessage)
export type CitationDisplay = z.infer<typeof fresh> | z.infer<typeof retained>
export type CitationDisplayOptions = {
  version: "1.1.0" | "1.2.0"
  mode: "recent" | "turn"
  clientTurnId: string | null
  signal: AbortSignal
}
export function parseCitationDisplay(
  value: unknown,
  version: "1.1.0" | "1.2.0",
): CitationDisplay {
  const parsed = (version === "1.1.0" ? fresh : retained).safeParse(value)
  if (!parsed.success) throw new Error("Reviewed-source display is unavailable")
  // Turns were checked with the unchanged canonical history schema above.
  return parsed.data
}
export function citationHref(referenceValue: string): string {
  const match = reference.exec(referenceValue)
  if (!match || Number(match[1]) > 64)
    throw new Error("Reviewed source is unavailable")
  return new URL(
    `/session-prep/citations/${encodeURIComponent(referenceValue)}`,
    window.location.origin,
  ).href
}

export async function loadCitationDisplay({
  origin,
  getToken,
  assertCurrent,
  options,
}: {
  origin: string
  getToken: () => Promise<string>
  assertCurrent: () => void
  options: CitationDisplayOptions
}): Promise<CitationDisplay> {
  const { signal, version, mode, clientTurnId } = options
  const check = () => {
    signal.throwIfAborted()
    assertCurrent()
  }
  if (
    mode === "recent"
      ? clientTurnId !== null
      : typeof clientTurnId !== "string" ||
        !/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(
          clientTurnId,
        )
  )
    throw new Error("Invalid display request")
  check()
  const token = await getToken()
  check()
  const response = await fetch(
    `${origin}/embed/${version === "1.1.0" ? "history-with-citations" : "history-retained"}`,
    {
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
      body: JSON.stringify({
        schema_version: version,
        mode,
        client_turn_id: clientTurnId,
      }),
    },
  )
  let reader: ReadableStreamDefaultReader<Uint8Array> | undefined
  const cancel = () => {
    void (reader ? reader.cancel() : response.body?.cancel())?.catch(
      () => undefined,
    )
  }
  try {
    check()
    if (response.status === 401 || response.status === 403)
      throw new HistoryAccessDeniedError("Assistant access is unavailable")
    const length = response.headers.get("Content-Length")
    if (
      !response.ok ||
      response.headers.get("X-Cosmaneura-Embed-Version") !== "1.0" ||
      response.headers
        .get("Content-Type")
        ?.split(";")[0]
        .trim()
        .toLowerCase() !== "application/json" ||
      (length !== null &&
        (!/^\d+$/.test(length) || Number(length) > 256 * 1024)) ||
      !response.body
    )
      throw new Error()
    reader = response.body.getReader()
    signal.addEventListener("abort", cancel, { once: true })
    const decoder = new TextDecoder("utf-8", { fatal: true })
    let body = "",
      size = 0
    for (;;) {
      const chunk = await reader.read()
      check()
      if (chunk.done) break
      size += chunk.value.byteLength
      if (size > 256 * 1024) throw new Error()
      body += decoder.decode(chunk.value, { stream: true })
    }
    body += decoder.decode()
    const value = parseCitationDisplay(JSON.parse(body), version)
    const legacy = parseSovereignHistory({
      ...value,
      schema_version: "1.0.0",
      history: {
        ...value.history,
        messages: value.history.messages.map(
          ({ reviewed_citations: _citations, ...message }) => {
            const { display_state: _state, ...original } =
              message as typeof message & { display_state?: unknown }
            return original
          },
        ),
      },
    })
    if (
      mode === "turn" &&
      (legacy.turns.length !== 1 ||
        legacy.turns[0].client_turn_id !== clientTurnId)
    )
      throw new Error()
    check()
    return value
  } catch (error) {
    check()
    if (error instanceof HistoryAccessDeniedError) throw error
    throw new Error("Reviewed-source display is unavailable")
  } finally {
    signal.removeEventListener("abort", cancel)
    cancel()
  }
}
