import { afterEach, describe, expect, it, vi } from "vitest"

import { CopyButton } from "./CopyButton"
import { fireEvent, render, screen } from "@testing-library/react"

const toast = vi.fn()

vi.mock("~/hooks/use-toast", () => ({
  toast: (...args: unknown[]) => toast(...args),
}))

afterEach(() => {
  toast.mockReset()
  vi.restoreAllMocks()
})

describe("CopyButton", () => {
  it("confirms a copy visibly and to assistive technology", async () => {
    render(<CopyButton text="Synthetic text" label="Copy link" />)

    fireEvent.click(screen.getByRole("button", { name: "Copy link" }))

    expect(await screen.findByRole("button", { name: "Copied" })).toBeVisible()
    expect(screen.getByText("Copied to clipboard")).toHaveAttribute(
      "aria-live",
      "polite",
    )
    expect(await navigator.clipboard.readText()).toBe("Synthetic text")
  })

  it("labels an icon-only button and swaps its label after copying", async () => {
    render(<CopyButton text="Synthetic text" label="Copy Plan" iconOnly />)

    const button = screen.getByRole("button", { name: "Copy Plan" })
    expect(button).not.toHaveTextContent("Copy Plan")

    fireEvent.click(button)

    expect(await screen.findByText("Copied to clipboard")).toBeInTheDocument()
    expect(button).toHaveAccessibleName("Copy Plan")
  })

  it("builds HTML at click time", async () => {
    render(<CopyButton getHtml={() => "<h3>Plan</h3><p>Rest</p>"} />)

    fireEvent.click(screen.getByRole("button", { name: "Copy to Clipboard" }))
    await screen.findByRole("button", { name: "Copied" })

    const [item] = await navigator.clipboard.read()
    expect(await (await item.getType("text/html")).text()).toBe(
      "<h3>Plan</h3><p>Rest</p>",
    )
    expect(await (await item.getType("text/plain")).text()).toContain("Plan")
  })

  it("does not blame the browser when there is nothing to copy", async () => {
    render(<CopyButton text="" />)

    fireEvent.click(screen.getByRole("button", { name: "Copy to Clipboard" }))

    await vi.waitFor(() =>
      expect(toast).toHaveBeenCalledWith({
        title: "There's nothing to copy yet",
      }),
    )
  })

  it("tells the user when the browser refuses the copy", async () => {
    vi.spyOn(navigator.clipboard, "writeText").mockRejectedValue(
      new DOMException("denied", "NotAllowedError"),
    )
    render(<CopyButton text="Synthetic text" />)

    fireEvent.click(screen.getByRole("button", { name: "Copy to Clipboard" }))

    await vi.waitFor(() =>
      expect(toast).toHaveBeenCalledWith(
        expect.objectContaining({
          title: "Couldn't copy",
          variant: "destructive",
        }),
      ),
    )
    expect(screen.queryByRole("button", { name: "Copied" })).toBeNull()
  })
})
