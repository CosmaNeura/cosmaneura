import { describe, expect, it } from "vitest"

import {
  buildDesktopAuthorizationCallback,
  buildDesktopLaunchUrl,
  parseDesktopCaptureHandoff,
  parseParticipantRegions,
} from "./desktop-capture-handoff"

const secret = (value: string) => value.repeat(43)

function validFragment() {
  const params = new URLSearchParams({
    launch_ticket: secret("a"),
    code_challenge: secret("b"),
    code_challenge_method: "S256",
    redirect_uri: "cosmaneura-capture://oauth/callback",
    installation_thumbprint: secret("c"),
    state: secret("d"),
  })
  return params.toString()
}

describe("desktop capture browser handoff", () => {
  it("accepts only the exact value-safe fragment shape", () => {
    expect(parseDesktopCaptureHandoff(validFragment())).toEqual({
      launchTicket: secret("a"),
      codeChallenge: secret("b"),
      redirectUri: "cosmaneura-capture://oauth/callback",
      installationThumbprint: secret("c"),
      state: secret("d"),
    })

    expect(
      parseDesktopCaptureHandoff(`${validFragment()}&tenant_id=attacker`),
    ).toBeNull()
    expect(
      parseDesktopCaptureHandoff(`${validFragment()}&state=${secret("e")}`),
    ).toBeNull()
    expect(parseDesktopCaptureHandoff("launch_ticket=secret")).toBeNull()
  })

  it("builds callbacks only for the fixed desktop redirect", () => {
    expect(
      buildDesktopAuthorizationCallback(
        secret("e"),
        "cosmaneura-capture://oauth/callback",
        secret("f"),
      ),
    ).toBe(
      `cosmaneura-capture://oauth/callback?code=${secret("e")}&state=${secret("f")}`,
    )
    expect(
      buildDesktopAuthorizationCallback(
        secret("e"),
        "https://attacker.invalid/callback",
        secret("f"),
      ),
    ).toBeNull()
  })

  it("normalizes US regions and rejects ambiguous input", () => {
    expect(parseParticipantRegions("ny, US-NJ, ny")).toEqual(["US-NY", "US-NJ"])
    expect(parseParticipantRegions("New York")).toBeNull()
    expect(parseParticipantRegions("")).toBeNull()
  })

  it("builds a value-safe launch deep link", () => {
    expect(buildDesktopLaunchUrl(secret("g"))).toBe(
      `cosmaneura-capture://launch?launch_ticket=${secret("g")}`,
    )
    expect(buildDesktopLaunchUrl("../unsafe")).toBeNull()
  })
})
