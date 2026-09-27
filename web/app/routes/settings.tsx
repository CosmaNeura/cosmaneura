import { useCallback, useEffect, useState } from "react"
import { useForm } from "react-hook-form"
import { MetaFunction, useRevalidator, useSearchParams } from "react-router"
import { z } from "zod"

import { ChevronDown, Upload } from "lucide-react"
import pluralize from "pluralize"

import type { Route } from "./+types/settings"

import { AppSidebar } from "@/components/app-sidebar"
import { Button } from "@/components/ui/button"
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar"
import { LabRangeSettings } from "~/components/LabRangeSettings"
import { NoteCustomizationForm } from "~/components/NoteCustomizationForm"
import { TerminologySettings } from "~/components/TerminologySettings"
import { Icons } from "~/components/icons"
import { BillingReturnNotice } from "~/components/shared/BillingReturnNotice"
import { ImageDropzone } from "~/components/shared/ImageDropzone"
import { InnerScrollableContent } from "~/components/shared/InnerScrollableContent"
import { PracticeJoinPanel } from "~/components/shared/PracticeJoinPanel"
import { PracticeRoster } from "~/components/shared/PracticeRoster"
import { SimpleComboBox } from "~/components/shared/SimpleComboBox"
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "~/components/ui/form"
import {
  type PracticeRosterMember,
  practiceRoster,
  settingsData,
  updateSettingsData,
  zoomAuthUrl,
  zoomDisconnect,
} from "~/configuration/client"
import { queryClient } from "~/configuration/tanstack-query"
import { toast } from "~/hooks/use-toast"
import { BAA_PDF_URL } from "~/lib/baa"
import {
  restrictSettingsToBillingRecovery,
  safeBillingReturnTo,
} from "~/lib/billing-recovery"
import {
  exampleNoteUploadErrorCopy,
  submitExampleNote,
} from "~/lib/example-note-upload"
import {
  type BaaAcceptanceRecord,
  type PracticeStatus,
  getBaaAcceptances,
  getPractice,
} from "~/lib/practice-api"
import {
  isReferenceLabsEnabled,
  isReferenceToolkitEnabled,
} from "~/utils/reference-toolkit-feature"
import { useTerminology } from "~/utils/terminology"
import { isTerminologyEnabled } from "~/utils/terminology-feature"
import { isZoomEnabled } from "~/utils/zoom-feature"

import { useUser } from "@clerk/clerk-react"
import { invariant } from "@epic-web/invariant"
import { zodResolver } from "@hookform/resolvers/zod"
import { settingsDataOptions } from "client/@tanstack/react-query.gen"

type NoteSchema = Route.ComponentProps["loaderData"]["notes_schema"][number]

function PracticeSettings({
  practice,
  roster,
  acceptances,
  onPracticeChange,
  billingReturnTo,
}: {
  practice: PracticeStatus | null
  roster: PracticeRosterMember[] | null
  acceptances: BaaAcceptanceRecord[]
  onPracticeChange: (practice: PracticeStatus) => void
  billingReturnTo: string
}) {
  if (!practice) return null
  return (
    <section className="prose-headings prose w-1/2 space-y-4">
      <BillingReturnNotice
        practice={practice}
        onPracticeChange={onPracticeChange}
      />
      <h3>Practice</h3>
      <PracticeJoinPanel
        practice={practice}
        showBillingActions
        billingReturnTo={billingReturnTo}
      />
      <PracticeRoster practice={practice} members={roster} />
      {acceptances.length > 0 ? (
        <div className="not-prose rounded-lg border p-4 text-sm">
          <p className="font-semibold">Legal / Agreements</p>
          <ul className="mt-2 space-y-1">
            {acceptances.map((record) => (
              <li
                key={record.accepted_at_utc}
                className="text-muted-foreground"
              >
                {record.doc_id} v{record.version} — accepted by{" "}
                {record.accepted_by_name} ({record.accepted_by_title}) on{" "}
                {new Date(record.accepted_at_utc).toLocaleString()}
              </li>
            ))}
          </ul>
          <p className="text-muted-foreground mt-2 text-xs">
            <a
              href={BAA_PDF_URL}
              target="_blank"
              rel="noreferrer"
              className="underline underline-offset-2"
            >
              Download the agreement (PDF)
            </a>
          </p>
        </div>
      ) : null}
    </section>
  )
}

