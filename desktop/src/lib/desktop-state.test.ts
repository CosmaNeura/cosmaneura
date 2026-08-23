import {
  canStartCapture,
  canStopCapture,
  desktopSnapshotSchema,
  idleDesktopSnapshot,
} from "./desktop-state"

describe("desktop workflow safety", () => {
  it("requires server consent, a selected appointment, and a provider", () => {
    expect(canStartCapture(idleDesktopSnapshot, true)).toBe(false)
    expect(
      canStartCapture(
        {
          ...idleDesktopSnapshot,
          phase: "authorized",
          appointmentSelection: "confirmed",
          consentStatus: "granted",
          providerEnabled: true,
        },
        false,
      ),
    ).toBe(false)
    expect(
      canStartCapture(
        {
          ...idleDesktopSnapshot,
          phase: "authorized",
          appointmentSelection: "confirmed",
          consentStatus: "granted",
          providerEnabled: true,
        },
        true,
      ),
    ).toBe(true)
  })

  it("keeps local stop available without backend reachability", () => {
    expect(
      canStopCapture({
        ...idleDesktopSnapshot,
        phase: "degraded",
        localCaptureActive: true,
        backendReachable: false,
      }),
    ).toBe(true)
  })

  it("rejects unrecognized fields at the IPC boundary", () => {
    expect(() =>
      desktopSnapshotSchema.parse({
        ...idleDesktopSnapshot,
        accessToken: "must-never-cross-ipc",
      }),
    ).toThrow()
  })
})
