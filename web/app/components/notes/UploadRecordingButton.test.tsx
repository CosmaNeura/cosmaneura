import { format, parseISO } from "date-fns"
import { RouterProvider, createMemoryRouter } from "react-router"
import { afterEach, describe, expect, it, vi } from "vitest"

import { appointmentDateFormat } from "~/lib/date"

import { UploadRecordingButton } from "./UploadRecordingButton"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"

function renderButton() {
  const router = createMemoryRouter(
    [
      {
        id: "layouts/notes",
        path: "/notes",
        loader: () => ({
          appointments: [{ id: "appointment_existing" }],
          noteStatuses: {},
          blocked: null,
        }),
        children: [
          { index: true, element: <UploadRecordingButton /> },
          { path: ":id", element: <p>Opened uploaded visit</p> },
        ],
      },
    ],
    { initialEntries: ["/notes"] },
  )
  return render(<RouterProvider router={router} />)
}

const { upload } = vi.hoisted(() => ({ upload: vi.fn() }))
const uploadedVisit = vi.hoisted(() => ({
  find: vi.fn(async () => null as string | null),
}))
vi.mock("~/lib/uploaded-visit", () => ({
  findUploadedAppointmentId: uploadedVisit.find,
}))
vi.mock("~/lib/visit-audio-upload", () => ({ submitVisitAudioUpload: upload }))
vi.mock("~/configuration/client", () => ({ client: {} }))
vi.mock("client/@tanstack/react-query.gen", () => ({
  patientListOptions: () => ({}),
}))
vi.mock("@tanstack/react-query", () => ({
  useQuery: () => ({
    data: [
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
    ],
  }),
}))
vi.mock("~/hooks/use-toast", () => ({ useToast: () => ({ toast: vi.fn() }) }))
vi.mock("~/utils/terminology", () => ({
  useTerminology: () => ({
    patient: "patient",
    patientTitle: "Patient",
    patientPossessive: "patient’s",
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
  uploadedVisit.find.mockReset()
  uploadedVisit.find.mockResolvedValue(null)
})

describe("date-only recording upload", () => {
  it("clears the previous birthday for a patient with no date and still requires an entered date for legacy upload", async () => {
    upload.mockResolvedValue({ ok: true })
    renderButton()
    fireEvent.click(
      await screen.findByRole("button", { name: "Upload Recording" }),
    )
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
    renderButton()
    fireEvent.click(
      await screen.findByRole("button", { name: "Upload Recording" }),
    )
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
      const view = renderButton()
      fireEvent.click(
        await screen.findByRole("button", { name: "Upload Recording" }),
      )
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

describe("after a successful upload", () => {
  it("opens the uploaded visit when it can be identified", async () => {
    upload.mockResolvedValue({ ok: true })
    uploadedVisit.find.mockResolvedValue("appointment_new")
    renderButton()
    fireEvent.click(
      await screen.findByRole("button", { name: "Upload Recording" }),
    )
    fireEvent.click(
      screen.getByRole("button", { name: /Synthetic date check/ }),
    )
    fireEvent.click(
      screen.getByRole("button", { name: "Choose synthetic audio" }),
    )
    fireEvent.click(screen.getByRole("button", { name: "Upload" }))

    expect(await screen.findByText("Opened uploaded visit")).toBeVisible()
    expect(uploadedVisit.find).toHaveBeenCalledWith(
      new Set(["appointment_existing"]),
    )
  })
})