function BiographyInput({ biography }: { biography: string | null }) {
  const formSchema = z.object({
    biography: z.string().min(1, "Biography is required"),
  })

  const form = useForm({
    resolver: zodResolver(formSchema),
    defaultValues: {
      biography: biography || "",
    },
  })

  async function onSubmit(values: z.infer<typeof formSchema>) {
    await updateSettingsData({
      body: values,
    })

    toast({
      title: "Biography Updated",
      description: "Your biography has been updated successfully.",
    })
  }

  return (
    <section className="prose-headings prose w-1/2 space-y-4">
      <h3>Biography</h3>
      <p className="text-muted-foreground text-sm">
        This is the information that will be used to personalize your
        experience, including intake. You can change this at any time.
      </p>
      <Form {...form}>
        <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
          <FormField
            control={form.control}
            name="biography"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Biography</FormLabel>
                <FormControl>
                  <textarea
                    className="w-full rounded-md border p-2"
                    rows={5}
                    placeholder="Tell us about yourself. For example... I provide comprehensive dermatologic care for patients of all ages, focusing on medical, surgical, and cosmetic dermatology. I have a special interest in the early detection and treatment of skin cancer, as well as the management of complex inflammatory skin conditions such as eczema and psoriasis."
                    {...field}
                  />
                </FormControl>
                <FormDescription>
                  Share information about your medical practice, expertise, and
                  approach.
                </FormDescription>
                <FormMessage />
              </FormItem>
            )}
          />
          <Button type="submit">
            {form.formState.isSubmitting ? (
              <>
                <Icons.spinner className="mr-2 h-4 w-4 animate-spin" />
                Saving...
              </>
            ) : (
              "Save Biography"
            )}
          </Button>
        </form>
      </Form>
    </section>
  )
}

function NoteSchemaDisplay({ noteSchemas }: { noteSchemas: NoteSchema[] }) {
  const [expandedIds, setExpandedIds] = useState<string[]>([])

  const toggleExpand = (id: string) => {
    setExpandedIds((prev) =>
      prev.includes(id)
        ? prev.filter((existingId) => existingId !== id)
        : [...prev, id],
    )
  }

  return (
    <section className="prose-headings prose w-1/2 space-y-4">
      <h3>Note Types</h3>
      {noteSchemas.length > 0 ? (
        <p>Below are the notes you've uploaded.</p>
      ) : (
        <p>Once you upload note types, they'll appear here.</p>
      )}
      {noteSchemas.map((schema) => (
        <div key={schema.id} className="flex flex-col rounded-lg border p-4">
          <div
            className="flex cursor-pointer items-center gap-4"
            onClick={() => toggleExpand(schema.id)}
          >
            <div className="font-medium">{schema.note_type}</div>
            <div className="text-sm">
              {schema.example_count}{" "}
              {pluralize("example", schema.example_count)}
            </div>
            <div className="text-muted-foreground ml-auto flex items-center gap-2 text-sm">
              <ChevronDown
                className={`h-4 w-4 transform transition-transform ${
                  expandedIds.includes(schema.id) ? "rotate-180" : ""
                }`}
              />
            </div>
          </div>
          {expandedIds.includes(schema.id) && (
            <>
              <p className="text-sm">
                Here's the structure we've extracted from your note:
              </p>
              <div className="mt-4 border-l-2 pl-4">
                <p className="text-muted-foreground text-sm">
                  {Object.entries(schema.fields).map(([field, description]) => (
                    <div key={field} className="mb-2">
                      <span className="font-semibold">{field}:</span>{" "}
                      {String(description)}
                    </div>
                  ))}
                </p>
              </div>
            </>
          )}
        </div>
      ))}{" "}
    </section>
  )
}

function NoteCustomization({
  noteSchemas,
  customizations,
}: {
  noteSchemas: Route.ComponentProps["loaderData"]["notes_schema"]
  customizations: Route.ComponentProps["loaderData"]["note_customizations"]
}) {
  if (noteSchemas.length === 0) return null

  const bySchema = new Map(
    customizations.map((c) => [c.doctor_note_schema_id, c]),
  )

  return (
    <section className="prose-headings prose w-1/2 space-y-4">
      <h3>AI Output Customization</h3>
      <p className="text-sm">
        Tune how Cosmas writes each of your note types. Changes affect only the
        note type you edit.
      </p>
      {noteSchemas.map((schema) => {
        const customization = bySchema.get(schema.id)
        if (!customization) return null
        return (
          <NoteCustomizationForm
            key={schema.id}
            schema={schema}
            customization={customization}
          />
        )
      })}
    </section>
  )
}

