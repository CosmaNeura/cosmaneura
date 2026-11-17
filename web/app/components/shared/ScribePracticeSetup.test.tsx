import { describe, expect, it, vi } from "vitest"

import type { PracticeStatus } from "~/lib/practice-api"

import {
  ScribePracticeSetup,
  isScribeSetupRequest,
} from "./ScribePracticeSetup"
import { renderToStaticMarkup } from "react-dom/server"

const member = {
  role: "owner",
  baa_accepted: true,
  attested: true,
  access_authorized: true,
  authorization_basis: "workforce_attestation",
  access_revoked: false,
  access_ended: false,
} as PracticeStatus

function markup(practice: PracticeStatus | null, viewAs = false) {
  const legal = vi.fn(() => <span>Existing agreement form</span>)
  const enroll = vi.fn(() => <span>Existing enrollment form</span>)
  const output = renderToStaticMarkup(
    <ScribePracticeSetup
      practice={practice}
      viewAs={viewAs}
      enrollment={<Enroll />}
      agreement={<Legal />}
    />,
  )
  function Legal() {
    return legal()
  }
  function Enroll() {
    return enroll()
  }
  return { output, legal, enroll }
}

describe("Scribe setup guidance preserves legal and enrollment boundaries", () => {
  it("offers ordinary enrollment only when no membership exists", () => {
    const result = markup(null)
    expect(result.enroll).toHaveBeenCalledOnce()
    expect(result.legal).not.toHaveBeenCalled()
  })
  it("reuses the current agreement only for an eligible owner with a missing BAA", () => {
    const result = markup({ ...member, baa_accepted: false, attested: false })
    expect(result.legal).toHaveBeenCalledOnce()
    expect(result.enroll).not.toHaveBeenCalled()
  })
  it.each([{ access_revoked: true }, { access_ended: true }])(
    "does not offer forms to revoked or ended owners",
    (changes) => {
      const result = markup({ ...member, baa_accepted: false, ...changes })
      expect(result.legal).not.toHaveBeenCalled()
      expect(result.enroll).not.toHaveBeenCalled()
      expect(result.output).toContain("cannot restore access")
    },
  )
  it.each([
    { role: "workforce", baa_accepted: false },
    { authorization_basis: "organization_roster", baa_accepted: false },
    { attested: false },
    { access_authorized: false },
  ])(
    "requires owner review rather than reenrollment for an existing incomplete membership",
    (changes) => {
      const result = markup({ ...member, ...changes })
      expect(result.legal).not.toHaveBeenCalled()
      expect(result.enroll).not.toHaveBeenCalled()
      expect(result.output).toContain(
        "Joining again does not change an existing membership",
      )
    },
  )
  it.each([null, { ...member, baa_accepted: false }])(
    "blocks all forms while viewing another account",
    (practice) => {
      const result = markup(practice, true)
      expect(result.legal).not.toHaveBeenCalled()
      expect(result.enroll).not.toHaveBeenCalled()
      expect(result.output).toContain("End view-as")
    },
  )
  it("instructs a current member to recheck native authority without claiming activation", () => {
    const result = markup(member)
    expect(result.legal).not.toHaveBeenCalled()
    expect(result.enroll).not.toHaveBeenCalled()
    expect(result.output).toContain("does not create a Scribe connection")
    expect(result.output).toContain("Continue in Core")
    expect(result.output.toLowerCase()).not.toContain(
      String.fromCharCode(122, 111, 111, 109),
    )
  })
  it("uses only fixed relative enrollment, support and Core links", () => {
    const output = renderToStaticMarkup(<ScribePracticeSetup practice={null} />)
    expect(output).toContain('href="/onboarding?scribe=setup"')
    expect(output).toContain('href="/notes"')
    expect(output).not.toContain("https:")
  })
})

describe("explicit Core-only setup mode", () => {
  it("accepts only the explicit onboarding query once", () => {
    expect(
      isScribeSetupRequest("https://synthetic.invalid/onboarding?scribe=setup"),
    ).toBe(true)
    for (const path of [
      "/onboarding",
      "/onboarding?scribe=other",
      "/onboarding?scribe=setup&scribe=setup",
      "/notes?scribe=setup",
      "/onboarding?return_to=scribe",
      "/onboarding?scribe=setup&scribe=other",
    ]) {
      expect(isScribeSetupRequest("https://synthetic.invalid" + path)).toBe(
        false,
      )
    }
  })
})
