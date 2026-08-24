import { describe, expect, it, vi } from "vitest"

import { initializePostHogForLocation, isPublicIntakeLocation } from "./posthog"

describe("PostHog privacy boundaries", () => {
  it("recognizes the dedicated intake host and intake routes exactly", () => {
    expect(
      isPublicIntakeLocation(
        { hostname: "INTAKE.EXAMPLE.TEST.", pathname: "/support" },
        "intake.example.test",
      ),
    ).toBe(true)
    expect(
      isPublicIntakeLocation(
        { hostname: "app.example.test", pathname: "/chat/intake/token" },
        "intake.example.test",
      ),
    ).toBe(true)
    expect(
      isPublicIntakeLocation(
        { hostname: "app.example.test", pathname: "/intake/token" },
        "intake.example.test",
      ),
    ).toBe(false)
  })

  it("does not initialize PostHog on the public intake surface", () => {
    const client = { init: vi.fn() }

    const result = initializePostHogForLocation(
      {
        hostname: import.meta.env.VITE_INTAKE_CHAT_DOMAIN,
        pathname: "/chat/intake/intake_private",
      },
      client as never,
    )

    expect(result).toBeUndefined()
    expect(client.init).not.toHaveBeenCalled()
  })

  it("keeps normal app telemetry explicit and disables automatic capture", () => {
    const initialized = { capture: vi.fn() }
    const client = { init: vi.fn(() => initialized) }

    const result = initializePostHogForLocation(
      { hostname: "app.example.test", pathname: "/notes" },
      client as never,
    )

    expect(result).toBe(initialized)
    expect(client.init).toHaveBeenCalledWith(
      import.meta.env.VITE_POSTHOG_KEY,
      expect.objectContaining({
        autocapture: false,
        capture_pageview: false,
        capture_pageleave: false,
        disable_session_recording: true,
      }),
    )
  })
})
