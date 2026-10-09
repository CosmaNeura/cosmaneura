import { loadNotesLayoutData } from "~/layouts/notes"

export const UPLOADED_VISIT_LOOKUP_MS = 5_000

// Registering an upload creates its appointment synchronously, so the new
// visit is the one ID that was not listed before. The lookup reuses the notes
// layout's gated read and is bounded so a slow list never holds up a finished
// upload. Anything ambiguous (no prior list, several new visits, a blocked,
// failed or slow read) yields null rather than a guess.
export async function findUploadedAppointmentId(
  before: ReadonlySet<string> | null,
): Promise<string | null> {
  if (!before) return null
  let timer: ReturnType<typeof setTimeout> | undefined
  try {
    const after = await Promise.race([
      loadNotesLayoutData(),
      new Promise<null>((resolve) => {
        timer = setTimeout(() => resolve(null), UPLOADED_VISIT_LOOKUP_MS)
      }),
    ])
    if (!after || after.blocked) return null
    const added = after.appointments
      .map(({ id }) => id)
      .filter((id) => !before.has(id))
    return added.length === 1 ? added[0] : null
  } catch {
    return null
  } finally {
    clearTimeout(timer)
  }
}
