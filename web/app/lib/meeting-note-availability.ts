import { loadNoteDetail } from "~/lib/note-detail-load"

export type MeetingNoteAvailability = "ready" | "missing" | "unavailable"

/** A visit link and an ended meeting do not establish that a note exists. */
export async function loadMeetingNoteAvailability(
  appointmentId: string,
  request: Request,
): Promise<MeetingNoteAvailability> {
  try {
    const detail = await loadNoteDetail(appointmentId, request)
    request.signal.throwIfAborted()
    if (
      detail.appointment?.id !== appointmentId ||
      (detail.notes !== null && !Array.isArray(detail.notes)) ||
      detail.notes?.some(
        (note) =>
          note.appointment_id !== appointmentId ||
          note.doctor_id !== detail.appointment.doctor_id,
      )
    ) {
      return "unavailable"
    }
    return detail.notes?.length ? "ready" : "missing"
  } catch (error) {
    // Only the existing loader's unavailable/temporary read failures become
    // status copy. Cancellation, session and billing/practice recovery escape.
    if (request.signal.aborted) throw error
    if (error && typeof error === "object" && "data" in error) {
      const failure = error.data
      if (
        failure &&
        typeof failure === "object" &&
        "noteLoadFailure" in failure &&
        (failure.noteLoadFailure === "temporary" ||
          failure.noteLoadFailure === "unavailable")
      ) {
        return "unavailable"
      }
    }
    throw error
  }
}
