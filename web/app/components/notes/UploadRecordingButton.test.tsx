import { format, parseISO } from "date-fns"
import { afterEach, describe, expect, it, vi } from "vitest"

import { appointmentDateFormat } from "~/lib/date"

import { UploadRecordingButton } from "./UploadRecordingButton"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"

const { upload } = vi.hoisted(() => ({ upload: vi.fn() }))
vi.mock("~/lib/visit-audio-upload", () => ({ submitVisitAudioUpload: upload }))
vi.mock("~/configuration/client", () => ({ client: {} }))
vi.mock("client/@tanstack/react-query.gen", () => ({
  patientListOptions: () => ({}),
}))
const patientQuery = vi.hoisted(() => ({ loading: false }))
vi.mock("@tanstack/react-query", () => ({
  useQuery: () => ({
    data: patientQuery.loading
      ? undefined
      : [
          {
            id: "patient_synthetic",
            name: "Synthetic date check",
            date_of_birth: "2000-01-01T00:00:00Z",
          },
          {
            id: "patient_unknown_dob",
            name: "Synthetic unknown date",
            date_of_birth: null,
          },
          {
            id: "patient_stored_with_space",
            name: "Synthetic spaced name ",
            date_of_birth: "1990-05-06T00:00:00Z",
          },
        ],
  }),
}))
vi.mock("~/hooks/use-toast", () => ({ useToast: () => ({ toast: vi.fn() }) }))
vi.mock("~/utils/terminology", () => ({
  useTerminology: () => ({
    patient: "client",
    patientTitle: "Client",
    patientPlural: "clients",
    patientPossessive: "client’s",
  }),
}))
// Keep the actual form, validation, selection handler, and upload submission.
// Date picker presentation uses the same local-calendar format as the real UI.
vi.mock("~/components/lingua-time/datetime-picker", () => ({
  DateTimePicker: ({
    id,
    value,
    onChange,
    "aria-invalid": invalid,
  }: {
    id: string
    value?: Date
    onChange: (value: Date | undefined) => void
    "aria-invalid"?: boolean
  }) => (
    <input
      id={id}
      aria-invalid={invalid}
      value={value ? format(value, "yyyy-MM-dd") : ""}
      onChange={(event) =>
        onChange(event.target.value ? parseISO(event.target.value) : undefined)
      }
    />
  ),
}))
vi.mock("~/components/shared/SimpleComboBox", () => ({
  SimpleComboBox: ({
    items,
    onSelectedItemChange,
    renderItem,
  }: {
    items: { value: string; label: string }[]
    onSelectedItemChange: (item: { value: string; label: string }) => void
    renderItem: (item: { value: string; label: string }) => React.ReactNode
  }) => (
    <>
      <button
        type="button"
        onClick={() =>
          onSelectedItemChange({ value: "new_item", label: "  Typed person  " })
        }
      >
        Type a new name
      </button>
      {items.map((item) => (
        <button
          key={item.value}
          type="button"
          onClick={() => onSelectedItemChange(item)}
        >
          {renderItem(item)}
        </button>
      ))}
    </>
  ),
}))
vi.mock("~/components/shared/AudioVideoDropzone", () => ({
  AudioVideoDropzone: ({
    onDropFile,
  }: {
    onDropFile: (file: File) => void
  }) => (
    <button
      type="button"
      onClick={() =>
        onDropFile(
          new File(["fictional"], "synthetic.wav", { type: "audio/wav" }),
        )
      }
    >
      Choose synthetic audio
    </button>
  ),
}))

afterEach(() => {
  vi.useRealTimers()
  upload.mockReset()
  patientQuery.loading = false
})

