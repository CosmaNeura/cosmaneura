import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import {
  PublicInvitationMutationJournalError,
  discardCorruptPublicInvitationMutation,
  discardPublicInvitationMutation,
  loadPublicInvitationMutation,
  publicInvitationMutationStorageKey,
  reservePublicInvitationMutation,
} from "./public-invitation-mutation"

const SCOPE = "practice:practice_test"
const EMAIL = "clinician@example.test"
const IDEMPOTENCY_KEY = "11111111-1111-4111-8111-111111111111"

function installSerializedCrossTabLocks() {
  let tail = Promise.resolve<unknown>(undefined)
  const request = vi.fn((...args: unknown[]) => {
    const callback = args.at(-1) as (lock: Lock) => unknown | Promise<unknown>
    const result = tail.then(() =>
      callback({ name: String(args[0]), mode: "exclusive" } as Lock),
    )
    tail = result.then(
      () => undefined,
      () => undefined,
    )
    return result
  })
  Object.defineProperty(navigator, "locks", {
    configurable: true,
    value: { request } as unknown as LockManager,
  })
  return request
}

describe("durable public invitation mutations", () => {
  beforeEach(() => {
    window.localStorage.clear()
    installSerializedCrossTabLocks()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it("reuses the persisted key after response loss and reload", async () => {
    const randomUUID = vi
      .spyOn(crypto, "randomUUID")
      .mockReturnValue(IDEMPOTENCY_KEY)
    const firstAttempt = await reservePublicInvitationMutation(SCOPE, EMAIL, 7)

    const send = vi.fn(async (idempotencyKey: string) => {
      expect(loadPublicInvitationMutation(SCOPE)?.idempotencyKey).toBe(
        idempotencyKey,
      )
      throw new TypeError("response connection lost")
    })
    await expect(send(firstAttempt.idempotencyKey)).rejects.toThrow(
      "response connection lost",
    )

    // A new component instance after reload reads and reserves the same
    // logical mutation. Neither response loss nor response receipt clears it.
    const recovered = loadPublicInvitationMutation(SCOPE)
    const retry = await reservePublicInvitationMutation(SCOPE, EMAIL, 7)
    expect(recovered).toEqual(firstAttempt)
    expect(retry).toEqual(firstAttempt)
    expect(randomUUID).toHaveBeenCalledOnce()
    expect(loadPublicInvitationMutation(SCOPE)).toEqual(firstAttempt)

    await discardPublicInvitationMutation(SCOPE, firstAttempt.idempotencyKey)
    expect(loadPublicInvitationMutation(SCOPE)).toBeNull()
  })

  it("serializes concurrent tabs and gives both dispatches one key", async () => {
    const randomUUID = vi
      .spyOn(crypto, "randomUUID")
      .mockReturnValue(IDEMPOTENCY_KEY)

    const [tabOne, tabTwo] = await Promise.all([
      reservePublicInvitationMutation(SCOPE, EMAIL, 7),
      reservePublicInvitationMutation(SCOPE, EMAIL.toUpperCase(), 7),
    ])

    expect(tabOne.idempotencyKey).toBe(IDEMPOTENCY_KEY)
    expect(tabTwo.idempotencyKey).toBe(IDEMPOTENCY_KEY)
    expect(tabTwo).toEqual(tabOne)
    expect(randomUUID).toHaveBeenCalledOnce()
  })

  it("does not let a second tab replace a different pending mutation", async () => {
    vi.spyOn(crypto, "randomUUID").mockReturnValue(IDEMPOTENCY_KEY)
    const first = await reservePublicInvitationMutation(SCOPE, EMAIL, 7)

    await expect(
      reservePublicInvitationMutation(SCOPE, "other@example.test", 14),
    ).rejects.toBeInstanceOf(PublicInvitationMutationJournalError)
    expect(loadPublicInvitationMutation(SCOPE)).toEqual(first)
  })

  it("does not let a stale tab discard a newer saved mutation", async () => {
    const newerKey = "22222222-2222-4222-8222-222222222222"
    vi.spyOn(crypto, "randomUUID")
      .mockReturnValueOnce(IDEMPOTENCY_KEY)
      .mockReturnValueOnce(newerKey)
    const stale = await reservePublicInvitationMutation(SCOPE, EMAIL, 7)
    await discardPublicInvitationMutation(SCOPE, stale.idempotencyKey)
    const newer = await reservePublicInvitationMutation(
      SCOPE,
      "newer@example.test",
      14,
    )

    await expect(
      discardPublicInvitationMutation(SCOPE, stale.idempotencyKey),
    ).resolves.toBe(false)
    expect(newer.idempotencyKey).toBe(newerKey)
    expect(loadPublicInvitationMutation(SCOPE)).toEqual(newer)
  })

  it("fails before persistence when cross-tab locking is unavailable", async () => {
    Object.defineProperty(navigator, "locks", {
      configurable: true,
      value: undefined,
    })
    const randomUUID = vi.spyOn(crypto, "randomUUID")

    await expect(
      reservePublicInvitationMutation(SCOPE, EMAIL, 7),
    ).rejects.toThrow(/cannot safely coordinate/i)
    expect(randomUUID).not.toHaveBeenCalled()
    expect(window.localStorage.length).toBe(0)
  })

  it("does not let a stale corrupt-record confirmation delete a newer request", async () => {
    const key = publicInvitationMutationStorageKey(SCOPE)
    window.localStorage.setItem(key, "{corrupt")
    let invalidSnapshot: string | null = null
    try {
      loadPublicInvitationMutation(SCOPE)
    } catch (error) {
      expect(error).toBeInstanceOf(PublicInvitationMutationJournalError)
      invalidSnapshot = (error as PublicInvitationMutationJournalError)
        .invalidSnapshot
    }
    expect(invalidSnapshot).toBe("{corrupt")

    expect(
      await discardCorruptPublicInvitationMutation(SCOPE, invalidSnapshot!),
    ).toBe(true)
    vi.spyOn(crypto, "randomUUID").mockReturnValue(IDEMPOTENCY_KEY)
    const newer = await reservePublicInvitationMutation(SCOPE, EMAIL, 7)

    expect(
      await discardCorruptPublicInvitationMutation(SCOPE, invalidSnapshot!),
    ).toBe(false)
    expect(loadPublicInvitationMutation(SCOPE)).toEqual(newer)
  })
})
