import { format } from "date-fns"
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
vi.mock("@tanstack/react-query", () => ({
  useQuery: () => ({
    data: [
      {
        id: "patient_synthetic",
        name: "Synthetic date check",
        date_of_birth: "2000-01-01T00:00:00Z",
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
  DateTimePicker: ({ id, value }: { id: string; value?: Date }) => (
    <input id={id} readOnly value={value ? format(value, "yyyy-MM-dd") : ""} />
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
    <button type="button" onClick={() => onSelectedItemChange(items[0])}>
      {renderItem(items[0])}
    </button>
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
})

describe("date-only recording upload", () => {
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
