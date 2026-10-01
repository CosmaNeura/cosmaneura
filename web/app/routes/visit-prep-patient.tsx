import type { MetaFunction } from "react-router"

import { PatientInterview } from "~/components/visit-prep/PatientInterview"

export const meta: MetaFunction = () => [
  { title: "Prepare for your visit | Cosmaneura" },
  { name: "robots", content: "noindex, nofollow, noarchive" },
  { name: "referrer", content: "no-referrer" },
]

export default function VisitPrepPatientRoute() {
  return <PatientInterview />
}