describe("upload form guidance", () => {
  it("explains each missing field instead of submitting", async () => {
    render(<UploadRecordingButton />)
    fireEvent.click(screen.getByRole("button", { name: "Upload Recording" }))

    fireEvent.click(screen.getByRole("button", { name: "Upload" }))

    expect(await screen.findByText("Enter the client’s name")).toBeVisible()
    expect(screen.getByText("Enter a date of birth")).toBeVisible()
    expect(screen.getByText("Choose a recording to upload")).toBeVisible()
    expect(upload).not.toHaveBeenCalled()
  })

  it("clears the file error as soon as a recording is chosen", async () => {
    render(<UploadRecordingButton />)
    fireEvent.click(screen.getByRole("button", { name: "Upload Recording" }))
    fireEvent.click(screen.getByRole("button", { name: "Upload" }))
    await screen.findByText("Choose a recording to upload")

    fireEvent.click(
      screen.getByRole("button", { name: "Choose synthetic audio" }),
    )

    await waitFor(() =>
      expect(screen.queryByText("Choose a recording to upload")).toBeNull(),
    )
  })

  it("uses the doctor's terminology in the date of birth help", () => {
    render(<UploadRecordingButton />)
    fireEvent.click(screen.getByRole("button", { name: "Upload Recording" }))

    expect(
      screen.getByText(/It is used to identify this client in the future/),
    ).toBeInTheDocument()
  })

  it("shows a placeholder input while the list loads", () => {
    patientQuery.loading = true
    render(<UploadRecordingButton />)
    fireEvent.click(screen.getByRole("button", { name: "Upload Recording" }))

    expect(
      screen.getByRole("status", { name: "Loading clients" }),
    ).toBeInTheDocument()
    expect(screen.queryByText("Loading...")).toBeNull()
  })

  it("keeps a selected name exactly as stored so the patient is matched", async () => {
    upload.mockResolvedValue({ ok: true })
    render(<UploadRecordingButton />)
    fireEvent.click(screen.getByRole("button", { name: "Upload Recording" }))
    fireEvent.click(
      screen.getByRole("button", { name: /Synthetic spaced name/ }),
    )
    fireEvent.click(
      screen.getByRole("button", { name: "Choose synthetic audio" }),
    )
    fireEvent.click(screen.getByRole("button", { name: "Upload" }))

    await waitFor(() => expect(upload).toHaveBeenCalledOnce())
    expect(upload.mock.calls[0][0].patientName).toBe("Synthetic spaced name ")
  })

  it("trims a newly typed name", async () => {
    upload.mockResolvedValue({ ok: true })
    render(<UploadRecordingButton />)
    fireEvent.click(screen.getByRole("button", { name: "Upload Recording" }))
    fireEvent.click(screen.getByRole("button", { name: "Type a new name" }))
    fireEvent.change(screen.getByLabelText("Date of Birth"), {
      target: { value: "1995-02-03" },
    })
    fireEvent.click(
      screen.getByRole("button", { name: "Choose synthetic audio" }),
    )
    fireEvent.click(screen.getByRole("button", { name: "Upload" }))

    await waitFor(() => expect(upload).toHaveBeenCalledOnce())
    expect(upload.mock.calls[0][0].patientName).toBe("Typed person")
  })

  it("does not render the trigger hidden at any breakpoint", () => {
    render(<UploadRecordingButton />)
    const trigger = screen.getByRole("button", { name: "Upload Recording" })
    expect(trigger.className).not.toMatch(/(^|\s)hidden(\s|$)/)
  })
})

