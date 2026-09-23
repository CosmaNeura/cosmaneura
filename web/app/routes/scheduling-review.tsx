import { useEffect, useState } from "react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { SidebarInset } from "@/components/ui/sidebar"
import { Icons } from "~/components/icons"
import {
  type AmbiguousPatientMatchResponse,
  type Patient,
  patientList,
  schedulingReviewConfirm,
  schedulingReviewDismiss,
  schedulingReviewList,
} from "~/configuration/client"
import { toast } from "~/hooks/use-toast"

export const meta = () => {
  return [
    { title: "Scheduling Review" },
    {
      name: "description",
      content: "Confirm patients synced in from your connected calendar",
    },
  ]
}

function ReviewRow({
  match,
  patients,
  onResolved,
}: {
  match: AmbiguousPatientMatchResponse
  patients: Patient[]
  onResolved: (id: string) => void
}) {
  const [selectedPatientId, setSelectedPatientId] = useState<string>("")
  const [newPatientName, setNewPatientName] = useState("")
  const [newPatientDob, setNewPatientDob] = useState("")
  const [submitting, setSubmitting] = useState(false)

  async function handleConfirm() {
    setSubmitting(true)
    const { error } = await schedulingReviewConfirm({
      path: { match_id: match.id },
      body: selectedPatientId
        ? { patient_id: selectedPatientId }
        : {
            new_patient_name: newPatientName,
            new_patient_date_of_birth: newPatientDob,
          },
    })
    setSubmitting(false)
    if (error) {
      toast({
        title: "Failed to confirm. Please try again.",
        variant: "destructive",
      })
      return
    }
    toast({ title: "Confirmed" })
    onResolved(match.id)
  }

  async function handleDismiss() {
    setSubmitting(true)
    const { error } = await schedulingReviewDismiss({
      path: { match_id: match.id },
    })
    setSubmitting(false)
    if (error) {
      toast({
        title: "Failed to dismiss. Please try again.",
        variant: "destructive",
      })
      return
    }
    onResolved(match.id)
  }

  const canConfirm = Boolean(
    selectedPatientId || (newPatientName.trim() && newPatientDob),
  )

  return (
    <div className="space-y-3 rounded-md border p-4">
      <div>
        <div className="font-medium">
          {match.candidate_patient_name || "Unknown name"}
        </div>
        <div className="text-muted-foreground text-sm">
          {match.candidate_patient_email || match.candidate_patient_phone || (
            <span className="italic">No contact info from {match.source}</span>
          )}
        </div>
        <div className="text-muted-foreground text-sm">
          Appointment:{" "}
          {new Date(match.candidate_appointment_start_at).toLocaleString()}
        </div>
      </div>

      <div className="space-y-2">
        <Label>Existing patient</Label>
        <Select
          value={selectedPatientId}
          onValueChange={(value) => {
            setSelectedPatientId(value)
            if (value) {
              setNewPatientName("")
              setNewPatientDob("")
            }
          }}
        >
          <SelectTrigger>
            <SelectValue placeholder="Select a patient" />
          </SelectTrigger>
          <SelectContent>
            {patients.map((patient) => (
              <SelectItem key={patient.id} value={patient.id}>
                {patient.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <div className="text-muted-foreground text-center text-xs">or</div>

      <div className="grid grid-cols-2 gap-2">
        <div className="space-y-2">
          <Label>New patient name</Label>
          <Input
            value={newPatientName}
            disabled={Boolean(selectedPatientId)}
            onChange={(e) => setNewPatientName(e.target.value)}
          />
        </div>
        <div className="space-y-2">
          <Label>Date of birth</Label>
          <Input
            type="date"
            value={newPatientDob}
            disabled={Boolean(selectedPatientId)}
            onChange={(e) => setNewPatientDob(e.target.value)}
          />
        </div>
      </div>

      <div className="flex justify-end gap-2">
        <Button variant="outline" onClick={handleDismiss} disabled={submitting}>
          Dismiss
        </Button>
        <Button onClick={handleConfirm} disabled={submitting || !canConfirm}>
          {submitting ? (
            <Icons.spinner className="h-4 w-4 animate-spin" />
          ) : (
            "Confirm"
          )}
        </Button>
      </div>
    </div>
  )
}

export default function SchedulingReviewPage() {
  const [matches, setMatches] = useState<
    AmbiguousPatientMatchResponse[] | null
  >(null)
  const [patients, setPatients] = useState<Patient[]>([])

  useEffect(() => {
    schedulingReviewList().then(({ data }) => setMatches(data ?? []))
    patientList().then(({ data }) => setPatients(data ?? []))
  }, [])

  function handleResolved(id: string) {
    setMatches((current) => current?.filter((m) => m.id !== id) ?? null)
  }

  return (
    <SidebarInset>
      <header className="bg-background sticky top-0 flex shrink-0 items-center gap-2 border-b p-4">
        <h1 className="text-lg font-semibold">Scheduling Review</h1>
      </header>
      <div className="mx-auto max-w-2xl space-y-4 p-6">
        <p className="text-muted-foreground text-sm">
          These appointments synced in from a connected calendar but couldn't be
          matched to an existing patient automatically. Confirm who each one
          belongs to, or dismiss it.
        </p>
        {matches === null ? (
          <div className="text-muted-foreground text-sm">Loading...</div>
        ) : matches.length === 0 ? (
          <div className="text-muted-foreground text-sm">
            Nothing to review right now.
          </div>
        ) : (
          matches.map((match) => (
            <ReviewRow
              key={match.id}
              match={match}
              patients={patients}
              onResolved={handleResolved}
            />
          ))
        )}
      </div>
    </SidebarInset>
  )
}
