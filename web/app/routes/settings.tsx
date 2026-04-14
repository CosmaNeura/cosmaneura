import { useState } from "react"
import { useForm } from "react-hook-form"
import { MetaFunction, useRevalidator } from "react-router"
import { z } from "zod"

import { ChevronDown, Upload } from "lucide-react"
import pluralize from "pluralize"

import type { Route } from "./+types/settings"

import { AppSidebar } from "@/components/app-sidebar"
import { Button } from "@/components/ui/button"
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar"
import { Icons } from "~/components/icons"
import { ImageDropzone } from "~/components/shared/ImageDropzone"
import { InnerScrollableContent } from "~/components/shared/InnerScrollableContent"
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
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "~/components/ui/dialog"
import { Input } from "~/components/ui/input"
import {
  generateSignedExampleNoteUploadUrl,
  settingsData,
  updateSettingsData,
  uploadExampleNote,
  zoomConnect,
  zoomDisconnect,
} from "~/configuration/client"
import { toast } from "~/hooks/use-toast"
import { uploadFileToAzure } from "~/lib/azure-upload"

import { invariant } from "@epic-web/invariant"
import { zodResolver } from "@hookform/resolvers/zod"

type NoteSchema = Route.ComponentProps["loaderData"]["notes_schema"][number]

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

function NoteUpload({ noteSchemas }: { noteSchemas: NoteSchema[] }) {
  const formSchema = z.object({
    //   image: z
    //     .instanceof(File),
    //   // .refine((file) => file.size !== 0, "Please upload an image"),
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

    const { data: sasSignature } = await generateSignedExampleNoteUploadUrl()

    invariant(
      sasSignature,
      "sasSignature should always exist unless the server fails",
    )

    const { stampedFileName } = await uploadFileToAzure(file, sasSignature)

    const _internalResponse = await uploadExampleNote({
      query: {
        file_name: stampedFileName,
        note_type: values.note_type,
      },
    })

    setFile(null)

    toast({
      title: "Upload Complete",
      description:
        "Your intake notes have been uploaded successfully. They will be processed and displayed here in a couple minutes.",
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
              your patients, so make sure they are a great representation of the
              structure and quality of note that you want to see from your
              personalized AI!
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
                    Click here or drag to select an image smaller than 10MB
                  </p>
                </div>
              </div>
            )}
            {file && (
              <div className="flex flex-col gap-1 font-semibold">
                <p>{file.name}</p>
                <p className="text-muted-foreground text-xs">
                  Press the upload button below to add the example patient note
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

          <Button disabled={!file} type="submit">
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
  const revalidator = useRevalidator()
  const [open, setOpen] = useState(false)
  const [disconnecting, setDisconnecting] = useState(false)

  const formSchema = z.object({
    email: z.string().email("Please enter a valid email address"),
  })

  const form = useForm({
    resolver: zodResolver(formSchema),
    defaultValues: { email: "" },
  })

  async function onConnect(values: z.infer<typeof formSchema>) {
    const { error } = await zoomConnect({ body: { email: values.email } })
    if (error) {
      const detail =
        error.detail && error.detail.length > 0
          ? error.detail.map((e) => e.msg).join(", ")
          : "Failed to connect Zoom. Please try again."
      toast({ title: "Connection failed", description: detail, variant: "destructive" })
      return
    }
    toast({ title: "Zoom connected successfully" })
    setOpen(false)
    form.reset()
    revalidator.revalidate()
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
    revalidator.revalidate()
    setDisconnecting(false)
  }

  return (
    <section className="prose-headings prose w-1/2 space-y-4">
      <h3>Zoom Integration</h3>
      <p className="text-muted-foreground text-sm">
        Connect your Zoom account so Cosma can automatically join your meetings
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
        <Dialog open={open} onOpenChange={setOpen}>
          <Button onClick={() => setOpen(true)}>Connect Zoom</Button>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Connect your Zoom account</DialogTitle>
              <DialogDescription>
                Enter the email address associated with your Zoom account. It
                must belong to the same Zoom organization as this app.
              </DialogDescription>
            </DialogHeader>
            <Form {...form}>
              <form
                onSubmit={form.handleSubmit(onConnect)}
                className="space-y-4"
              >
                <FormField
                  control={form.control}
                  name="email"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Zoom Email</FormLabel>
                      <FormControl>
                        <Input
                          type="email"
                          placeholder="you@yourorganization.com"
                          {...field}
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <Button
                  type="submit"
                  disabled={form.formState.isSubmitting}
                  className="w-full"
                >
                  {form.formState.isSubmitting ? (
                    <>
                      <Icons.spinner className="mr-2 h-4 w-4 animate-spin" />
                      Connecting...
                    </>
                  ) : (
                    "Connect"
                  )}
                </Button>
              </form>
            </Form>
          </DialogContent>
        </Dialog>
      )}
    </section>
  )
}

export async function clientLoader(_loaderArgs: Route.ClientLoaderArgs) {
  // TODO need idiomatic error handling here
  const { data } = await settingsData()
  invariant(data, "data should exist")

  return data
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

export default function SettingsPage({ loaderData }: Route.ComponentProps) {
  return (
    <SidebarProvider
      // eliminate the message sidebar
      style={{ "--sidebar-width": "3em" } as React.CSSProperties}
    >
      <AppSidebar isLoading={false} entries={false} />

      <SidebarInset>
        <header className="bg-background sticky top-0 flex shrink-0 items-center gap-2 border-b p-4">
          <h1 className="text-lg font-semibold">Settings</h1>
        </header>
        <InnerScrollableContent>
          <BiographyInput biography={loaderData.biography} />
          <NoteUpload noteSchemas={loaderData.notes_schema} />
          <ZoomConnect zoomConnected={loaderData.zoom_connected} />
        </InnerScrollableContent>
      </SidebarInset>
    </SidebarProvider>
  )
}
