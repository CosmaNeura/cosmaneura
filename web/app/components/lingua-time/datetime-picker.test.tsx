import { describe, expect, it, vi } from "vitest"

import { DateTimePicker } from "./datetime-picker"
import { fireEvent, render, screen } from "@testing-library/react"

describe("date picker controlled resets", () => {
  it("clears visible text when a parent resets a previously known birthday", () => {
    const onChange = vi.fn()
    const view = render(
      <DateTimePicker
        aria-label="Birthday"
        value={new Date(2000, 0, 1)}
        onChange={onChange}
        showTimePicker={false}
        suggestions={[]}
      />,
    )
    const input = screen.getByLabelText("Birthday")
    expect(input).not.toHaveValue("")
    fireEvent.focus(input)
    view.rerender(
      <DateTimePicker
        aria-label="Birthday"
        value={undefined}
        onChange={onChange}
        showTimePicker={false}
        suggestions={[]}
      />,
    )
    expect(input).toHaveValue("")
    expect(onChange).not.toHaveBeenCalled()
    fireEvent.blur(input, { relatedTarget: document.body })
    expect(onChange).toHaveBeenCalledWith(undefined)
    expect(input).toHaveValue("")
  })

  it("preserves unfinished text across renders until the user leaves an empty date field", () => {
    const onChange = vi.fn()
    const picker = (
      <DateTimePicker
        aria-label="Birthday"
        value={undefined}
        onChange={onChange}
        showTimePicker={false}
        suggestions={[]}
      />
    )
    const view = render(picker)
    const input = screen.getByLabelText("Birthday")
    fireEvent.change(input, { target: { value: "not a date" } })
    view.rerender(
      <DateTimePicker
        aria-label="Birthday"
        value={undefined}
        onChange={onChange}
        showTimePicker={false}
        suggestions={[]}
        placeholder="Birthday still unknown"
      />,
    )
    expect(input).toHaveValue("not a date")
    fireEvent.blur(input, { relatedTarget: document.body })
    expect(input).toHaveValue("")
    expect(onChange).toHaveBeenCalledWith(undefined)
  })
})