function NoteUpload({ noteSchemas }: { noteSchemas: NoteSchema[] }) {
  const t = useTerminology()
  const formSchema = z.object({
    note_type: z.string(),
  })

  const [file, setFile] = useState<File | null>(null)
  const form = useForm({
    resolver: zodResolver(formSchema),
  })

  async function onSubmit(values: z.infer<typeof formSchema>) {
    invariant(
      file,
      "file should be required, submit should be impossible without a valid file",
    )

    const uploadResult = await submitExampleNote(file, values.note_type)

    if (!uploadResult.ok) {
      toast({
        ...exampleNoteUploadErrorCopy(uploadResult.stage),
        variant: "destructive",
      })
      return
    }

    setFile(null)

    toast({
      title: "Upload Complete",
      description:
        "Your example note has been received and will be processed shortly.",
    })
  }

  const PREDEFINED_NOTE_TYPES = [
    "Telehealth SOAP Note",
    "Dermatology Office Visit",
    "Annual Wellness Visit",
    "Psychiatry Follow-up Note",
    "Urgent Care Visit",
    "Post-Op Follow-up",
    "Cardiology Consultation",
    "Pediatric Well Visit",
    "Pain Management Note",
    "Physical Therapy Evaluation",
  ]

  function noteTypeComboBoxItems(noteSchemas: NoteSchema[]) {
    const uploadedLabels = new Set(noteSchemas.map((s) => s.note_type))
    const uploadedItems = noteSchemas.map((schema) => ({
      value: schema.id,
      label: schema.note_type,
    }))
    const predefinedItems = PREDEFINED_NOTE_TYPES.filter(
      (label) => !uploadedLabels.has(label),
    ).map((label) => ({ value: label, label }))
    return [...uploadedItems, ...predefinedItems]
  }

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)} className="flex gap-8">
        <section className="w-1/2 space-y-4">
          <div className="space-y-2">
            <h2 className="text-left text-xl font-semibold">Notetaking</h2>
            <p className="text-muted-foreground text-left text-sm">
              Upload high quality examples of the different types of notes that
              you use. These examples will be used to generate future notes for
              your {t.patientPlural}, so make sure they are a great
              representation of the structure and quality of note that you want
              to see from your personalized AI!
            </p>
            <p className="text-muted-foreground text-left text-sm">
              For example: if you use the telehealth SOAP note, pick a
              high-quality note for that note type and upload a PDF version of
              this note type.
            </p>
          </div>

          <ImageDropzone onDropFile={setFile}>
            {!file && (
              <div className="flex items-center gap-4 font-semibold">
                <div className="bg-muted rounded-full p-3">
                  <Upload className="h-4 w-4" />
                </div>
                <div className="flex flex-col gap-1">
                  <p>Upload Example Visit Note</p>
                  <p className="text-muted-foreground text-xs">
                    Click here or drag to select a PDF (10MB or smaller)
                  </p>
                </div>
              </div>
            )}
            {file && (
              <div className="flex flex-col gap-1 font-semibold">
                <p>{file.name}</p>
                <p className="text-muted-foreground text-xs">
                  Press the upload button below to add the example {t.patient}{" "}
                  note
                </p>
              </div>
            )}
          </ImageDropzone>

          <FormField
            control={form.control}
            name="note_type"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Note Type</FormLabel>
                <FormControl>
                  <SimpleComboBox
                    items={noteTypeComboBoxItems(noteSchemas)}
                    onSelectedItemChange={(item) =>
                      item && field.onChange(item.label)
                    }
                    {...field}
                  />
                </FormControl>
                <FormDescription>
                  Enter the type of note you want to upload. For example, "SOAP
                  Note". If you've already uploaded a note for this note type,
                  and want to add more examples to improve our system's
                  performance, please select it from the list.
                </FormDescription>
                <FormMessage />
              </FormItem>
            )}
          />

          <Button disabled={!file || form.formState.isSubmitting} type="submit">
            {form.formState.isSubmitting ? (
              <>
                <Icons.spinner className="mr-2 h-4 w-4 animate-spin" />
                Uploading...
              </>
            ) : (
              "Upload Example Visit Note"
            )}
          </Button>
        </section>
        <NoteSchemaDisplay noteSchemas={noteSchemas} />
      </form>
    </Form>
  )
}

