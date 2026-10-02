import { Button } from "@/components/ui/button"
import type { CabSource } from "~/lib/cab-provider-header"

const visitLabels: Record<CabSource["visitContext"]["encounterType"], string> =
  {
    office_new: "Office · New patient",
    office_established: "Office · Established patient",
    telehealth_new: "Telehealth · New patient",
    telehealth_established: "Telehealth · Established patient",
  }
const sexLabels = { F: "Female", M: "Male", other: "Other", unknown: "Unknown" }
const placeLabels = {
  "11": "Office (11)",
  "10": "Patient at home (10)",
  "02": "Patient elsewhere (02)",
}

export function CabVisitContext({
  source,
  disabled,
  onUpdate,
}: {
  source: CabSource
  disabled: boolean
  onUpdate: (visit: CabSource["visitContext"]) => void
}) {
  const visit = source.visitContext
  return (
    <section
      aria-label="Confirmed visit details"
      className="rounded-md border p-3 text-sm"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="font-medium">Confirmed visit details</h3>
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={disabled}
          onClick={() => onUpdate(visit)}
        >
          Update visit details
        </Button>
      </div>
      <dl className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
        {[
          ["Service date", visit.serviceDate || "Not specified"],
          ["Visit setting", visitLabels[visit.encounterType]],
          ["Place of service", placeLabels[visit.placeOfService]],
          ["Provider specialty", visit.providerSpecialty],
          ["Age at visit", String(visit.patientAge)],
          ["Documented sex", sexLabels[visit.patientSex]],
        ].map(([label, value]) => (
          <div key={label}>
            <dt className="text-muted-foreground text-xs">{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
    </section>
  )
}
