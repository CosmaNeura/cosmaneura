import { describe, expect, it, vi } from "vitest"

import { SessionPrepCard } from "./SessionPrepCard"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"

const generateSessionPrep = vi.fn()
vi.mock("~/configuration/client", () => ({
  generateSessionPrep: (...args: unknown[]) => generateSessionPrep(...args),
}))
vi.mock("~/hooks/use-toast", () => ({ toast: vi.fn() }))

const sessionPrepLatestQueryFn = vi.fn()
vi.mock("client/@tanstack/react-query.gen", () => ({
  sessionPrepLatestOptions: (options?: { query?: Record<string, string> }) => ({
    queryKey: ["session-prep-latest", options?.query ?? {}],
    queryFn: () => sessionPrepLatestQueryFn(options),
  }),
}))

function renderCard(props: { patientId?: string; appointmentId?: string }) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <SessionPrepCard {...props} />
    </QueryClientProvider>,
  )
}

const COMPLETED_PREP = {
  id: "session_prep_1",
  patient_id: "patient_1",
  appointment_id: null,
  status: "completed",
  talking_points: ["Follow up on sleep log", "Ask about new job stress"],
  open_threads: ["Practice breathing exercise daily"],
  alerts: ["Passive SI mentioned last session"],
  personality_note: "Prefers collaborative decisions",
  days_since_last_session: 7,
  visits_in_context: 3,
  created_at: "2026-07-22T00:00:00Z",
}

describe("SessionPrepCard", () => {
  it("shows the generate button when no prep exists", async () => {
    sessionPrepLatestQueryFn.mockRejectedValue(new Error("404"))

    renderCard({ patientId: "patient_1" })

    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Prep for session" }),
      ).toBeInTheDocument(),
    )
  })

  it("renders a completed brief with alerts first", async () => {
    sessionPrepLatestQueryFn.mockResolvedValue(COMPLETED_PREP)

    renderCard({ patientId: "patient_1" })

    await waitFor(() =>
      expect(screen.getByText(/Follow up on sleep log/)).toBeInTheDocument(),
    )
    expect(
      screen.getByText(/Passive SI mentioned last session/),
    ).toBeInTheDocument()
    expect(
      screen.getByText(/Practice breathing exercise daily/),
    ).toBeInTheDocument()
    expect(
      screen.getByText(/Prefers collaborative decisions/),
    ).toBeInTheDocument()
    expect(screen.getByText(/3 prior visits/)).toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: "Regenerate" }),
    ).toBeInTheDocument()
  })

  it("starts generation with the right target", async () => {
    sessionPrepLatestQueryFn.mockRejectedValue(new Error("404"))
    generateSessionPrep.mockResolvedValue({
      data: { ...COMPLETED_PREP, status: "pending", talking_points: [] },
    })

    renderCard({ appointmentId: "appointment_9" })

    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Prep for session" }),
      ).toBeInTheDocument(),
    )

    fireEvent.click(screen.getByRole("button", { name: "Prep for session" }))

    await waitFor(() =>
      expect(generateSessionPrep).toHaveBeenCalledWith({
        body: { appointment_id: "appointment_9" },
      }),
    )
    await waitFor(() =>
      expect(screen.getByText("Preparing brief…")).toBeInTheDocument(),
    )
  })
})