describe("date-only recording upload", () => {
  it("clears the previous birthday for a patient with no date and still requires an entered date for legacy upload", async () => {
    upload.mockResolvedValue({ ok: true })
    render(<UploadRecordingButton />)
    fireEvent.click(screen.getByRole("button", { name: "Upload Recording" }))
    fireEvent.click(
      screen.getByRole("button", { name: /Synthetic date check/ }),
    )
    expect(screen.getByLabelText("Date of Birth")).toHaveValue("2000-01-01")
    const unknown = screen.getByRole("button", {
      name: /Synthetic unknown date/,
    })
    expect(unknown).toHaveTextContent("Date of birth not provided")
    fireEvent.click(unknown)
    expect(screen.getByLabelText("Date of Birth")).toHaveValue("")
    fireEvent.click(
      screen.getByRole("button", { name: "Choose synthetic audio" }),
    )
    fireEvent.click(screen.getByRole("button", { name: "Upload" }))
    await waitFor(() =>
      expect(screen.getByLabelText("Date of Birth")).toHaveAttribute(
        "aria-invalid",
        "true",
      ),
    )
    expect(upload).not.toHaveBeenCalled()
    fireEvent.change(screen.getByLabelText("Date of Birth"), {
      target: { value: "1995-02-03" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Upload" }))
    await waitFor(() => expect(upload).toHaveBeenCalledOnce())
    expect(upload).toHaveBeenCalledWith(
      expect.objectContaining({
        patientName: "Synthetic unknown date",
        dateOfBirth: "1995-02-03T00:00:00",
      }),
    )
  })

  it.each([
    "2026-09-25T00:45:00Z",
    "2026-09-24T23:45:00Z",
    "2026-03-08T07:30:00Z",
  ])("submits the displayed day and existing birthday at %s", async (now) => {
    vi.useFakeTimers({ toFake: ["Date"] })
    vi.setSystemTime(new Date(now))
    upload.mockResolvedValue({ ok: true })
    render(<UploadRecordingButton />)
    fireEvent.click(screen.getByRole("button", { name: "Upload Recording" }))
    const displayedDay = format(new Date(), "yyyy-MM-dd")
    expect(screen.getByLabelText("Appointment Date")).toHaveValue(displayedDay)
    // A birthday is a calendar day even if the API representation carries Z.
    expect(
      screen.getByRole("button", { name: /Synthetic date check/ }),
    ).toHaveTextContent("1/1/00")
    fireEvent.click(
      screen.getByRole("button", { name: /Synthetic date check/ }),
    )
    expect(screen.getByLabelText("Date of Birth")).toHaveValue("2000-01-01")
    fireEvent.click(
      screen.getByRole("button", { name: "Choose synthetic audio" }),
    )
    fireEvent.click(screen.getByRole("button", { name: "Upload" }))
    await waitFor(() => expect(upload).toHaveBeenCalledOnce())
    expect(upload).toHaveBeenCalledWith(
      expect.objectContaining({
        appointmentDate: new Date(now).toISOString(),
        dateOfBirth: "2000-01-01T00:00:00",
        patientName: "Synthetic date check",
      }),
    )
    // The API emits the stored UTC instant without an offset. Its display must
    // round-trip to the calendar day seen by the provider, not the UTC day.
    const persistedDate = upload.mock.calls[0][0].appointmentDate.slice(0, -1)
    expect(appointmentDateFormat(persistedDate)).toBe(
      format(new Date(now), "M/d/yy"),
    )
  })

  it("retains the order of morning and afternoon uploads on the same day", async () => {
    vi.useFakeTimers({ toFake: ["Date"] })
    upload.mockResolvedValue({ ok: true })
    for (const hour of [9, 15]) {
      vi.setSystemTime(new Date(2026, 8, 24, hour, 30))
      const view = render(<UploadRecordingButton />)
      fireEvent.click(screen.getByRole("button", { name: "Upload Recording" }))
      fireEvent.click(
        screen.getByRole("button", { name: /Synthetic date check/ }),
      )
      fireEvent.click(
        screen.getByRole("button", { name: "Choose synthetic audio" }),
      )
      fireEvent.click(screen.getByRole("button", { name: "Upload" }))
      await waitFor(() =>
        expect(upload).toHaveBeenCalledTimes(hour === 9 ? 1 : 2),
      )
      view.unmount()
    }
    const [morning, afternoon] = upload.mock.calls.map(
      ([value]) => value.appointmentDate,
    )
    expect(Date.parse(afternoon) - Date.parse(morning)).toBe(6 * 60 * 60 * 1000)
    expect(appointmentDateFormat(morning.slice(0, -1))).toBe("9/24/26")
    expect(appointmentDateFormat(afternoon.slice(0, -1))).toBe("9/24/26")
  })
})
