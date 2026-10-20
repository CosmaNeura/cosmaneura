import { type Appointment, noteList } from "~/configuration/client"
import { queryClient } from "~/configuration/tanstack-query"
import type { NoteStatuses } from "~/lib/appointment"
import {
  type PracticeAgreementBlock,
  PracticeAgreementError,
  practiceAgreementBlockFromClientResult,
  practiceAgreementBlockFromRouteFailure,
} from "~/lib/practice-gate-error"

import { invariant } from "@epic-web/invariant"
import { noteListOptions } from "client/@tanstack/react-query.gen"

export type NotesLayoutData = {
  appointments: Appointment[]
  noteStatuses: NoteStatuses
  blocked: PracticeAgreementBlock | null
}

function blockedNotesData(blocked: PracticeAgreementBlock): NotesLayoutData {
  return { appointments: [], noteStatuses: {}, blocked }
}

export async function loadNotesLayoutData(): Promise<NotesLayoutData> {
  try {
    const result = await queryClient.fetchQuery({
      ...noteListOptions(),
      // Recheck access on navigation while sharing any request already in flight.
      staleTime: 0,
      retry: false,
      queryFn: async ({ signal }) => {
        const result = await noteList({ signal, throwOnError: false })
        const blocked = practiceAgreementBlockFromClientResult(result)
        if (blocked) throw new PracticeAgreementError(blocked)
        invariant(result.data, "data failed to load")
        return result.data
      },
    })
    return {
      appointments: result.appointments.map((visit) => ({
        ...visit,
        patient_name: result.patient_names?.[visit.id] ?? visit.patient_name,
      })),
      noteStatuses: result.note_statuses ?? {},
      blocked: null,
    }
  } catch (error) {
    const blocked = practiceAgreementBlockFromRouteFailure(error)
    if (blocked) {
      queryClient.removeQueries({
        queryKey: noteListOptions().queryKey,
        exact: true,
      })
      return blockedNotesData(blocked)
    }
    throw error
  }
}
