import { afterEach, describe, expect, it, vi } from "vitest"

const STORAGE_KEY = "cosmas.desktop-capture.handoff.v1"

afterEach(() => {
  window.sessionStorage.clear()
  window.history.replaceState({}, "", "/")
  vi.resetModules()
})

describe("desktop capture fragment bootstrap", () => {
  it("removes handoff material from the URL before it is consumed", async () => {
    const fragment = "launch_ticket=sensitive-but-synthetic"
    window.history.replaceState({}, "", `/desktop-capture/connect#${fragment}`)

    const module = await import("./bootstrap-desktop-capture")

    expect(window.location.hash).toBe("")
    expect(window.sessionStorage.getItem(STORAGE_KEY)).toBe(fragment)
    expect(module.takeDesktopCaptureHandoff()).toBe(fragment)
    expect(window.sessionStorage.getItem(STORAGE_KEY)).toBeNull()
    expect(module.takeDesktopCaptureHandoff()).toBeNull()
  })

  it("does not capture fragments from unrelated routes", async () => {
    window.history.replaceState({}, "", "/notes#ordinary-navigation")

    const module = await import("./bootstrap-desktop-capture")

    expect(window.location.hash).toBe("#ordinary-navigation")
    expect(module.takeDesktopCaptureHandoff()).toBeNull()
  })
})
