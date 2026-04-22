import { useState } from "react"
import { useForm } from "react-hook-form"
import { href, redirect } from "react-router"
import { z } from "zod"

import type { Route } from "./+types/onboarding"

import { Icons } from "~/components/icons"
import { ImageDropzone } from "~/components/shared/ImageDropzone"
import { SimpleComboBox } from "~/components/shared/SimpleComboBox"
import { StepIndicator } from "~/components/shared/StepIndicator"
import { Button } from "~/components/ui/button"
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "~/components/ui/form"
import { getClient } from "~/configuration/clerk"
import {
  completeOnboarding,
  generateSignedExampleNoteUploadUrl,
  settingsData,
  updateSettingsData,
  uploadExampleNote,
} from "~/configuration/client"
import { toast } from "~/hooks/use-toast"
import { uploadFileToAzure } from "~/lib/azure-upload"
import { requireEnv } from "~/utils/environment"

import { invariant } from "@epic-web/invariant"
import { zodResolver } from "@hookform/resolvers/zod"
import { motion } from "motion/react"
import { SiZoom } from "react-icons/si"

const ZOOM_COMING_SOON = false

type Step = 0 | 1 | 2 | 3 | 4

function getInitialStep(data: {
  biography: string | null
  notes_schema: unknown[]
  zoom_connected: boolean
}): Step {
  if (!data.biography) return 0
  if (data.notes_schema.length === 0) return 2
  if (!ZOOM_COMING_SOON && !data.zoom_connected) return 3
  return 4
}

export async function clientLoader(_: Route.ClientLoaderArgs) {
  const { data } = await settingsData()
  invariant(data, "settings data failed to load")

  if (data.onboarding_completed) {
    throw redirect(href("/notes"))
  }

  return data
}

// ─── Step 0: Welcome ────────────────────────────────────────────────────────

function WelcomeStep({ onNext }: { onNext: () => void }) {
  return (
    <div className="flex flex-col items-center gap-6 text-center">
      <h1 className="text-3xl font-bold">Welcome to Cosma</h1>
      <p className="text-muted-foreground max-w-md text-base">
        Cosma is your AI medical scribe. It listens to your patient visits,
        generates notes in your style, and handles patient intake so you can
        focus on care.
      </p>
      <p className="text-muted-foreground max-w-md text-sm">
        To get started, we'll set up three things: your biography, your note
        style, and your Zoom connection.
      </p>
      <Button size="lg" onClick={onNext}>
        Get Started
      </Button>
    </div>
  )
}

// ─── Step 1: Biography ──────────────────────────────────────────────────────

function BiographyStep({
  biography,
  onComplete,
}: {
  biography: string | null
  onComplete: () => void
}) {
  const formSchema = z.object({
    biography: z.string().min(1, "Biography is required"),
  })

  const form = useForm({
    resolver: zodResolver(formSchema),
    defaultValues: { biography: biography ?? "" },
  })

  async function onSubmit(values: z.infer<typeof formSchema>) {
    await updateSettingsData({ body: values })
    onComplete()
  }

  return (
    <div className="flex flex-col items-center gap-6 text-center">
      <div className="space-y-1">
        <h2 className="text-2xl font-semibold">Tell us about yourself</h2>
        <p className="text-muted-foreground text-sm">
          This helps Cosma personalize your patient intake and note generation.
        </p>
      </div>
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
                    placeholder="I provide comprehensive dermatologic care for patients of all ages, focusing on medical, surgical, and cosmetic dermatology..."
                    {...field}
                  />
                </FormControl>
                <FormDescription>
                  Describe your specialty, focus areas, and approach to care.
                </FormDescription>
                <FormMessage />
              </FormItem>
            )}
          />
          <Button
            type="submit"
            className="w-full"
            disabled={form.formState.isSubmitting}
          >
            {form.formState.isSubmitting ? (
              <>
                <Icons.spinner className="mr-2 h-4 w-4 animate-spin" />
                Saving...
              </>
            ) : (
              "Save & Continue"
            )}
          </Button>
        </form>
      </Form>
    </div>
  )
}

// ─── Step 2: Example Notes ──────────────────────────────────────────────────

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

