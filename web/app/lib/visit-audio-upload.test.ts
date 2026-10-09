import { describe, expect, it, vi } from "vitest"

import {
  VISIT_UPLOAD_MAX_BYTES,
  submitVisitAudioUpload,
} from "./visit-audio-upload"

vi.mock("~/configuration/client", () => ({
  generateSignedVisitRecordingUploadUrl: vi.fn(),
  uploadVisitAudioFile: vi.fn(),
}))
vi.mock("~/configuration/clerk", () => ({
  getUserId: vi.fn(),
}))

function input(size = 123) {
  const file = new File(["audio"], "private-patient-name.wav", {
    type: "audio/wav",
  })
  Object.defineProperty(file, "size", { value: size })
  return {
    appointmentDate: "2026-08-24T12:00:00.000Z",
    dateOfBirth: "1980-01-01T00:00:00.000Z",
    file,
    patientName: "Private Patient",
  }
}

function dependencies() {
  return {
    issueGrant: vi.fn().mockResolvedValue({
      data: {
        content_type: "audio/wav",
        registration_token: "opaque-registration-token",
        upload_url: "https://uploads.invalid/visit_opaque.wav?sig=secret",
      },
    }),
    registerUpload: vi.fn().mockResolvedValue({}),
    uploadBlob: vi.fn().mockResolvedValue({ error: false }),
  }
}

describe("visit audio upload", () => {
  it("returns only the appointment ID provided by durable registration", async () => {
    const deps = dependencies()
    deps.registerUpload.mockResolvedValue({
      data: { appointment_id: "appointment_exact" },
    })
    await expect(submitVisitAudioUpload(input(), deps)).resolves.toEqual({
      ok: true,
      appointmentId: "appointment_exact",
    })
  })

  it("binds the selected byte size and completes only after Azure succeeds", async () => {
    const deps = dependencies()

    await expect(submitVisitAudioUpload(input(), deps)).resolves.toEqual({
      ok: true,
    })
    expect(deps.issueGrant).toHaveBeenCalledWith(
      "private-patient-name.wav",
      123,
    )
    expect(deps.uploadBlob).toHaveBeenCalledOnce()
    expect(deps.registerUpload).toHaveBeenCalledWith(
      expect.objectContaining({
        registration_token: "opaque-registration-token",
      }),
    )
  })

  it("rejects an empty or over-limit selection before requesting a grant", async () => {
    for (const size of [0, VISIT_UPLOAD_MAX_BYTES + 1]) {
      const deps = dependencies()
      await expect(submitVisitAudioUpload(input(size), deps)).resolves.toEqual({
        ok: false,
        stage: "selection",
      })
      expect(deps.issueGrant).not.toHaveBeenCalled()
      expect(deps.uploadBlob).not.toHaveBeenCalled()
      expect(deps.registerUpload).not.toHaveBeenCalled()
    }
  })

  it("does not upload or register when grant issuance fails", async () => {
    const deps = dependencies()
    deps.issueGrant.mockResolvedValue({ error: { detail: "unavailable" } })

    await expect(submitVisitAudioUpload(input(), deps)).resolves.toEqual({
      ok: false,
      stage: "grant",
    })
    expect(deps.uploadBlob).not.toHaveBeenCalled()
    expect(deps.registerUpload).not.toHaveBeenCalled()
  })

  it("does not register when Azure rejects or fails the upload", async () => {
    for (const failure of [
      Promise.resolve({ error: true }),
      Promise.reject(new Error("network unavailable")),
    ]) {
      const deps = dependencies()
      deps.uploadBlob.mockReturnValue(failure)

      await expect(submitVisitAudioUpload(input(), deps)).resolves.toEqual({
        ok: false,
        stage: "azure",
      })
      expect(deps.registerUpload).not.toHaveBeenCalled()
    }
  })

  it("never reports success when registration fails", async () => {
    const deps = dependencies()
    deps.registerUpload.mockResolvedValue({ error: { detail: "expired" } })

    await expect(submitVisitAudioUpload(input(), deps)).resolves.toEqual({
      ok: false,
      stage: "registration",
    })
    expect(deps.uploadBlob).toHaveBeenCalledOnce()
  })
})

describe("visit audio upload progress", () => {
  it("passes the progress handler to the file transfer only", async () => {
    const deps = dependencies()
    const onUploadProgress = vi.fn()

    await submitVisitAudioUpload({ ...input(), onUploadProgress }, deps)

    expect(deps.uploadBlob).toHaveBeenCalledWith(
      expect.any(File),
      "https://uploads.invalid/visit_opaque.wav?sig=secret",
      "audio/wav",
      onUploadProgress,
    )
  })
})
