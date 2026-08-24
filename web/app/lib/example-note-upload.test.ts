import { beforeEach, describe, expect, it, vi } from "vitest"

import {
  exampleNoteUploadErrorCopy,
  submitExampleNote,
} from "./example-note-upload"

const generateSignedExampleNoteUploadUrlV2 = vi.fn()
const uploadExampleNoteV2 = vi.fn()
const generateSignedExampleNoteUploadUrl = vi.fn()
const uploadExampleNote = vi.fn()
const uploadPdfToExactAzureUrl = vi.fn()
const uploadFileToAzure = vi.fn()

vi.mock("~/configuration/client", () => ({
  generateSignedExampleNoteUploadUrl: (...args: unknown[]) =>
    generateSignedExampleNoteUploadUrl(...args),
  generateSignedExampleNoteUploadUrlV2: (...args: unknown[]) =>
    generateSignedExampleNoteUploadUrlV2(...args),
  uploadExampleNote: (...args: unknown[]) => uploadExampleNote(...args),
  uploadExampleNoteV2: (...args: unknown[]) => uploadExampleNoteV2(...args),
}))

vi.mock("~/lib/azure-upload", () => ({
  uploadFileToAzure: (...args: unknown[]) => uploadFileToAzure(...args),
  uploadPdfToExactAzureUrl: (...args: unknown[]) =>
    uploadPdfToExactAzureUrl(...args),
}))

const grant = {
  upload_url: "https://uploads.example/server-generated.pdf?sig=secret",
  registration_token: "opaque-registration-token",
}

