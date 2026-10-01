import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const sentry = vi.hoisted(() => ({
  init: vi.fn(),
  browserTracingIntegration: vi.fn(
    (_options: {
      shouldCreateSpanForRequest: (url: string) => boolean
    }) => ({}),
  ),
  feedbackIntegration: vi.fn(() => ({})),
  withErrorBoundary: vi.fn((component) => component),
}))
vi.mock("@sentry/react", () => sentry)
vi.mock("~/components/ViewAsRouteGuard", () => ({
  ViewAsProtectedErrorGuard: () => null,
}))
vi.mock("~/utils/environment", () => ({
  isProduction: () => true,
  isDevelopment: () => false,
  isTesting: () => false,
  environmentName: () => "production",
  requireEnv: () => "synthetic",
}))

type Options = {
  beforeBreadcrumb: (value: unknown) => unknown
  beforeSend: (value: unknown) => unknown
  beforeSendTransaction: (value: unknown) => unknown
}

describe("patient telemetry boundary", () => {
  beforeEach(() => {
    vi.resetModules()
    vi.clearAllMocks()
  })
  afterEach(() => window.history.replaceState({}, "", "/"))

  it("does not initialize Sentry for the public patient page", async () => {
    window.history.replaceState({}, "", "/visit-prep")
    await import("./sentry")
    expect(sentry.init).not.toHaveBeenCalled()
  })

  it("drops patient events after an account-to-patient navigation", async () => {
    window.history.replaceState({}, "", "/notes")
    await import("./sentry")
    expect(sentry.init).toHaveBeenCalledOnce()
    const options = sentry.init.mock.calls[0][0] as Options
    const tracing = sentry.browserTracingIntegration.mock.calls[0][0] as {
      shouldCreateSpanForRequest: (url: string) => boolean
    }
    const event = { message: "Synthetic patient story" }
    expect(options.beforeSend(event)).toBe(event)
    expect(
      tracing.shouldCreateSpanForRequest("https://app.example.test/settings"),
    ).toBe(true)
    window.history.replaceState({}, "", "/visit-prep")
    expect(options.beforeBreadcrumb(event)).toBeNull()
    expect(options.beforeSend(event)).toBeNull()
    expect(options.beforeSendTransaction(event)).toBeNull()
    expect(
      tracing.shouldCreateSpanForRequest("https://app.example.test/settings"),
    ).toBe(false)
  })
})
