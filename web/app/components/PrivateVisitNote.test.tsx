import { beforeEach, describe, expect, it, vi } from "vitest"

import { PrivateVisitNote } from "./PrivateVisitNote"
import { act, fireEvent, render, screen } from "@testing-library/react"

const api = vi.hoisted(() => ({ read: vi.fn(), save: vi.fn() }))
vi.mock("~/configuration/client", () => ({
  scribePrivateNoteRead: api.read,
  scribePrivateNoteSave: api.save,
}))
vi.mock("react-router", () => ({ useBlocker: () => ({ state: "unblocked" }) }))

beforeEach(() => {
  vi.useFakeTimers()
  api.read.mockReset().mockResolvedValue({
    data: { text: "Invented saved reminder", version: 2, updated_at: null },
  })
  api.save.mockReset()
})

async function show() {
  render(<PrivateVisitNote appointmentId="appointment_invented" />)
  await act(async () => {
    await Promise.resolve()
  })
  return screen.getByLabelText("Private note")
}

describe("private reminder autosave", () => {
  it("keeps the field unavailable until the current author’s saved version is known", async () => {
    api.read.mockRejectedValue(new Error("Invented read failure"))
    const input = await show()
    expect(input).toBeDisabled()
    expect(screen.getByRole("status")).toHaveTextContent(
      "couldn’t load or save",
    )
    expect(api.save).not.toHaveBeenCalled()
  })

  it.each<[number, string, string | null]>([
    [401, "sign-in session could not be verified", "Check access again"],
    [
      403,
      "unavailable for this visit in the current account",
      "Check access again",
    ],
    [
      404,
      "unavailable for this visit in the current account",
      "Check access again",
    ],
    [422, "request could not be accepted", null],
    [503, "couldn’t load or save", "Try again"],
  ])(
    "keeps a failed %s read closed without presenting it as a save",
    async (code, message, action) => {
      api.read.mockResolvedValue({ response: { status: code } })
      expect(await show()).toBeDisabled()
      expect(screen.getByRole("status")).toHaveTextContent(message!)
      expect(api.save).not.toHaveBeenCalled()
      if (action)
        expect(screen.getByRole("button", { name: action })).toBeVisible()
      else
        expect(
          screen.queryByRole("button", { name: "Try again" }),
        ).not.toBeInTheDocument()
    },
  )

  it("keeps the exact unresolved save and disables editing after access is denied", async () => {
    api.save
      .mockResolvedValueOnce({ response: { status: 403 } })
      .mockResolvedValueOnce({
        data: { text: "Invented retained edit", version: 3 },
      })
    const input = await show()
    fireEvent.change(input, { target: { value: "Invented retained edit" } })
    await act(async () => {
      vi.advanceTimersByTime(750)
    })
    const pending = api.save.mock.calls[0][0].body
    expect(input).toHaveValue("Invented retained edit")
    expect(input).toBeDisabled()
    expect(screen.getByRole("status")).toHaveTextContent("current account")
    await act(async () => {
      vi.advanceTimersByTime(10_000)
    })
    expect(api.save).toHaveBeenCalledTimes(1)
    await act(async () => {
      fireEvent.click(
        screen.getByRole("button", { name: "Check access again" }),
      )
    })
    expect(api.save.mock.calls[1][0].body).toEqual(pending)
    expect(screen.getByRole("status")).toHaveTextContent("Saved")
    expect(input).not.toBeDisabled()
  })

  it("offers a confirmed saved-note reload after an invalid save without silently dropping input", async () => {
    api.save.mockResolvedValue({ response: { status: 422 } })
    const originalConfirm = Object.getOwnPropertyDescriptor(window, "confirm")
    const confirm = vi.fn().mockReturnValue(false)
    Object.defineProperty(window, "confirm", {
      configurable: true,
      value: confirm,
    })
    const input = await show()
    fireEvent.change(input, {
      target: { value: "Invented rejected edit\u0000" },
    })
    await act(async () => {
      vi.advanceTimersByTime(750)
    })
    expect(input).toBeDisabled()
    expect(screen.getByRole("status")).toHaveTextContent(
      "could not be accepted",
    )
    fireEvent.click(screen.getByRole("button", { name: "Reload saved note" }))
    expect(input).toHaveValue("Invented rejected edit\u0000")
    expect(api.read).toHaveBeenCalledTimes(1)
    confirm.mockReturnValue(true)
    api.read.mockResolvedValueOnce({
      data: { text: "Invented saved reminder", version: 3 },
    })
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Reload saved note" }))
    })
    expect(input).toHaveValue("Invented saved reminder")
    expect(input).not.toBeDisabled()
    expect(screen.getByRole("status")).toHaveTextContent("Saved")
    expect(api.save).toHaveBeenCalledTimes(1)
    if (originalConfirm)
      Object.defineProperty(window, "confirm", originalConfirm)
    else Reflect.deleteProperty(window, "confirm")
  })

  it("ignores an older visit’s read after opening another visit", async () => {
    let older!: (value: unknown) => void
    api.read
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            older = resolve
          }),
      )
      .mockResolvedValueOnce({
        data: { text: "Invented next visit reminder", version: 1 },
      })
    const view = render(
      <PrivateVisitNote appointmentId="appointment_invented_first" />,
    )
    await act(async () => {
      view.rerender(
        <PrivateVisitNote appointmentId="appointment_invented_second" />,
      )
    })
    await act(async () => {
      older({ data: { text: "Invented previous visit reminder", version: 8 } })
    })
    expect(screen.getByLabelText("Private note")).toHaveValue(
      "Invented next visit reminder",
    )
  })

  it("keeps typing during a save and serializes the next edit against the acknowledged version", async () => {
    let acknowledge!: (value: unknown) => void
    api.save
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            acknowledge = resolve
          }),
      )
      .mockResolvedValueOnce({
        data: { text: "Invented second edit", version: 4 },
      })
    const input = await show()
    fireEvent.change(input, { target: { value: "Invented first edit" } })
    await act(async () => {
      vi.advanceTimersByTime(750)
    })
    expect(api.save.mock.calls[0][0].body).toMatchObject({
      expected_version: 2,
      text: "Invented first edit",
    })
    fireEvent.change(input, { target: { value: "Invented second edit" } })
    const unload = new Event("beforeunload", { cancelable: true })
    window.dispatchEvent(unload)
    expect(unload.defaultPrevented).toBe(true)
    await act(async () => {
      acknowledge({ data: { text: "Invented first edit", version: 3 } })
    })
    expect(input).toHaveValue("Invented second edit")
    await act(async () => {
      vi.advanceTimersByTime(750)
    })
    expect(api.save.mock.calls[1][0].body).toMatchObject({
      expected_version: 3,
      text: "Invented second edit",
    })
    expect(screen.getByRole("status")).toHaveTextContent("Saved")
  })

  it("retains the exact failed mutation key and local text for retry", async () => {
    api.save
      .mockRejectedValueOnce(new Error("Invented offline failure"))
      .mockResolvedValueOnce({
        data: { text: "Invented offline edit", version: 3 },
      })
    fireEvent.change(await show(), {
      target: { value: "Invented offline edit" },
    })
    await act(async () => {
      vi.advanceTimersByTime(750)
    })
    expect(screen.getByRole("status")).toHaveTextContent("Try again")
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Try again" }))
    })
    expect(api.save.mock.calls[1][0].body).toEqual(
      api.save.mock.calls[0][0].body,
    )
    expect(screen.getByLabelText("Private note")).toHaveValue(
      "Invented offline edit",
    )
  })

  it("keeps local edits visible when another device has changed the note", async () => {
    api.save.mockResolvedValue({ response: { status: 409 } })
    fireEvent.change(await show(), { target: { value: "Invented local edit" } })
    await act(async () => {
      vi.advanceTimersByTime(750)
    })
    expect(screen.getByRole("status")).toHaveTextContent(
      "changed on another device",
    )
    expect(screen.getByLabelText("Private note")).toHaveValue(
      "Invented local edit",
    )
    expect(
      screen.getByRole("button", { name: "Reload saved note" }),
    ).toBeVisible()
    expect(api.read).toHaveBeenCalledTimes(1)
  })
})