function NotesStep({ onComplete }: { onComplete: () => void }) {
  const formSchema = z.object({ note_type: z.string().min(1) })
  const [file, setFile] = useState<File | null>(null)

  const form = useForm({ resolver: zodResolver(formSchema) })

  async function onSubmit(values: z.infer<typeof formSchema>) {
    invariant(file, "a file must be selected before submitting")

    const { data: sasSignature } = await generateSignedExampleNoteUploadUrl()
    invariant(sasSignature, "SAS signature should exist")

    const { stampedFileName, error: uploadError } = await uploadFileToAzure(
      file,
      sasSignature,
    )

    if (uploadError) {
      toast({
        title: "Failed to upload note. Please try again.",
        variant: "destructive",
      })
      return
    }

    await uploadExampleNote({
      query: { file_name: stampedFileName, note_type: values.note_type },
    })

    setFile(null)
    toast({
      title: "Note uploaded",
      description:
        "Your example note has been received and will be processed shortly.",
    })
    onComplete()
  }

  return (
    <div className="flex flex-col items-center gap-6 text-center">
      <div className="space-y-1">
        <h2 className="text-2xl font-semibold">Upload an example note</h2>
        <p className="text-muted-foreground text-sm">
          Upload a high-quality example of a visit note you've written. Cosma
          uses it to learn your style and generate future notes that match your
          format.
        </p>
      </div>
      <Form {...form}>
        <form
          onSubmit={form.handleSubmit(onSubmit)}
          className="w-full space-y-4"
        >
          <ImageDropzone onDropFile={setFile}>
            {!file ? (
              <div className="flex items-center gap-4 font-semibold">
                <p>Upload Example Visit Note</p>
                <p className="text-muted-foreground text-xs">
                  Click or drag a PDF (under 10MB)
                </p>
              </div>
            ) : (
              <div className="flex flex-col gap-1 font-semibold">
                <p>{file.name}</p>
                <p className="text-muted-foreground text-xs">
                  Press Upload to continue
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
                    items={PREDEFINED_NOTE_TYPES.map((label) => ({
                      value: label,
                      label,
                    }))}
                    onSelectedItemChange={(item) =>
                      item && field.onChange(item.label)
                    }
                    {...field}
                  />
                </FormControl>
                <FormDescription>
                  Select the type of note you're uploading.
                </FormDescription>
                <FormMessage />
              </FormItem>
            )}
          />
          <Button
            type="submit"
            className="w-full"
            disabled={!file || form.formState.isSubmitting}
          >
            {form.formState.isSubmitting ? (
              <>
                <Icons.spinner className="mr-2 h-4 w-4 animate-spin" />
                Uploading...
              </>
            ) : (
              "Upload & Continue"
            )}
          </Button>
        </form>
      </Form>
    </div>
  )
}

// ─── Step 3: Zoom ────────────────────────────────────────────────────────────

function ZoomStep({ onComplete }: { onComplete: () => void }) {
  const [loading, setLoading] = useState(false)

  async function connectZoom() {
    setLoading(true)
    const clerk = await getClient()
    const token = await clerk!.session!.getToken()
    const base = requireEnv("VITE_PYTHON_URL").replace(/\/$/, "")
    const res = await fetch(`${base}/internal/v1/zoom-auth-url`, {
      headers: { Authorization: `Bearer ${token}` },
      credentials: "include",
    })
    const { url } = (await res.json()) as { url: string }
    if (url) window.location.href = url
    setLoading(false)
  }

  if (ZOOM_COMING_SOON) {
    return (
      <div className="flex flex-col items-center gap-6 text-center">
        <div className="space-y-1">
          <div className="flex items-center justify-center gap-2">
            <h2 className="text-2xl font-semibold">Connect Zoom</h2>
            <span className="bg-muted text-muted-foreground rounded-full px-2 py-0.5 text-xs font-medium">
              Coming Soon
            </span>
          </div>
          <p className="text-muted-foreground text-sm">
            Zoom integration is coming soon. You'll be able to connect your
            account so Cosma can automatically join your meetings and generate
            visit notes after each session.
          </p>
        </div>
        <Button onClick={onComplete}>Continue</Button>
      </div>
    )
  }

  return (
    <div className="flex flex-col items-center gap-6 text-center">
      <div className="space-y-1">
        <h2 className="text-2xl font-semibold">Connect Zoom</h2>
        <p className="text-muted-foreground text-sm">
          Connect your Zoom account so Cosma can automatically join your
          meetings and generate visit notes after each session.
        </p>
      </div>
      <Button onClick={connectZoom} disabled={loading} size="lg">
        {loading ? (
          <>
            <Icons.spinner className="mr-2 h-4 w-4 animate-spin" />
            Connecting...
          </>
        ) : (
          <>
            <span className="mr-2 flex h-6 w-6 items-center justify-center rounded-sm border border-white">
              <SiZoom className="size-4 text-white" />
            </span>
            Connect Zoom
          </>
        )}
      </Button>
    </div>
  )
}

