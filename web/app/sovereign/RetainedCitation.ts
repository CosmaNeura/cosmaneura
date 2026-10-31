import { z } from "zod"

import { displayCitationSchema } from "./SovereignCitationDisplay"

export function citationCoordinates(reference: string): {
  final_ref: string
  ordinal: number
} {
  const match =
    /^(conversation_final_phase_[0-9a-hjkmnp-tv-z]{26})\.([1-9][0-9]?)$/.exec(
      reference,
    )
  if (!match || Number(match[2]) > 64)
    throw new Error("Reviewed source is unavailable")
  return { final_ref: match[1], ordinal: Number(match[2]) }
}
const section = z
  .object({
    title: z
      .string()
      .min(1)
      .max(400)
      .refine((value) => Array.from(value).length <= 200),
    text: z
      .string()
      .max(36_000)
      .refine((value) => Array.from(value).length <= 18_000),
  })
  .strict()
const retained = z
  .object({
    schema_version: z.literal("1.0.0"),
    citation: displayCitationSchema,
    content_sha256: z.string().regex(/^[0-9a-f]{64}$/),
    sections: z.array(section).min(1).max(64),
  })
  .strict()
export type RetainedCitation = z.infer<typeof retained>

/** Verify the retained bytes, never substitute a current note with the same title/date. */
export async function parseRetainedCitation(
  value: unknown,
  reference: string,
): Promise<RetainedCitation> {
  const parsed = retained.safeParse(value)
  if (!parsed.success) throw new Error("Reviewed source is unavailable")
  const data = parsed.data
  const coordinates = citationCoordinates(reference)
  const encoded = JSON.stringify(
    data.sections.map((item) => [item.title, item.text]),
  )
  if (
    data.citation.citation_ref !== reference ||
    data.citation.ordinal !== coordinates.ordinal ||
    new Set(data.sections.map((item) => item.title)).size !==
      data.sections.length ||
    !data.sections.some((item) => item.title === data.citation.section) ||
    Array.from(encoded).length > 18_000
  )
    throw new Error("Reviewed source is unavailable")
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(encoded),
  )
  if (
    Array.from(new Uint8Array(digest), (byte) =>
      byte.toString(16).padStart(2, "0"),
    ).join("") !== data.content_sha256
  )
    throw new Error("Reviewed source is unavailable")
  return data
}
