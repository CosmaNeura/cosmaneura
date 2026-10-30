import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import {
  correctSessionPrepSourceNote,
  reviewNoteForSessionPrep,
} from "~/configuration/client"

import { SessionPrepSourceNoteEditor } from "./SessionPrepSourceNoteEditor"
import VisitNoteViewer from "./VisitNoteViewer"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"

const { reload, fullReload } = vi.hoisted(() => ({
  reload: vi.fn(),
  fullReload: vi.fn(),
}))
vi.mock("react-router", () => ({
  useRevalidator: () => ({ revalidate: reload }),
}))
vi.mock("~/configuration/client", () => ({
  correctSessionPrepSourceNote: vi.fn(),
  reviewNoteForSessionPrep: vi.fn(),
  prepareBillingHeaderOperation: vi.fn(),
}))
const digest = "a".repeat(64)
const fields = {
  "Labs - 02/12/2026": "BMP is pending.",
  Provider: "Incorrect template identity.",
}
const props = {
  noteId: "visit_note_synthetic",
  digest,
  fields,
  onEditingChange: vi.fn(),
}

function begin() {
  render(<SessionPrepSourceNoteEditor {...props} />)
  fireEvent.click(screen.getByRole("button", { name: "Correct this note" }))
}

describe("SessionPrepSourceNoteEditor", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    reload.mockReset().mockResolvedValue(undefined)
    vi.stubGlobal("location", { reload: fullReload })
  })
  afterEach(() => vi.unstubAllGlobals())

  it("saves visit-only text and heading corrections, removes an incorrect section, and reloads for fresh review", async () => {
    vi.mocked(correctSessionPrepSourceNote).mockResolvedValue({
      data: { note_id: "visit_note_corrected", content_sha256: "b".repeat(64) },
    } as never)
    begin()
    fireEvent.change(
      screen.getByRole("textbox", { name: "Section 1 heading" }),
      { target: { value: "Labs" } },
    )
    fireEvent.change(screen.getByRole("textbox", { name: "Section 1 text" }), {
      target: { value: "BMP was cancelled; no result is documented." },
    })
    fireEvent.click(screen.getByRole("button", { name: "Remove section 2" }))
    fireEvent.click(screen.getByRole("button", { name: "Save correction" }))
    await waitFor(() => expect(reload).toHaveBeenCalledTimes(1))
    expect(correctSessionPrepSourceNote).toHaveBeenCalledExactlyOnceWith({
      path: { note_id: "visit_note_synthetic" },
      body: {
        expected_content_sha256: digest,
        fields: { Labs: "BMP was cancelled; no result is documented." },
      },
      throwOnError: false,
    })
    expect(reviewNoteForSessionPrep).not.toHaveBeenCalled()
  })

  it("discards unsaved changes without sending a write", () => {
    begin()
    fireEvent.change(screen.getByRole("textbox", { name: "Section 1 text" }), {
      target: { value: "Unsaved correction." },
    })
    fireEvent.click(screen.getByRole("button", { name: "Discard edits" }))
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument()
    expect(props.onEditingChange).toHaveBeenLastCalledWith(false)
    expect(correctSessionPrepSourceNote).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole("button", { name: "Correct this note" }))
    expect(screen.getByRole("textbox", { name: "Section 1 text" })).toHaveValue(
      fields["Labs - 02/12/2026"],
    )
  })

  it.each([404, 409, 503])(
    "retains draft text and closes blind retries after an unconfirmed or stale %i response",
    async (status) => {
      vi.mocked(correctSessionPrepSourceNote).mockResolvedValue({
        response: new Response(null, { status }),
      } as never)
      begin()
      fireEvent.change(
        screen.getByRole("textbox", { name: "Section 1 text" }),
        { target: { value: "Clinician correction retained." } },
      )
      fireEvent.click(screen.getByRole("button", { name: "Save correction" }))
      expect(await screen.findByRole("alert")).toHaveTextContent(
        "Edits remain only in this open editor",
      )
      expect(
        screen.getByRole("textbox", { name: "Section 1 text" }),
      ).toHaveValue("Clinician correction retained.")
      expect(
        screen.getByRole("textbox", { name: "Section 1 text" }),
      ).toBeEnabled()
      expect(
        screen.getByRole("textbox", { name: "Section 1 text" }),
      ).toHaveAttribute("readonly")
      expect(
        screen.getByRole("button", { name: "Save correction" }),
      ).toBeDisabled()
      fireEvent.click(screen.getByRole("button", { name: "Save correction" }))
      expect(correctSessionPrepSourceNote).toHaveBeenCalledTimes(1)
      fireEvent.click(
        screen.getByRole("button", { name: "Discard edits and reload visit" }),
      )
      expect(fullReload).toHaveBeenCalledTimes(1)
      expect(reload).not.toHaveBeenCalled()
    },
  )

  it("recovers at a full fresh-read boundary even if the note ID and digest are unchanged", async () => {
    vi.mocked(correctSessionPrepSourceNote).mockResolvedValue({
      response: new Response(null, { status: 503 }),
    } as never)
    const view = render(<SessionPrepSourceNoteEditor {...props} />)
    fireEvent.click(screen.getByRole("button", { name: "Correct this note" }))
    fireEvent.change(screen.getByRole("textbox", { name: "Section 1 text" }), {
      target: { value: "Unconfirmed correction" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Save correction" }))
    await screen.findByRole("alert")
    fireEvent.click(
      screen.getByRole("button", { name: "Discard edits and reload visit" }),
    )
    expect(fullReload).toHaveBeenCalledOnce()
    view.unmount()
    render(<SessionPrepSourceNoteEditor {...props} />)
    fireEvent.click(screen.getByRole("button", { name: "Correct this note" }))
    expect(screen.getByRole("textbox", { name: "Section 1 text" })).toHaveValue(
      fields["Labs - 02/12/2026"],
    )
    expect(props.onEditingChange).toHaveBeenLastCalledWith(true)
  })

  it("keeps a confirmed save distinct from a failed refresh", async () => {
    vi.mocked(correctSessionPrepSourceNote).mockResolvedValue({
      data: { note_id: "visit_note_corrected", content_sha256: "b".repeat(64) },
    } as never)
    reload.mockRejectedValue(new Error("Synthetic loader failure"))
    begin()
    fireEvent.change(screen.getByRole("textbox", { name: "Section 1 text" }), {
      target: { value: "Correction" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Save correction" }))
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Changes saved, but the visit could not refresh",
    )
    expect(screen.queryByText(/Save was not confirmed/)).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Reload saved visit" }))
    expect(fullReload).toHaveBeenCalledOnce()
  })

  it("drops an old-patient draft and stale asynchronous callbacks on note identity change", async () => {
    let resolveSave: (result: never) => void = () => {}
    vi.mocked(correctSessionPrepSourceNote).mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveSave = resolve
        }) as never,
    )
    const view = render(<SessionPrepSourceNoteEditor {...props} />)
    fireEvent.click(screen.getByRole("button", { name: "Correct this note" }))
    fireEvent.change(screen.getByRole("textbox", { name: "Section 1 text" }), {
      target: { value: "Old-patient draft" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Save correction" }))
    view.rerender(
      <SessionPrepSourceNoteEditor
        {...props}
        noteId="visit_note_other_patient"
        fields={{ Plan: "Other synthetic patient" }}
      />,
    )
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument()
    expect(props.onEditingChange).toHaveBeenLastCalledWith(false)
    resolveSave({
      data: { note_id: "visit_note_corrected", content_sha256: "b".repeat(64) },
    } as never)
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Correct this note" }),
      ).toBeEnabled(),
    )
    expect(reload).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole("button", { name: "Correct this note" }))
    expect(screen.getByRole("textbox", { name: "Section 1 text" })).toHaveValue(
      "Other synthetic patient",
    )
  })

  it("retains editable text after a validation rejection", async () => {
    vi.mocked(correctSessionPrepSourceNote).mockResolvedValue({
      response: new Response(null, { status: 422 }),
    } as never)
    begin()
    fireEvent.change(screen.getByRole("textbox", { name: "Section 1 text" }), {
      target: { value: "Correction" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Save correction" }))
    expect(await screen.findByRole("alert")).toHaveTextContent("18,000")
    expect(
      screen.getByRole("textbox", { name: "Section 1 text" }),
    ).toBeEnabled()
    expect(
      screen.getByRole("button", { name: "Save correction" }),
    ).toBeEnabled()
  })

  it("blocks review of the displayed old version while changes are unsaved", () => {
    render(
      <VisitNoteViewer
        defaultOpen
        sourceEditable
        note={{
          id: "visit_note_synthetic",
          doctor_id: "doctor_synthetic",
          appointment_id: "appointment_synthetic",
          ai_transcript_id: "ai_visit_transcript_synthetic",
          doctor_note_schema_id: "doctor_note_schema_synthetic",
          note_type: "SOAP",
          note: fields,
        }}
        review={{ content_sha256: digest }}
      />,
    )
    expect(
      screen.getByRole("button", { name: "Mark reviewed for Session Prep" }),
    ).toBeVisible()
    fireEvent.click(screen.getByRole("button", { name: "Correct this note" }))
    expect(
      screen.queryByRole("button", { name: "Mark reviewed for Session Prep" }),
    ).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Discard edits" }))
    expect(
      screen.getByRole("button", { name: "Mark reviewed for Session Prep" }),
    ).toBeVisible()
  })

  it("restores review when an older backend removes the editing affordance", () => {
    const note = {
      id: "visit_note_synthetic",
      doctor_id: "doctor_synthetic",
      appointment_id: "appointment_synthetic",
      ai_transcript_id: "ai_visit_transcript_synthetic",
      doctor_note_schema_id: "doctor_note_schema_synthetic",
      note_type: "SOAP",
      note: fields,
    }
    const view = render(
      <VisitNoteViewer
        defaultOpen
        sourceEditable
        note={note}
        review={{ content_sha256: digest }}
      />,
    )
    fireEvent.click(screen.getByRole("button", { name: "Correct this note" }))
    expect(
      screen.queryByRole("button", { name: "Mark reviewed for Session Prep" }),
    ).not.toBeInTheDocument()
    view.rerender(
      <VisitNoteViewer
        defaultOpen
        note={note}
        review={{ content_sha256: digest }}
      />,
    )
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: "Mark reviewed for Session Prep" }),
    ).toBeVisible()
  })
})
