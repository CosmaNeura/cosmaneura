import { describe, expect, it, vi } from "vitest"

import { EmptySelectionState } from "./index"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor } from "@testing-library/react"

// index.tsx pulls in the generated client (which demands Clerk env config)
// through the Header import chain; stub it out
vi.mock("~/configuration/client", () => ({
  generateSignedVisitRecordingUploadUrl: vi.fn(),
  uploadVisitAudioFile: vi.fn(),
}))
vi.mock("~/lib/azure-upload", () => ({
  VISIT_UPLOAD_MAX_BYTES: 2_000 * 1024 * 1024,
  uploadToExactAzureUrl: vi.fn(),
}))

// The terminology hook reads the settings query; serve a therapist_client
// doctor to prove the label wiring end-to-end.
vi.mock("client/@tanstack/react-query.gen", () => ({
  settingsDataOptions: () => ({
    queryKey: ["settingsData"],
    queryFn: () => Promise.resolve({ terminology: "therapist_client" }),
  }),
}))

function renderWithQueryClient(ui: React.ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>,
  )
}

describe("notes empty state terminology", () => {
  it("renders client labels for a therapist_client doctor", async () => {
    renderWithQueryClient(<EmptySelectionState />)

    // falls back to doctor/patient until settings resolve...
    expect(screen.getByText("Select a patient")).toBeInTheDocument()

    // ...then swaps to the configured label set
    await waitFor(() =>
      expect(screen.getByText("Select a client")).toBeInTheDocument(),
    )
    expect(
      screen.getByText("Choose a client from the list to view their details"),
    ).toBeInTheDocument()
  })
})
