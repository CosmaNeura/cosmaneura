import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import {
  uploadFileToAzure,
  uploadPdfToExactAzureUrl,
  uploadToExactAzureUrl,
} from "./azure-upload"

const getUserId = vi.fn()

vi.mock("~/configuration/clerk", () => ({
  getUserId: (...args: unknown[]) => getUserId(...args),
}))

describe("Azure uploads", () => {
  beforeEach(() => {
    getUserId.mockReset().mockResolvedValue("user_audio")
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true } as Response))
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it("uploads an example-note PDF to the exact server-issued URL", async () => {
    const uploadUrl =
      "https://uploads.example/container/server-generated.pdf?sv=1&sig=secret"
    const file = new File(["%PDF-1.7"], "private-patient-name.pdf", {
      type: "application/pdf",
    })

    const result = await uploadPdfToExactAzureUrl(file, uploadUrl)

    expect(fetch).toHaveBeenCalledWith(uploadUrl, {
      method: "PUT",
      headers: {
        "x-ms-blob-type": "BlockBlob",
        "Content-Type": "application/pdf",
        "If-None-Match": "*",
      },
      body: file,
    })
    expect(getUserId).not.toHaveBeenCalled()
    expect(result.error).toBe(false)
  })

  it("uses an opaque legacy filename without exposing the original name", async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date("2026-08-24T12:00:00Z"))
    vi.spyOn(crypto, "randomUUID").mockReturnValue(
      "11111111-1111-4111-8111-111111111111",
    )
    const file = new File(["audio"], "private-patient-name.WAV", {
      type: "audio/wav",
    })

    const result = await uploadFileToAzure(
      file,
      "https://uploads.example/audio?sv=1&sig=secret",
    )

    expect(result.stampedFileName).toBe(
      "user_audio_1787572800000_11111111-1111-4111-8111-111111111111.wav",
    )
    expect(result.stampedFileName).not.toContain("private-patient-name")
    expect(fetch).toHaveBeenCalledWith(
      "https://uploads.example/audio/user_audio_1787572800000_11111111-1111-4111-8111-111111111111.wav?sv=1&sig=secret",
      {
        method: "PUT",
        headers: {
          "x-ms-blob-type": "BlockBlob",
          "Content-Type": "audio/wav",
        },
        body: file,
      },
    )
  })

  it("reports a rejected exact-URL upload", async () => {
    vi.mocked(fetch).mockResolvedValue({ ok: false } as Response)

    const result = await uploadPdfToExactAzureUrl(
      new File(["%PDF-1.7"], "note.pdf", { type: "application/pdf" }),
      "https://uploads.example/opaque.pdf?sig=secret",
    )

    expect(result.error).toBe(true)
  })

  it("uploads clinical media to the exact server-issued URL without overwrite", async () => {
    const uploadUrl =
      "https://uploads.example/container/visit_opaque.wav?sv=1&sig=secret"
    const file = new File(["audio"], "patient-name.wav", {
      type: "audio/wav",
    })

    await uploadToExactAzureUrl(file, uploadUrl, "audio/wav")

    expect(fetch).toHaveBeenCalledWith(uploadUrl, {
      method: "PUT",
      headers: {
        "x-ms-blob-type": "BlockBlob",
        "Content-Type": "audio/wav",
        "If-None-Match": "*",
      },
      body: file,
    })
    expect(getUserId).not.toHaveBeenCalled()
  })
})