describe("submitExampleNote", () => {
  beforeEach(() => {
    generateSignedExampleNoteUploadUrlV2.mockReset().mockResolvedValue({
      data: grant,
      error: undefined,
    })
    uploadPdfToExactAzureUrl.mockReset().mockResolvedValue({ error: false })
    uploadExampleNoteV2.mockReset().mockResolvedValue({ error: undefined })
    generateSignedExampleNoteUploadUrl.mockReset().mockResolvedValue({
      data: "https://uploads.example/example-notes?sp=c&sig=legacy",
      error: undefined,
    })
    uploadFileToAzure.mockReset().mockResolvedValue({
      stampedFileName: "user_owner_1787572800000_note.pdf",
      error: false,
    })
    uploadExampleNote.mockReset().mockResolvedValue({ error: undefined })
  })

  it("uploads to the exact URL before registering with the opaque token", async () => {
    const file = new File(["%PDF-1.7"], "patient-name.pdf", {
      type: "application/pdf",
    })

    await expect(submitExampleNote(file, "SOAP Note")).resolves.toEqual({
      ok: true,
    })

    expect(uploadPdfToExactAzureUrl).toHaveBeenCalledWith(
      file,
      grant.upload_url,
    )
    expect(uploadExampleNoteV2).toHaveBeenCalledWith({
      body: {
        registration_token: grant.registration_token,
        note_type: "SOAP Note",
      },
    })
    expect(uploadPdfToExactAzureUrl.mock.invocationCallOrder[0]).toBeLessThan(
      uploadExampleNoteV2.mock.invocationCallOrder[0],
    )
  })

  it("fails visibly at preparation when the server does not return a grant", async () => {
    generateSignedExampleNoteUploadUrlV2.mockResolvedValue({
      data: undefined,
      error: { detail: "unavailable" },
    })

    await expect(
      submitExampleNote(new File(["%PDF-1.7"], "note.pdf"), "SOAP Note"),
    ).resolves.toEqual({ ok: false, stage: "preparation" })
    expect(uploadPdfToExactAzureUrl).not.toHaveBeenCalled()
    expect(uploadExampleNoteV2).not.toHaveBeenCalled()
    expect(generateSignedExampleNoteUploadUrl).not.toHaveBeenCalled()
  })

  it("rejects the legacy string response without attempting an upload", async () => {
    generateSignedExampleNoteUploadUrlV2.mockResolvedValue({
      data: "https://uploads.example/container?sig=legacy",
      error: undefined,
    })

    await expect(
      submitExampleNote(new File(["%PDF-1.7"], "note.pdf"), "SOAP Note"),
    ).resolves.toEqual({ ok: false, stage: "preparation" })
    expect(uploadPdfToExactAzureUrl).not.toHaveBeenCalled()
    expect(uploadExampleNoteV2).not.toHaveBeenCalled()
    expect(generateSignedExampleNoteUploadUrl).not.toHaveBeenCalled()
  })

  it("uses the legacy contract only when a rolling old API lacks v2", async () => {
    generateSignedExampleNoteUploadUrlV2.mockResolvedValue({
      data: undefined,
      error: { detail: "Not Found" },
      response: new Response(null, { status: 404 }),
    })
    const file = new File(["%PDF-1.7"], "note.pdf", {
      type: "application/pdf",
    })

    await expect(submitExampleNote(file, "SOAP Note")).resolves.toEqual({
      ok: true,
    })

    expect(generateSignedExampleNoteUploadUrl).toHaveBeenCalledOnce()
    expect(uploadFileToAzure).toHaveBeenCalledWith(
      file,
      "https://uploads.example/example-notes?sp=c&sig=legacy",
    )
    expect(uploadExampleNote).toHaveBeenCalledWith({
      query: {
        file_name: "user_owner_1787572800000_note.pdf",
        note_type: "SOAP Note",
      },
    })
    expect(uploadPdfToExactAzureUrl).not.toHaveBeenCalled()
    expect(uploadExampleNoteV2).not.toHaveBeenCalled()
  })

  it("does not downgrade when the v2 API reports an outage", async () => {
    generateSignedExampleNoteUploadUrlV2.mockResolvedValue({
      data: undefined,
      error: { detail: "Unavailable" },
      response: new Response(null, { status: 503 }),
    })

    await expect(
      submitExampleNote(new File(["%PDF-1.7"], "note.pdf"), "SOAP Note"),
    ).resolves.toEqual({ ok: false, stage: "preparation" })

    expect(generateSignedExampleNoteUploadUrl).not.toHaveBeenCalled()
    expect(uploadFileToAzure).not.toHaveBeenCalled()
  })

  it("does not register when Azure rejects the upload", async () => {
    uploadPdfToExactAzureUrl.mockResolvedValue({ error: true })

    await expect(
      submitExampleNote(new File(["%PDF-1.7"], "note.pdf"), "SOAP Note"),
    ).resolves.toEqual({ ok: false, stage: "upload" })
    expect(uploadExampleNoteV2).not.toHaveBeenCalled()
  })

  it("reports registration rejection after a successful blob upload", async () => {
    uploadExampleNoteV2.mockResolvedValue({ error: { detail: "invalid PDF" } })

    await expect(
      submitExampleNote(new File(["%PDF-1.7"], "note.pdf"), "SOAP Note"),
    ).resolves.toEqual({ ok: false, stage: "registration" })
  })

  it("turns network exceptions into the failure stage where they occurred", async () => {
    uploadPdfToExactAzureUrl.mockRejectedValue(new Error("secret URL"))

    await expect(
      submitExampleNote(new File(["%PDF-1.7"], "note.pdf"), "SOAP Note"),
    ).resolves.toEqual({ ok: false, stage: "upload" })
  })

  it("keeps preparation, upload, and PDF-validation errors user-visible", () => {
    expect(exampleNoteUploadErrorCopy("preparation")).toEqual({
      title: "Failed to prepare note upload. Please try again.",
    })
    expect(exampleNoteUploadErrorCopy("upload")).toEqual({
      title: "Failed to upload note. Please try again.",
    })
    expect(exampleNoteUploadErrorCopy("registration")).toEqual({
      title: "Example note was not accepted",
      description: "Please choose a valid PDF under 10MB and try again.",
    })
  })
})
