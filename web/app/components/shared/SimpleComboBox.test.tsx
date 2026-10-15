import { describe, expect, it, vi } from "vitest"

import { SimpleComboBox } from "./SimpleComboBox"
import { fireEvent, render, screen } from "@testing-library/react"

describe("SimpleComboBox", () => {
  it("connects its input to an accessible listbox and selects an option", async () => {
    const onSelectedItemChange = vi.fn()
    render(
      <SimpleComboBox
        aria-label="Clinician"
        items={[
          { label: "Alex Active", value: "alex" },
          { label: "Riley Ready", value: "riley" },
        ]}
        onSelectedItemChange={onSelectedItemChange}
        value={null}
      />,
    )

    const input = screen.getByRole("combobox", { name: "Clinician" })
    fireEvent.click(input)

    const listbox = await screen.findByRole("listbox")
    expect(input).toHaveAttribute("aria-controls", listbox.id)
    fireEvent.click(screen.getByRole("option", { name: "Riley Ready" }))

    expect(onSelectedItemChange).toHaveBeenCalledWith({
      disabled: undefined,
      label: "Riley Ready",
      value: "riley",
    })
  })
})
