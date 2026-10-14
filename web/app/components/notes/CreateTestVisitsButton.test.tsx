import { beforeEach, describe, expect, it, vi } from "vitest"

import { CreateTestVisitsButton } from "./CreateTestVisitsButton"
import { Header } from "./Header"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"

const mocks = vi.hoisted(() => ({
  create: vi.fn(),
  revalidate: vi.fn(),
  staging: true,
  Expired: class extends Error {},
}))
vi.mock("~/lib/staging-test-visits", () => ({
  createTestVisits: mocks.create,
  TestVisitRequestExpired: mocks.Expired,
}))
vi.mock("react-router", () => ({
  useRevalidator: () => ({ revalidate: mocks.revalidate }),
  Link: ({
    to,
    children,
    ...props
  }: {
    to: string
    children: React.ReactNode
  }) => (
    <a href={to} {...props}>
      {children}
    </a>
  ),
}))
vi.mock("~/utils/environment", () => ({ isStaging: () => mocks.staging }))
vi.mock("~/components/shared/PageHeader", () => ({
  PageHeader: ({ actions }: { actions: React.ReactNode }) => (
    <div>{actions}</div>
  ),
}))
vi.mock("@/components/notes/UploadRecordingButton", () => ({
  UploadRecordingButton: () => <button>Upload Recording</button>,
}))

beforeEach(() => {
  vi.clearAllMocks()
  sessionStorage.clear()
  mocks.staging = true
})

describe("staging test visits", () => {
  it("keeps creation off production and existing visit headers", () => {
    mocks.staging = false
    const view = render(<Header />)
    expect(screen.queryByText("Create test visits")).not.toBeInTheDocument()
    expect(screen.getByText("Upload Recording")).toBeInTheDocument()
    mocks.staging = true
    view.rerender(<Header title="Existing visit" />)
    expect(screen.queryByText("Create test visits")).not.toBeInTheDocument()
  })

  it("retains the exact request on failure and prevents duplicate clicks", async () => {
    let finish: (value: unknown) => void = () => {}
    mocks.create.mockRejectedValueOnce(new Error("Invented lost response"))
    mocks.create.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve
        }),
    )
    render(<CreateTestVisitsButton />)
    fireEvent.click(screen.getByRole("button", { name: "Create test visits" }))
    fireEvent.click(screen.getByRole("button", { name: "Create two visits" }))
    await screen.findByRole("alert")
    expect(
      screen.queryByRole("button", { name: "Start fresh test pair" }),
    ).not.toBeInTheDocument()
    const request = mocks.create.mock.calls[0][0]
    fireEvent.click(screen.getByRole("button", { name: "Retry same request" }))
    expect(screen.getByRole("button", { name: "Creating…" })).toBeDisabled()
    fireEvent.click(screen.getByRole("button", { name: "Creating…" }))
    expect(mocks.create).toHaveBeenCalledTimes(2)
    expect(mocks.create.mock.calls[1][0]).toEqual(request)
    finish({
      ...request,
      created: false,
      visits: [
        {
          appointment_id: "appointment_01m4x83v1zfa4bttxs033hchjw",
          appointment_name: "Invented acceptance one",
        },
        {
          appointment_id: "appointment_01m4x7f54ffsfavptkpcfyadk2",
          appointment_name: "Invented acceptance two",
        },
      ],
    })
    await screen.findByText("The same two test visits were recovered.")
    expect(
      screen.getByRole("link", { name: "Invented acceptance one" }),
    ).toHaveAttribute("href", "/notes/appointment_01m4x83v1zfa4bttxs033hchjw")
    await waitFor(() => expect(mocks.revalidate).toHaveBeenCalledTimes(1))
  })

  it("starts fresh explicitly after confirmed expired absence", async () => {
    mocks.create.mockRejectedValueOnce(new mocks.Expired())
    mocks.create.mockRejectedValueOnce(new Error("Invented lost response"))
    render(<CreateTestVisitsButton />)
    fireEvent.click(screen.getByRole("button", { name: "Create test visits" }))
    fireEvent.click(screen.getByRole("button", { name: "Create two visits" }))
    await screen.findByText(/The service confirmed it has no saved test visits/)
    const expired = mocks.create.mock.calls[0][0]
    const key = sessionStorage.key(0)!
    expect(JSON.parse(sessionStorage.getItem(key)!)).toEqual(expired)
    fireEvent.click(
      screen.getByRole("button", { name: "Start fresh test pair" }),
    )
    expect(sessionStorage.getItem(key)).toBeNull()
    expect(mocks.create).toHaveBeenCalledTimes(1)
    fireEvent.click(screen.getByRole("button", { name: "Create two visits" }))
    await screen.findByRole("alert")
    expect(mocks.create.mock.calls[1][0].request_id).not.toEqual(
      expired.request_id,
    )
    expect(
      screen.queryByRole("button", { name: "Start fresh test pair" }),
    ).not.toBeInTheDocument()
  })
})
