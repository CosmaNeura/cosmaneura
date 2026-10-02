import { webcrypto } from "node:crypto"
import { RouterProvider, createMemoryRouter } from "react-router"
import { beforeEach, describe, expect, it, vi } from "vitest"

import {
  expected,
  header,
  op,
  reply,
  source,
} from "~/lib/cab-provider-header.test-fixtures"

import { CabBillingHeaderPanel } from "./CabBillingHeaderPanel"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"

function mount(request: ReturnType<typeof vi.fn>) {
  const locked = vi.fn()
  const router = createMemoryRouter([
    {
      path: "/",
      element: (
        <CabBillingHeaderPanel
          operationId={op}
          expectedSource={expected}
          request={request}
          onLocked={locked}
        />
      ),
    },
  ])
  render(<RouterProvider router={router} />)
  return { locked, router }
}
beforeEach(() => {
  vi.stubGlobal("crypto", webcrypto)
})

describe("inline provider billing header", () => {
  it("accepts and reads on opening, then generates only when requested", async () => {
    const request = vi.fn(async (action: string) =>
      reply(action, action === "generate" ? header() : null),
    )
    mount(request)
    const generate = await screen.findByRole("button", {
      name: "Generate billing header",
    })
    expect(request.mock.calls.map(([action]) => action)).toEqual([
      "accept",
      "read",
    ])
    fireEvent.click(generate)
    expect(
      await screen.findByRole("button", { name: "Edit header" }),
    ).toBeVisible()
    expect(
      screen.getByText("Code proposals await coder review.", { exact: false }),
    ).toBeVisible()
  })
  it("keeps a lost save and retries its identical UUID and draft", async () => {
    const view = header(),
      original = JSON.stringify(view)
    const saves: unknown[] = []
    const request = vi.fn(async (action: string, command: unknown) => {
      if (action !== "save")
        return reply(action, action === "read" ? view : null)
      saves.push(structuredClone(command))
      throw new Error("response lost")
    })
    const { locked } = mount(request)
    fireEvent.click(await screen.findByRole("button", { name: "Edit header" }))
    fireEvent.change(screen.getByLabelText("Visit type"), {
      target: { value: "Fertility follow-up" },
    })
    fireEvent.change(screen.getByLabelText("Reason for edit"), {
      target: { value: "Provider clarification" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Save header edits" }))
    fireEvent.click(
      await screen.findByRole("button", { name: "Retry same request" }),
    )
    await waitFor(() => expect(saves).toHaveLength(2))
    expect(saves[1]).toEqual(saves[0])
    expect(screen.getByLabelText("Visit type")).toHaveValue(
      "Fertility follow-up",
    )
    expect(screen.getByLabelText("Visit type")).toBeDisabled()
    expect(locked).toHaveBeenLastCalledWith(true)
    expect(JSON.stringify(view)).toBe(original)
  })
  it("retains a rejected draft after a revision conflict and prevents another save", async () => {
    const request = vi.fn(async (action: string) => {
      if (action === "save")
        throw Object.assign(new Error("conflict"), { status: 409 })
      return reply(action, action === "read" ? header() : null)
    })
    mount(request)
    fireEvent.click(await screen.findByRole("button", { name: "Edit header" }))
    fireEvent.change(screen.getByLabelText("Reason for edit"), {
      target: { value: "A retained reason" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Save header edits" }))
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "unsaved inputs are retained",
    )
    expect(screen.getByLabelText("Reason for edit")).toHaveValue(
      "A retained reason",
    )
    expect(
      screen.getByRole("button", { name: "Save header edits" }),
    ).toBeDisabled()
    expect(
      screen.queryByRole("button", { name: "Retry same request" }),
    ).toBeNull()
  })
  it("allows editing a saved header when generation is no longer admitted", async () => {
    const restricted = {
      ...source,
      inferencePolicy: { allowed: false, reason: "expired" },
    }
    const request = vi.fn(async (action: string) =>
      reply(action, action === "read" ? header() : null, restricted),
    )
    mount(request)
    expect(
      await screen.findByRole("button", { name: "Edit header" }),
    ).toBeEnabled()
    expect(screen.getByText(/New generation is unavailable/)).toBeVisible()
    expect(request.mock.calls.map(([action]) => action)).toEqual([
      "accept",
      "read",
    ])
  })
})
