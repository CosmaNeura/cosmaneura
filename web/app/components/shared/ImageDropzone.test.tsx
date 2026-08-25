import { describe, expect, it, vi } from "vitest"

import { EXAMPLE_NOTE_MAX_FILE_SIZE, ImageDropzone } from "./ImageDropzone"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"

function renderDropzone(onDropFile = vi.fn()) {
  const view = render(
    <ImageDropzone onDropFile={onDropFile}>
      <span>Select an example note</span>
    </ImageDropzone>,
  )
  const input =
    view.container.querySelector<HTMLInputElement>('input[type="file"]')
  expect(input).not.toBeNull()
  return { input: input!, onDropFile }
}

describe("example note dropzone", () => {
  it("accepts PDF files", async () => {
    const { input, onDropFile } = renderDropzone()
    const pdf = new File(["%PDF-1.7"], "example.pdf", {
      type: "application/pdf",
    })

    fireEvent.change(input, { target: { files: [pdf] } })

    await waitFor(() => expect(onDropFile).toHaveBeenCalledWith(pdf))
  })

  it("rejects images and tells the user only PDFs are allowed", async () => {
    const { input, onDropFile } = renderDropzone()
    const image = new File(["image"], "example.jpg", {
      type: "image/jpeg",
    })

    expect(input).toHaveAttribute("accept", "application/pdf,.pdf")
    fireEvent.change(input, { target: { files: [image] } })

    await waitFor(() =>
      expect(screen.getByText("Only .pdf are allowed")).toBeInTheDocument(),
    )
    expect(onDropFile).not.toHaveBeenCalled()
  })

  it("rejects PDFs larger than 10 MB with a visible size error", async () => {
    const { input, onDropFile } = renderDropzone()
    const pdf = new File(["%PDF-1.7"], "too-large.pdf", {
      type: "application/pdf",
    })
    Object.defineProperty(pdf, "size", {
      value: EXAMPLE_NOTE_MAX_FILE_SIZE + 1,
    })

    fireEvent.change(input, { target: { files: [pdf] } })

    await waitFor(() =>
      expect(screen.getByText("Max size is 10.00MB")).toBeInTheDocument(),
    )
    expect(onDropFile).not.toHaveBeenCalled()
  })
})
