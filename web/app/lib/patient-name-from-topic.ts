/**
 * Best-effort extraction of a patient name from a free-form meeting topic
 * (e.g. a Zoom meeting title) so the new-intake form can be pre-filled.
 *
 * Deliberately conservative: in a medical context a wrong auto-fill is worse
 * than an empty field, so this returns null unless the topic yields exactly
 * one unambiguous person-shaped name. Callers must present the result as a
 * guess for the provider to verify.
 */

const TITLE_WORDS = new Set(["dr", "doctor", "md", "do", "np", "pa", "rn"])

// words that commonly appear capitalized in meeting titles but are not names
const NON_NAME_WORDS = new Set([
  "appointment",
  "appt",
  "visit",
  "meeting",
  "session",
  "checkup",
  "check-up",
  "consult",
  "consultation",
  "follow",
  "followup",
  "follow-up",
  "up",
  "intake",
  "new",
  "patient",
  "telehealth",
  "virtual",
  "zoom",
  "review",
  "annual",
  "physical",
  "initial",
  "urgent",
  "care",
  "clinic",
  "call",
  "with",
  "for",
  "and",
])

// a capitalized word made of letters/apostrophes/hyphens, optional trailing
// period (middle initials like "J.")
const NAME_WORD = /^\p{Lu}[\p{L}'’-]*\.?$/u

function looksLikePersonName(candidate: string): boolean {
  const words = candidate.trim().split(/\s+/)

  if (words.length < 2 || words.length > 4) return false

  return words.every((word) => {
    if (!NAME_WORD.test(word)) return false

    const clean = word.replace(/\.$/, "").toLowerCase()
    return !TITLE_WORDS.has(clean) && !NON_NAME_WORDS.has(clean)
  })
}

function isProvider(text: string): boolean {
  return /^(dr|doctor)\b/i.test(text) || /\b(md|do|np|pa|rn)\.?$/i.test(text)
}

function extractCandidate(segment: string): string | null {
  const text = segment.trim()
  if (!text) return null

  // "Appointment with Jane Doe", "Follow-up for Jane Doe"
  const withFor = text.match(/\b(?:with|for)[:\s]+(.+)$/i)
  if (withFor && looksLikePersonName(withFor[1].trim())) {
    return withFor[1].trim()
  }

  // "Dr. Smith and Jane Doe", "Jane Doe & Dr. Smith": take the non-provider side
  const pair = text.split(/\s+(?:and|&|\+|\/)\s+/i)
  if (pair.length === 2) {
    const [left, right] = pair.map((part) => part.trim())
    const leftIsProvider = isProvider(left)
    const rightIsProvider = isProvider(right)

    if (leftIsProvider && !rightIsProvider && looksLikePersonName(right)) {
      return right
    }
    if (rightIsProvider && !leftIsProvider && looksLikePersonName(left)) {
      return left
    }
  }

  // the whole segment is just a name, e.g. topic "Jane Doe"
  if (looksLikePersonName(text)) return text

  return null
}

export function guessPatientNameFromTopic(
  topic: string | null | undefined,
): string | null {
  const trimmed = (topic ?? "").trim()
  if (!trimmed) return null

  // evaluate the whole topic plus each separator-delimited segment, e.g.
  // "Telehealth: Jane Doe" or "Appointment with Jane Doe - Telehealth".
  // dashes only separate when surrounded by whitespace, so hyphenated
  // surnames ("Jane Doe-Smith") stay intact
  const segments = [trimmed, ...trimmed.split(/\s+[-–—]\s+|\s*[:|,]\s*/)]

  const found = new Map<string, string>()
  for (const segment of segments) {
    const candidate = extractCandidate(segment)
    if (candidate) {
      found.set(candidate.toLowerCase(), candidate)
    }
  }

  // more than one distinct name is ambiguous -- guess nothing
  if (found.size === 1) {
    return [...found.values()][0]
  }

  return null
}
