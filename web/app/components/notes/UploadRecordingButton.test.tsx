import { describe, expect, it, vi } from "vitest"

import { UploadRecordingButton } from "./UploadRecordingButton"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { fireEvent, render, screen } from "@testing-library/react"

vi.mock("~/configuration/client", () => ({ client: {} }))
vi.mock("~/lib/visit-audio-upload", () => ({
  submitVisitAudioUpload: vi.fn(),
}))
vi.mock("~/utils/terminology", () => ({
  useTerminology: () => ({ patient: "patient", patientTitle: "Patient" }),
}))
vi.mock("client/@tanstack/react-query.gen", () => ({
  patientListOptions: () => ({
    queryKey: ["patients"],
    queryFn: () => Promise.resolve([]),
  }),
}))

function renderButton() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <UploadRecordingButton />
    </QueryClientProvider>,
  )
}

describe("UploadRecordingButton", () => {
  it("keeps the primary upload action visible and the dialog scrollable", () => {
    renderButton()

    const button = screen.getByRole("button", { name: "Upload Recording" })
    expect(button).toBeVisible()
    expect(button).not.toHaveClass("hidden")
    expect(screen.getByText("Upload")).toHaveClass("sm:hidden")
    expect(screen.getByText("Upload Recording")).toHaveClass(
      "hidden",
      "sm:inline",
    )

    fireEvent.click(button)

    expect(screen.getByRole("dialog")).toHaveClass(
      "max-h-[calc(100dvh-2rem)]",
      "overflow-y-auto",
    )
  })
})
