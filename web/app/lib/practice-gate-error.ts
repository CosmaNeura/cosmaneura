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

// Measured live 403 after Google (Maura, 2026-08-31): practice/settings 200,
// notes 403. practice.baa_in_force requires CN-BAA-CUST-CW v2.3. An old v2.2
// acceptance is not empty-practice; empty-practice uses JOIN_PRACTICE_BLOCK.
const ACCEPT_AGREEMENT_BLOCK: PracticeAgreementBlock = {
  kind: "accept_agreement",
  detail: PRACTICE_BAA_REQUIRED_DETAIL,
  headline: "Accept the updated agreement",
  instruction: "Accept the updated agreement before notes will appear.",
}

function collectStrings(value: unknown, into: string[], depth = 0): void {
  if (depth > 4 || value == null) return
  if (typeof value === "string") {
    into.push(value)
    return
  }
  if (value instanceof Error) {
    into.push(value.message)
    return
  }
  if (typeof value !== "object") return

  const record = value as Record<string, unknown>
  for (const key of ["detail", "statusText", "message", "error", "data"]) {
    if (key in record) collectStrings(record[key], into, depth + 1)
  }
}

function blockFromStrings(strings: string[]): PracticeAgreementBlock | null {
  const blob = strings.join(" ")
  if (blob.includes("Name or join a practice")) return JOIN_PRACTICE_BLOCK
  if (blob.includes("BAA must be in force")) return ACCEPT_AGREEMENT_BLOCK
  return null
}

/** Recognize the two practice-gate 403s the notes API already returns. */
export function practiceAgreementBlockFromUnknown(
  error: unknown,
): PracticeAgreementBlock | null {
  const strings: string[] = []
  collectStrings(error, strings)
  return blockFromStrings(strings)
}

export function practiceAgreementBlockFromClientResult(result: {
  data?: unknown
  error?: unknown
  response?: { status?: number }
}): PracticeAgreementBlock | null {
  if (result.data) return null
  return practiceAgreementBlockFromUnknown({
    error: result.error,
    response: result.response,
    status: result.response?.status,
  })
}

function isRouteErrorLike(
  error: unknown,
): error is { status: number; statusText?: string; data: unknown } {
  return Boolean(
    error && typeof error === "object" && "status" in error && "data" in error,
  )
}

export function practiceAgreementBlockFromRouteFailure(
  error: unknown,
): PracticeAgreementBlock | null {
  if (isRouteErrorLike(error)) {
    return (
      practiceAgreementBlockFromUnknown(error.data) ??
      practiceAgreementBlockFromUnknown({
        status: error.status,
        detail: error.data ?? error.statusText,
      })
    )
  }
  return practiceAgreementBlockFromUnknown(error)
}
