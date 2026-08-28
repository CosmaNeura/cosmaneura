const PUBLIC_INVITATION_MUTATION_VERSION = 1 as const
const PUBLIC_INVITATION_MUTATION_PREFIX =
  "cosmas.public-practice-invitation-mutation.v1"

export type PendingPublicInvitationMutation = {
  version: typeof PUBLIC_INVITATION_MUTATION_VERSION
  scope: string
  email: string
  expiresInDays: number
  idempotencyKey: string
}

export class PublicInvitationMutationJournalError extends Error {
  readonly invalidSnapshot: string | null

  constructor(message: string, invalidSnapshot: string | null = null) {
    super(message)
    this.name = "PublicInvitationMutationJournalError"
    this.invalidSnapshot = invalidSnapshot
  }
}

function storageKey(scope: string) {
  return `${PUBLIC_INVITATION_MUTATION_PREFIX}:${encodeURIComponent(scope)}`
}

function lockName(scope: string) {
  return `${PUBLIC_INVITATION_MUTATION_PREFIX}:lock:${scope}`
}

function normalizedScope(scope: string) {
  const normalized = scope.trim()
  if (!normalized) {
    throw new PublicInvitationMutationJournalError(
      "The practice identity is unavailable, so invitation creation remains locked.",
    )
  }
  return normalized
}

function parseMutation(
  raw: string | null,
  expectedScope: string,
): PendingPublicInvitationMutation | null {
  if (raw === null) return null
  try {
    const value = JSON.parse(raw) as Partial<PendingPublicInvitationMutation>
    if (
      value.version !== PUBLIC_INVITATION_MUTATION_VERSION ||
      value.scope !== expectedScope ||
      typeof value.email !== "string" ||
      value.email !== value.email.trim().toLowerCase() ||
      !value.email.includes("@") ||
      typeof value.expiresInDays !== "number" ||
      !Number.isInteger(value.expiresInDays) ||
      value.expiresInDays < 1 ||
      value.expiresInDays > 30 ||
      typeof value.idempotencyKey !== "string" ||
      value.idempotencyKey.length < 8 ||
      value.idempotencyKey.length > 255
    ) {
      throw new Error("invalid journal")
    }
    return value as PendingPublicInvitationMutation
  } catch {
    throw new PublicInvitationMutationJournalError(
      "The saved invitation request could not be verified. Discard it before creating another invitation.",
      raw,
    )
  }
}

function localStorageForInvitationMutation() {
  if (typeof window === "undefined") {
    throw new PublicInvitationMutationJournalError(
      "Invitation creation is available only in the browser.",
    )
  }
  try {
    return window.localStorage
  } catch {
    throw new PublicInvitationMutationJournalError(
      "Secure browser storage is unavailable, so invitation creation remains locked.",
    )
  }
}

async function withCrossTabLock<T>(
  scope: string,
  action: () => T | Promise<T>,
) {
  if (typeof navigator === "undefined" || !navigator.locks) {
    throw new PublicInvitationMutationJournalError(
      "This browser cannot safely coordinate invitation creation across tabs. Update the browser or close other tabs and contact support.",
    )
  }
  return navigator.locks.request(lockName(scope), { mode: "exclusive" }, action)
}

export function publicInvitationMutationScope(input: {
  practiceId?: string | null
  practiceName: string
  ownerEmail?: string | null
}) {
  const practiceId = input.practiceId?.trim()
  if (practiceId) return `practice:${practiceId}`
  const ownerEmail = input.ownerEmail?.trim().toLowerCase()
  const practiceName = input.practiceName.trim().toLowerCase()
  if (!ownerEmail || !practiceName) return null
  return `owner:${ownerEmail}:practice:${practiceName}`
}

export function publicInvitationMutationStorageKey(scope: string) {
  return storageKey(normalizedScope(scope))
}

export function loadPublicInvitationMutation(scope: string) {
  const normalized = normalizedScope(scope)
  const storage = localStorageForInvitationMutation()
  return parseMutation(storage.getItem(storageKey(normalized)), normalized)
}

export async function reservePublicInvitationMutation(
  scope: string,
  email: string,
  expiresInDays: number,
) {
  const normalized = normalizedScope(scope)
  const normalizedEmail = email.trim().toLowerCase()
  if (
    !normalizedEmail.includes("@") ||
    !Number.isInteger(expiresInDays) ||
    expiresInDays < 1 ||
    expiresInDays > 30
  ) {
    throw new PublicInvitationMutationJournalError(
      "Enter a valid invitation email and expiration before continuing.",
    )
  }
  return withCrossTabLock(normalized, () => {
    const storage = localStorageForInvitationMutation()
    const key = storageKey(normalized)
    const existing = parseMutation(storage.getItem(key), normalized)
    if (existing) {
      if (
        existing.email !== normalizedEmail ||
        existing.expiresInDays !== expiresInDays
      ) {
        throw new PublicInvitationMutationJournalError(
          `Finish or discard the saved invitation for ${existing.email} before creating another one.`,
        )
      }
      return existing
    }

    const mutation: PendingPublicInvitationMutation = {
      version: PUBLIC_INVITATION_MUTATION_VERSION,
      scope: normalized,
      email: normalizedEmail,
      expiresInDays,
      idempotencyKey: crypto.randomUUID(),
    }
    storage.setItem(key, JSON.stringify(mutation))
    const persisted = parseMutation(storage.getItem(key), normalized)
    if (
      !persisted ||
      persisted.idempotencyKey !== mutation.idempotencyKey ||
      persisted.email !== mutation.email ||
      persisted.expiresInDays !== mutation.expiresInDays
    ) {
      throw new PublicInvitationMutationJournalError(
        "The invitation request could not be durably saved, so nothing was sent.",
      )
    }
    return persisted
  })
}

export async function discardPublicInvitationMutation(
  scope: string,
  expectedIdempotencyKey: string,
) {
  const normalized = normalizedScope(scope)
  return withCrossTabLock(normalized, () => {
    const storage = localStorageForInvitationMutation()
    const key = storageKey(normalized)
    const existing = parseMutation(storage.getItem(key), normalized)
    if (existing && existing.idempotencyKey !== expectedIdempotencyKey) {
      return false
    }
    storage.removeItem(key)
    return true
  })
}

export async function discardCorruptPublicInvitationMutation(
  scope: string,
  invalidSnapshot: string,
) {
  const normalized = normalizedScope(scope)
  return withCrossTabLock(normalized, () => {
    const storage = localStorageForInvitationMutation()
    const key = storageKey(normalized)
    const current = storage.getItem(key)
    if (current !== invalidSnapshot) return false
    try {
      if (parseMutation(current, normalized)) return false
    } catch (error) {
      if (!(error instanceof PublicInvitationMutationJournalError)) throw error
    }
    storage.removeItem(key)
    return true
  })
}
