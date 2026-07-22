// The single source of provider/patient role labels ("terminology") for the
// app. Behavioral-health providers say therapist/client, not doctor/patient —
// the label pair is a per-doctor setting stored on the backend
// (Doctor.terminology) and every user-visible role label should come from
// this catalog, never from a hardcoded string. Adding a new label pair means
// adding one entry here and one enum member in app/models/doctor.py.
import type { Terminology } from "~/configuration/client"
import { SETTINGS_STALE_TIME } from "~/configuration/tanstack-query"

import { useQuery } from "@tanstack/react-query"
import { settingsDataOptions } from "client/@tanstack/react-query.gen"

export interface LabelSet {
  /** "doctor" */
  provider: string
  /** "Doctor" */
  providerTitle: string
  /** "patient" */
  patient: string
  /** "Patient" */
  patientTitle: string
  /** "patients" */
  patientPlural: string
  /** "patient's" */
  patientPossessive: string
  /** "Doctor / Patient" — shown in the settings picker */
  displayName: string
}

export const TERMINOLOGY_SETS: Record<Terminology, LabelSet> = {
  doctor_patient: {
    provider: "doctor",
    providerTitle: "Doctor",
    patient: "patient",
    patientTitle: "Patient",
    patientPlural: "patients",
    patientPossessive: "patient's",
    displayName: "Doctor / Patient",
  },
  therapist_client: {
    provider: "therapist",
    providerTitle: "Therapist",
    patient: "client",
    patientTitle: "Client",
    patientPlural: "clients",
    patientPossessive: "client's",
    displayName: "Therapist / Client",
  },
  counselor_client: {
    provider: "counselor",
    providerTitle: "Counselor",
    patient: "client",
    patientTitle: "Client",
    patientPlural: "clients",
    patientPossessive: "client's",
    displayName: "Counselor / Client",
  },
}

export const DEFAULT_TERMINOLOGY: Terminology = "doctor_patient"

export function getTerminology(key: Terminology | null | undefined): LabelSet {
  return TERMINOLOGY_SETS[key ?? DEFAULT_TERMINOLOGY]
}

// Reads the signed-in doctor's terminology from the settings cache (the
// settings query is shared app-wide and deduped by SETTINGS_STALE_TIME).
// Falls back to doctor/patient until settings load.
export function useTerminology(): LabelSet {
  const { data } = useQuery({
    ...settingsDataOptions(),
    staleTime: SETTINGS_STALE_TIME,
  })

  return getTerminology(data?.terminology)
}
