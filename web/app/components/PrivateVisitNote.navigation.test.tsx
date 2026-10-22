import { Link, RouterProvider, createMemoryRouter } from "react-router"
import { expect, it, vi } from "vitest"

import { PrivateVisitNote } from "./PrivateVisitNote"
import { act, fireEvent, render, screen } from "@testing-library/react"

const api = vi.hoisted(() => ({ read: vi.fn(), save: vi.fn() }))
vi.mock("~/configuration/client", () => ({
  scribePrivateNoteRead: api.read,
  scribePrivateNoteSave: api.save,
}))

it("blocks visit navigation during a pending or failed recovery read without losing unsaved input", async () => {
  vi.useFakeTimers()
  api.read.mockResolvedValueOnce({
    data: { text: "Invented saved reminder", version: 2 },
  })
  api.save.mockResolvedValue({ response: { status: 422 } })
  const originalConfirm = Object.getOwnPropertyDescriptor(window, "confirm")
  Object.defineProperty(window, "confirm", {
    configurable: true,
    value: () => true,
  })
  const router = createMemoryRouter([
    {
      path: "/",
      element: (
        <>
          <PrivateVisitNote appointmentId="appointment_invented" />
          <Link to="/next">Next visit</Link>
        </>
      ),
    },
    { path: "/next", element: <p>Different visit</p> },
  ])
  try {
    render(<RouterProvider router={router} />)
    await act(async () => {})
    const input = screen.getByLabelText("Private note")
    fireEvent.change(input, {
      target: { value: "Invented rejected edit\u0000" },
    })
    await act(async () => {
      vi.advanceTimersByTime(750)
    })
    let finishRead!: (value: unknown) => void
    api.read.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finishRead = resolve
        }),
    )
    fireEvent.click(screen.getByRole("button", { name: "Reload saved note" }))
    await act(async () => {
      fireEvent.click(screen.getByRole("link", { name: "Next visit" }))
    })
    expect(router.state.location.pathname).toBe("/")
    expect(
      screen.getByRole("alertdialog", { name: "Save your private note" }),
    ).toBeVisible()
    expect(
      screen.getByRole("button", { name: "Save and continue" }),
    ).toBeDisabled()
    await act(async () => {
      finishRead({ response: { status: 503 } })
    })
    expect(input).toHaveValue("Invented rejected edit\u0000")
    expect(router.state.location.pathname).toBe("/")
    expect(api.save).toHaveBeenCalledTimes(1)
    fireEvent.click(screen.getByRole("button", { name: "Keep editing" }))
    expect(input).toHaveValue("Invented rejected edit\u0000")
  } finally {
    router.dispose()
    if (originalConfirm)
      Object.defineProperty(window, "confirm", originalConfirm)
    else Reflect.deleteProperty(window, "confirm")
    vi.useRealTimers()
  }
})