// ─── Step 4: All Set ─────────────────────────────────────────────────────────

function AllSetStep() {
  const [loading, setLoading] = useState(false)

  async function handleGoToCosma() {
    setLoading(true)
    await completeOnboarding()
    window.location.href = href("/notes")
  }

  return (
    <div className="flex flex-col items-center gap-6 text-center">
      <motion.div
        initial={{ scale: 0, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        transition={{ type: "spring", stiffness: 200, damping: 15, delay: 0.1 }}
        className="relative flex size-24 items-center justify-center rounded-full"
        style={{ background: "var(--brand-navy-deep)" }}
      >
        {/* ping ring */}
        <motion.span
          initial={{ scale: 0.8, opacity: 0.6 }}
          animate={{ scale: 1.6, opacity: 0 }}
          transition={{ duration: 1.2, repeat: Infinity, ease: "easeOut" }}
          className="absolute inset-0 rounded-full"
          style={{ background: "var(--brand-navy-deep)" }}
        />
        {/* checkmark */}
        <svg
          viewBox="0 0 52 52"
          className="size-12"
          fill="none"
          stroke="var(--brand-gold)"
          strokeWidth="4"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <motion.path
            d="M14 27 L23 36 L38 18"
            initial={{ pathLength: 0 }}
            animate={{ pathLength: 1 }}
            transition={{ duration: 0.5, delay: 0.4, ease: "easeOut" }}
          />
        </svg>
      </motion.div>

      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.7, duration: 0.4 }}
        className="flex flex-col items-center gap-3"
      >
        <h2 className="text-3xl font-bold">You're all set!</h2>
        <p className="text-muted-foreground max-w-md text-base">
          Your biography is saved, your note style is loaded, and Cosma will
          join your Zoom meetings automatically. You're ready to start using
          Cosma.
        </p>
        <Button
          size="lg"
          onClick={handleGoToCosma}
          disabled={loading}
          className="mt-2"
        >
          {loading ? (
            <>
              <Icons.spinner className="mr-2 h-4 w-4 animate-spin" />
              Loading...
            </>
          ) : (
            "Go to Cosma"
          )}
        </Button>
      </motion.div>
    </div>
  )
}

// ─── Step indicator ──────────────────────────────────────────────────────────

const STEP_LABELS = ["Perfil", "Historial", "Zoom"]

function OnboardingStepIndicator({ step }: { step: Step }) {
  if (step === 0 || step === 4) return null

  return (
    <StepIndicator currentStep={step} totalSteps={3} labels={STEP_LABELS} />
  )
}

// ─── Main component ──────────────────────────────────────────────────────────

export default function OnboardingPage({ loaderData }: Route.ComponentProps) {
  const initialStep = getInitialStep(loaderData)
  const [step, setStep] = useState<Step>(initialStep)

  const advance = () => setStep((s) => (s + 1) as Step)

  return (
    <div className="flex min-h-screen items-center justify-center">
      <div className="w-full max-w-lg space-y-6 px-8 py-16">
        <OnboardingStepIndicator step={step} />

        {step === 0 && <WelcomeStep onNext={advance} />}
        {step === 1 && (
          <BiographyStep
            biography={loaderData.biography}
            onComplete={advance}
          />
        )}
        {step === 2 && <NotesStep onComplete={advance} />}
        {step === 3 && <ZoomStep onComplete={advance} />}
        {step === 4 && <AllSetStep />}
      </div>
    </div>
  )
}