function ZoomConnect({ zoomConnected }: { zoomConnected: boolean }) {
  const t = useTerminology()
  const revalidator = useRevalidator()
  const [connecting, setConnecting] = useState(false)
  const [disconnecting, setDisconnecting] = useState(false)
  const { user } = useUser()
  const zoomEnabled = isZoomEnabled(user?.primaryEmailAddress?.emailAddress)

  async function onConnect() {
    setConnecting(true)
    const { data } = await zoomAuthUrl()
    if (data?.url) {
      window.location.href = data.url
    } else {
      toast({
        title: "Failed to start Zoom connection. Please try again.",
        variant: "destructive",
      })
      setConnecting(false)
    }
  }

  async function onDisconnect() {
    setDisconnecting(true)
    const { error } = await zoomDisconnect()
    if (error) {
      toast({
        title: "Failed to disconnect. Please try again.",
        variant: "destructive",
      })
      setDisconnecting(false)
      return
    }
    toast({ title: "Zoom disconnected" })
    setDisconnecting(false)
    revalidator.revalidate()
  }

  if (!zoomEnabled) {
    return (
      <section className="prose-headings prose w-1/2 space-y-4">
        <div className="flex items-center gap-2">
          <h3 className="!mb-0">Zoom Integration</h3>
          <span className="bg-muted text-muted-foreground rounded-full px-2 py-0.5 text-xs font-medium">
            Coming Soon
          </span>
        </div>
        <p className="text-muted-foreground text-sm">
          Zoom integration is coming soon. You'll be able to connect your
          account so Cosmas can automatically join your meetings and generate
          visit notes.
        </p>
      </section>
    )
  }

  return (
    <section className="prose-headings prose w-1/2 space-y-4">
      <h3>Zoom Integration</h3>
      <p className="text-muted-foreground text-sm">
        Connect your Zoom account so Cosmas can automatically join your meetings
        and generate visit notes.
      </p>

      {zoomConnected ? (
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2 text-sm">
            <div className="h-2 w-2 rounded-full bg-green-500" />
            <span>Connected</span>
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={onDisconnect}
            disabled={disconnecting}
          >
            {disconnecting ? (
              <>
                <Icons.spinner className="mr-2 h-4 w-4 animate-spin" />
                Disconnecting...
              </>
            ) : (
              "Disconnect"
            )}
          </Button>
        </div>
      ) : (
        <>
          <div className="text-muted-foreground bg-muted/40 rounded-md border p-3 text-xs leading-relaxed">
            <p className="mb-2">
              By connecting Zoom, you authorize Cosmas to join your scheduled
              Zoom meetings on your behalf to record audio, generate
              transcripts, and produce clinical notes. Audio and transcripts are
              processed by our subprocessors (Recall.ai for meeting capture, and
              Azure/OpenAI for transcription and note generation).
            </p>
            <p className="mb-2">
              <strong>
                {t.patientTitle} consent is your responsibility as the provider.
              </strong>{" "}
              The Cosmas AI Note-Taker bot will appear as a participant and
              announce that the meeting is being recorded; you must still obtain
              and document patient consent according to HIPAA and your state's
              requirements before the visit.
            </p>
            <p>
              See our{" "}
              <a
                href="/privacy-policy"
                target="_blank"
                rel="noopener noreferrer"
                className="underline"
              >
                Privacy Policy
              </a>{" "}
              and{" "}
              <a
                href="/terms-of-service"
                target="_blank"
                rel="noopener noreferrer"
                className="underline"
              >
                Terms of Service
              </a>
              . You can disconnect Zoom at any time from this page.
            </p>
          </div>
          <Button onClick={onConnect} disabled={connecting}>
            {connecting ? (
              <>
                <Icons.spinner className="mr-2 h-4 w-4 animate-spin" />
                Connecting...
              </>
            ) : (
              "Connect Zoom"
            )}
          </Button>
        </>
      )}
    </section>
  )
}

