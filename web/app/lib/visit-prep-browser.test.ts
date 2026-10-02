import { afterEach, describe, expect, it, vi } from "vitest"

import {
  canResumeVisitPrepConfirmation,
  captureVisitPrepCredential,
  forgetVisitPrepCredential,
  isCanonicalVisitPrepToken,
  isVisitPrepPatientPath,
  readVisitPrepCredential,
} from "./visit-prep-browser"

const token = `vp1_${"A".repeat(43)}`
afterEach(() => forgetVisitPrepCredential())

function capture(path: string, hash: string, search = "") {
  const replaceState = vi.fn()
  captureVisitPrepCredential({
    location: { pathname: path, hash, search },
    history: { state: { preserved: true }, replaceState },
  } as unknown as Parameters<typeof captureVisitPrepCredential>[0])
  return replaceState
}

describe("patient invitation memory boundary", () => {
  it("strips the fragment before retaining exactly one canonical credential", () => {
    const storage = vi.spyOn(Storage.prototype, "setItem")
    const replace = capture("/visit-prep", `#access_token=${token}`)
    expect(replace).toHaveBeenCalledWith({ preserved: true }, "", "/visit-prep")
    expect(readVisitPrepCredential()).toBe(token)
    expect(canResumeVisitPrepConfirmation()).toBe(true)
    expect(storage).not.toHaveBeenCalled()
    storage.mockRestore()
    forgetVisitPrepCredential()
    expect(readVisitPrepCredential()).toBeNull()
  })

  it.each([
    [`#access_token=${token}&access_token=${token}`, ""],
    [`#access_token=${token}&tracking=1`, ""],
    [`#access_token=${token}`, `?access_token=${token}`],
    [`#access_token=vp1_${"A".repeat(42)}B`, ""],
    [`#access_token=%76p1_${"A".repeat(43)}`, ""],
    ["", ""],
  ])(
    "rejects malformed/ambiguous credential input without retaining it",
    (hash, search) => {
      expect(capture("/visit-prep", hash, search)).toHaveBeenCalled()
      expect(readVisitPrepCredential()).toBeNull()
    },
  )

  it("rejects alternate path forms, while recognizing only the patient boundary", () => {
    capture("/visit-prep/", `#access_token=${token}`)
    expect(readVisitPrepCredential()).toBeNull()
    expect(isVisitPrepPatientPath("/visit-prep/unsupported")).toBe(true)
    expect(isVisitPrepPatientPath("/visit-preps")).toBe(false)
    expect(isCanonicalVisitPrepToken(token)).toBe(true)
  })

  it("allows a clean reload but not a malformed or ambiguous invitation to use a remembered cookie", () => {
    capture("/visit-prep", "")
    expect(canResumeVisitPrepConfirmation()).toBe(true)
    for (const [hash, search] of [
      ["#malformed", ""],
      ["", "?tracking=1"],
      [`#access_token=${token}&tracking=1`, ""],
    ]) {
      capture("/visit-prep", hash, search)
      expect(canResumeVisitPrepConfirmation()).toBe(false)
    }
  })
})
