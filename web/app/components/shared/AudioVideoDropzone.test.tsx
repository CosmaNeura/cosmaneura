import { describe, expect, it, vi } from "vitest"

import { AudioVideoDropzone } from "./AudioVideoDropzone"
import { render, screen } from "@testing-library/react"

describe("AudioVideoDropzone", () => {
  it("states the size limit derived from the upload ceiling", () => {
    render(<AudioVideoDropzone file={null} onDropFile={vi.fn()} />)

    expect(screen.getByText(/smaller than 2 GB/)).toBeInTheDocument()
  })

  it("shows the chosen file with its size and a way to change it", () => {
    const file = new File([new Uint8Array(3 * 1024 * 1024)], "visit.m4a", {
      type: "audio/mp4",
    })
    render(<AudioVideoDropzone file={file} onDropFile={vi.fn()} />)

    const name = screen.getByText("visit.m4a")
    expect(name).toHaveAttribute("data-ph-no-capture")
    expect(name).toHaveAttribute("data-sentry-block")
    expect(screen.getByText("3 MB")).toBeInTheDocument()
    expect(screen.getByText("Choose a different file")).toBeInTheDocument()
    expect(screen.queryByText(/Press the upload button/)).toBeNull()
  })
})
