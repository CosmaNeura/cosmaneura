import { queryClient } from "~/configuration/tanstack-query"

import { noteListOptions } from "client/@tanstack/react-query.gen"

export function knownAppointmentIds(): Set<string> | null {
  const cached = queryClient.getQueryData(noteListOptions().queryKey)
  return cached ? new Set(cached.appointments.map(({ id }) => id)) : null
}

// Registering an upload creates its appointment synchronously, so the new
// visit is the one ID that was not listed before. Anything ambiguous (no
// prior list, several new visits, a failed read) yields null rather than a
// guess.
export async function findUploadedAppointmentId(
  before: Set<string> | null,
): Promise<string | null> {
  if (!before) return null
  try {
    const after = await queryClient.fetchQuery({
      ...noteListOptions(),
      staleTime: 0,
    })
    const added = after.appointments
      .map(({ id }) => id)
      .filter((id) => !before.has(id))
    return added.length === 1 ? added[0] : null
  } catch {
    return null
  }
}
