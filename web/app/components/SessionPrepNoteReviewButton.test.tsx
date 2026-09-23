import { beforeEach, describe, expect, it, vi } from "vitest"

import { reviewNoteForSessionPrep } from "~/configuration/client"

import { SessionPrepNoteReviewButton } from "./SessionPrepNoteReviewButton"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"

vi.mock("~/configuration/client", () => ({
  reviewNoteForSessionPrep: vi.fn(),
}))

const digest = "a".repeat(64)

describe("SessionPrepNoteReviewButton", () => {
  beforeEach(() => vi.clearAllMocks())

  it("requires an explicit action and sends the exact displayed digest", async () => {
    vi.mocked(reviewNoteForSessionPrep).mockResolvedValue({
      data: { content_sha256: digest, reviewed_at: "2026-09-23T12:00:00Z" },
      response: new Response(null, { status: 200 }),
    } as never)
    render(
      <SessionPrepNoteReviewButton
        noteId="visit_note_synthetic"
        review={{ content_sha256: digest, reviewed_at: null }}
      />,
    )
    expect(reviewNoteForSessionPrep).not.toHaveBeenCalled()
    fireEvent.click(
      screen.getByRole("button", { name: "Mark reviewed for Session Prep" }),
    )
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Reviewed for Session Prep",
    )
    expect(reviewNoteForSessionPrep).toHaveBeenCalledExactlyOnceWith({
      path: { note_id: "visit_note_synthetic" },
      body: { expected_content_sha256: digest },
      throwOnError: false,
    })
    expect(screen.queryByRole("button")).not.toBeInTheDocument()
  })

  it.each([409, 503])(
    "does not claim a review after status %i",
    async (status) => {
      vi.mocked(reviewNoteForSessionPrep).mockResolvedValue({
        response: new Response(null, { status }),
      } as never)
      render(
        <SessionPrepNoteReviewButton
          noteId="visit_note_synthetic"
          review={{ content_sha256: digest }}
        />,
      )
      fireEvent.click(screen.getByRole("button"))
      const error = await screen.findByRole("alert")
      expect(error).toHaveTextContent(
        status === 409 ? "Reload the page" : "could not be saved",
      )
      expect(screen.queryByRole("status")).not.toBeInTheDocument()
      expect(screen.getByRole("button")).toBeEnabled()
    },
  )

  it("retains a confirmed review when the page is loaded again", () => {
    render(
      <SessionPrepNoteReviewButton
        noteId="visit_note_synthetic"
        review={{ content_sha256: digest, reviewed_at: "2026-09-23T12:00:00Z" }}
      />,
    )
    expect(screen.getByRole("status")).toHaveTextContent(
      "Reviewed for Session Prep",
    )
    expect(reviewNoteForSessionPrep).not.toHaveBeenCalled()
  })

  it("does not submit a second action while the first is pending", async () => {
    let finish: (value: never) => void = () => {}
    vi.mocked(reviewNoteForSessionPrep).mockReturnValue(
      new Promise<never>((resolve) => {
        finish = resolve
      }),
    )
    render(
      <SessionPrepNoteReviewButton
        noteId="visit_note_synthetic"
        review={{ content_sha256: digest }}
      />,
    )
    fireEvent.click(screen.getByRole("button"))
    fireEvent.click(screen.getByRole("button"))
    expect(reviewNoteForSessionPrep).toHaveBeenCalledTimes(1)
    finish({ response: new Response(null, { status: 503 }) } as never)
    await waitFor(() => expect(screen.getByRole("button")).toBeEnabled())
  })
})