export async function clientLoader(_loaderArgs: Route.ClientLoaderArgs) {
  const { data, error, response } = await settingsData()

  if (!data) {
    throw new Error(
      `Settings failed to load (HTTP ${response?.status}): ${JSON.stringify(error)}`,
    )
  }

  const practice = await getPractice().catch(() => null)
  const baa_acceptances = await getBaaAcceptances().catch(
    () => [] as BaaAcceptanceRecord[],
  )
  const practice_roster =
    practice?.role === "owner"
      ? await practiceRoster()
          .then(({ data }) => data?.members ?? null)
          .catch(() => null)
      : null
  return { ...data, practice, baa_acceptances, practice_roster }
}
export const meta: MetaFunction = () => {
  return [
    { title: "Settings" },
    {
      name: "description",
      content: "Your Account Settings",
    },
  ]
}

function NatMedConnection({ hasSubscription }: { hasSubscription: boolean }) {
  const [enabled, setEnabled] = useState(hasSubscription)
  const [saving, setSaving] = useState(false)
  const revalidator = useRevalidator()

  async function toggle(next: boolean) {
    setEnabled(next)
    setSaving(true)
    const { error } = await updateSettingsData({
      body: { biography: null, has_natmed_subscription: next },
    })
    setSaving(false)
    if (error) {
      setEnabled(!next)
      toast({ title: "Could not update", variant: "destructive" })
      return
    }
    toast({ title: "Saved" })
    revalidator.revalidate()
  }

  return (
    <section className="prose-headings prose w-1/2 space-y-4">
      <h3>NatMed Pro connection</h3>
      <p className="text-muted-foreground text-sm">
        If you have a NatMed Pro (Natural Medicines) subscription, turn this on
        to add its interaction checker and nutrient-depletion lookups as
        one-click links next to each patient&apos;s medications. Cosmas links
        you into your own NatMed session — it never copies or stores their
        content.
      </p>
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={enabled}
          disabled={saving}
          onChange={(e) => toggle(e.target.checked)}
        />
        I have a NatMed Pro subscription
      </label>
    </section>
  )
}

export default function SettingsPage({ loaderData }: Route.ComponentProps) {
  const { user } = useUser()
  const [searchParams] = useSearchParams()
  const billingReturnTo = safeBillingReturnTo(searchParams.get("returnTo"))
  const [practice, setPractice] = useState(loaderData.practice)
  useEffect(() => setPractice(loaderData.practice), [loaderData.practice])
  const billingRecoveryOnly = restrictSettingsToBillingRecovery(
    loaderData.billing_recovery_required,
    practice?.entitled,
  )
  const handlePracticeChange = useCallback((next: PracticeStatus) => {
    setPractice(next)
    void queryClient.invalidateQueries({
      queryKey: settingsDataOptions().queryKey,
      refetchType: "none",
    })
  }, [])
  const referenceToolkitEnabled = isReferenceToolkitEnabled(
    user?.primaryEmailAddress?.emailAddress,
  )
  const referenceLabsEnabled = isReferenceLabsEnabled(
    user?.primaryEmailAddress?.emailAddress,
  )
  const terminologyEnabled = isTerminologyEnabled(
    user?.primaryEmailAddress?.emailAddress,
  )

  return (
    <SidebarProvider
      // eliminate the message sidebar
      style={{ "--sidebar-width": "3em" } as React.CSSProperties}
    >
      <AppSidebar isLoading={false} entries={false} />

      <SidebarInset>
        <header className="bg-background sticky top-0 z-10 flex shrink-0 items-center gap-2 border-b p-4">
          <h1 className="text-lg font-semibold">Settings</h1>
        </header>
        <InnerScrollableContent>
          <PracticeSettings
            practice={practice}
            roster={loaderData.practice_roster}
            acceptances={loaderData.baa_acceptances}
            onPracticeChange={handlePracticeChange}
            billingReturnTo={billingReturnTo}
          />
          {!billingRecoveryOnly ? (
            <>
              <BiographyInput biography={loaderData.biography} />
              {terminologyEnabled && (
                <TerminologySettings
                  terminology={loaderData.terminology}
                  biography={loaderData.biography}
                />
              )}
              <NoteUpload noteSchemas={loaderData.notes_schema} />
              <NoteCustomization
                noteSchemas={loaderData.notes_schema}
                customizations={loaderData.note_customizations}
              />
              <ZoomConnect zoomConnected={loaderData.zoom_connected} />
              {referenceToolkitEnabled && (
                <NatMedConnection
                  hasSubscription={loaderData.has_natmed_subscription}
                />
              )}
              {referenceLabsEnabled && <LabRangeSettings />}
            </>
          ) : null}
        </InnerScrollableContent>
      </SidebarInset>
    </SidebarProvider>
  )
}
