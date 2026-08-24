import { beforeEach, describe, expect, it, vi } from "vitest"

import {
  exampleNoteUploadErrorCopy,
  submitExampleNote,
} from "./example-note-upload"

const generateSignedExampleNoteUploadUrl = vi.fn()
const uploadExampleNote = vi.fn()
const uploadPdfToExactAzureUrl = vi.fn()

vi.mock("~/configuration/client", () => ({
  generateSignedExampleNoteUploadUrl: (...args: unknown[]) =>
    generateSignedExampleNoteUploadUrl(...args),
  uploadExampleNote: (...args: unknown[]) => uploadExampleNote(...args),
}))

vi.mock("~/lib/azure-upload", () => ({
  uploadPdfToExactAzureUrl: (...args: unknown[]) =>
    uploadPdfToExactAzureUrl(...args),
}))

const grant = {
  upload_url: "https://uploads.example/server-generated.pdf?sig=secret",
  registration_token: "opaque-registration-token",
}

describe("submitExampleNote", () => {
  beforeEach(() => {
    generateSignedExampleNoteUploadUrl.mockReset().mockResolvedValue({
      data: grant,
      error: undefined,
    })
    uploadPdfToExactAzureUrl.mockReset().mockResolvedValue({ error: false })
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
    expect(uploadExampleNote).toHaveBeenCalledWith({
      body: {
        registration_token: grant.registration_token,
        note_type: "SOAP Note",
      },
    })
    expect(uploadPdfToExactAzureUrl.mock.invocationCallOrder[0]).toBeLessThan(
      uploadExampleNote.mock.invocationCallOrder[0],
    )
  })

  it("fails visibly at preparation when the server does not return a grant", async () => {
    generateSignedExampleNoteUploadUrl.mockResolvedValue({
      data: undefined,
      error: { detail: "unavailable" },
    })

    await expect(
      submitExampleNote(new File(["%PDF-1.7"], "note.pdf"), "SOAP Note"),
    ).resolves.toEqual({ ok: false, stage: "preparation" })
    expect(uploadPdfToExactAzureUrl).not.toHaveBeenCalled()
    expect(uploadExampleNote).not.toHaveBeenCalled()
  })

  it("rejects the legacy string response without attempting an upload", async () => {
    generateSignedExampleNoteUploadUrl.mockResolvedValue({
      data: "https://uploads.example/container?sig=legacy",
      error: undefined,
    })

    await expect(
      submitExampleNote(new File(["%PDF-1.7"], "note.pdf"), "SOAP Note"),
    ).resolves.toEqual({ ok: false, stage: "preparation" })
    expect(uploadPdfToExactAzureUrl).not.toHaveBeenCalled()
    expect(uploadExampleNote).not.toHaveBeenCalled()
  })

  it("does not register when Azure rejects the upload", async () => {
    uploadPdfToExactAzureUrl.mockResolvedValue({ error: true })

    await expect(
      submitExampleNote(new File(["%PDF-1.7"], "note.pdf"), "SOAP Note"),
    ).resolves.toEqual({ ok: false, stage: "upload" })
    expect(uploadExampleNote).not.toHaveBeenCalled()
  })

  it("reports registration rejection after a successful blob upload", async () => {
    uploadExampleNote.mockResolvedValue({ error: { detail: "invalid PDF" } })

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
