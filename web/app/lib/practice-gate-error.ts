export const PRACTICE_MEMBERSHIP_REQUIRED_DETAIL =
  "Name or join a practice and accept the BAA before using Cosmas."

export const PRACTICE_BAA_REQUIRED_DETAIL =
  "The Cosmaneura Inc BAA must be in force before Cosmas will process PHI."

export type PracticeAgreementKind = "join_practice" | "accept_agreement"

export type PracticeAgreementBlock = {
  kind: PracticeAgreementKind
  detail: string
  headline: string
  instruction: string
}

const JOIN_PRACTICE_BLOCK: PracticeAgreementBlock = {
  kind: "join_practice",
  detail: PRACTICE_MEMBERSHIP_REQUIRED_DETAIL,
  headline: "Join the practice",
  instruction: "Join the practice before notes will appear.",
}

const ACCEPT_AGREEMENT_BLOCK: PracticeAgreementBlock = {
  kind: "accept_agreement",
  detail: PRACTICE_BAA_REQUIRED_DETAIL,
  headline: "Accept the updated agreement",
  instruction: "Accept the updated agreement before notes will appear.",
}

/** Retain the HTTP status and safe recovery detail through the query cache. */
export class PracticeAgreementError extends Error {
  readonly status = 403

  constructor(readonly data: PracticeAgreementBlock) {
    super(data.detail)
    this.name = "PracticeAgreementError"
  }
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object"
    ? (value as Record<string, unknown>)
    : null
}

function blockFromBody(
  value: unknown,
  depth = 0,
): PracticeAgreementBlock | null {
  if (depth > 4) return null
  if (value === PRACTICE_MEMBERSHIP_REQUIRED_DETAIL) return JOIN_PRACTICE_BLOCK
  if (value === PRACTICE_BAA_REQUIRED_DETAIL) return ACCEPT_AGREEMENT_BLOCK
  const record = asRecord(value)
  if (!record) return null
  for (const key of ["detail", "error", "data"]) {
    const block = blockFromBody(record[key], depth + 1)
    if (block) return block
  }
  return null
}

/** Recognize only HTTP 403s with an exact detail emitted by practice_gate.py. */
export function practiceAgreementBlockFromUnknown(
  error: unknown,
): PracticeAgreementBlock | null {
  const record = asRecord(error)
  if (!record) return null
  const status =
    record.status ??
    asRecord(record.response)?.status ??
    asRecord(record.init)?.status
  return status === 403 ? blockFromBody(record) : null
}

export function practiceAgreementBlockFromClientResult(result: {
  data?: unknown
  error?: unknown
  response?: { status?: number }
}): PracticeAgreementBlock | null {
  if (result.data != null || result.response?.status !== 403) return null
  return blockFromBody(result.error)
}

export function practiceAgreementBlockFromRouteFailure(
  error: unknown,
): PracticeAgreementBlock | null {
  return practiceAgreementBlockFromUnknown(error)
}
