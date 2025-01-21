import { useState } from "react"
import { useForm } from "react-hook-form"
import { MetaFunction } from "react-router"
import { z } from "zod"

import { ChevronDown, Upload } from "lucide-react"
import pluralize from "pluralize"

import type { Route } from "./+types/settings"

import { AppSidebar } from "@/components/app-sidebar"
import { Button } from "@/components/ui/button"
import {
  DropZoneArea,
  Dropzone,
  DropzoneMessage,
  DropzoneTrigger,
  useDropzone,
} from "@/components/ui/dropzone"
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar"
import { Icons } from "~/components/icons"
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
import { Separator } from "~/components/ui/separator"
import {
  generateSignedExampleIntakeUploadUrl,
  generateSignedExampleNoteUploadUrl,
  settingsData,
  uploadExampleIntake,
  uploadExampleNote,
} from "~/configuration/client"
import { toast } from "~/hooks/use-toast"
import { uploadFileToAzure } from "~/lib/azure-upload"

import { invariant } from "@epic-web/invariant"
import { zodResolver } from "@hookform/resolvers/zod"

function ImageDropzone({
  children,
  onDropFile,
}: {
  children: React.ReactNode
  onDropFile: (file: File) => void
}) {
  const dropzone = useDropzone({
    // this off-the-shelf component seems to want to upload files right away, which we do not do
    // this expectation mismatch could cause issues in the future. Additionally, react-dropzone
    // is not officially supported by react 19, which could further cause issues.

    onDropFile: async (file: File) => {
      // naming is confusing here! This is the argument passed to the image drop
      // designed to bet a hook to set the file reference
      onDropFile(file)

      return {
        status: "success",
        result: null,
      }
    },
    validation: {
      accept: {
        "image/*": [".png", ".jpg", ".jpeg", ".pdf"],
      },
      // 10mb
      maxSize: 10 * 1024 * 1024,
      maxFiles: 1,
    },
    shiftOnMaxFiles: true,
  })

  return (
    <Dropzone {...dropzone}>
      <div className="flex justify-between">
        <DropzoneMessage />
      </div>
      <DropZoneArea>
        <DropzoneTrigger className="my-6 flex gap-8 bg-transparent text-sm">
          {children}
        </DropzoneTrigger>
      </DropZoneArea>
    </Dropzone>
  )
}

function IntakeUpload() {
  const [file, setFile] = useState<File | null>(null)
  const form = useForm({})

  async function onSubmit(_values: z.infer<typeof formSchema>) {
    invariant(
      file,
      "file should be required, submit should be impossible without a valid file",
    )

    const { data: sasSignature } = await generateSignedExampleIntakeUploadUrl()

    invariant(
      sasSignature,
      "sasSignature should always exist unless the server fails",
    )

    const { stampedFileName } = await uploadFileToAzure(file, sasSignature)

    const _internalResponse = await uploadExampleIntake({
      query: {
        file_name: stampedFileName,
      },
    })

    setFile(null)

    toast({
      title: "Upload Complete",
      description:
        "Your intake notes have been uploaded successfully. They will be processed and displayed here in a couple minutes.",
    })
  }

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)}>
        <section className="w-1/2 space-y-4">
          <div className="space-y-2">
            <h2 className="text-left text-xl font-semibold">Intake</h2>
            <p className="text-left text-sm text-muted-foreground">
              Upload the best intake notes that patients have provided you in
              the past. This will be used to create an intake process for each
              appointment type.
            </p>
          </div>
          <ImageDropzone onDropFile={setFile}>
            {!file && (
              <div className="flex items-center gap-4 font-semibold">
                <div className="rounded-full bg-muted p-3">
                  <Upload className="h-4 w-4" />
                </div>
                <div className="flex flex-col gap-1">
                  <p>Upload Example Intake Note</p>
                  <p className="text-xs text-muted-foreground">
                    Click here or drag to select an image smaller than 10MB
                  </p>
                </div>
              </div>
            )}
            {file && (
              <div className="flex flex-col gap-1 font-semibold">
                <p>{file.name}</p>
                <p className="text-xs text-muted-foreground">
                  Press the upload button below to add the note
                </p>
              </div>
            )}
          </ImageDropzone>

          <Button disabled={!file} type="submit">
            {form.formState.isSubmitting ? (
              <>
                <Icons.spinner className="mr-2 h-4 w-4 animate-spin" />
                Uploading...
              </>
            ) : (
              "Upload Example Intake Form"
            )}
          </Button>
        </section>
      </form>
    </Form>
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
      {noteSchemas.length > 1 ? (
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
            <div className="ml-auto flex items-center gap-2 text-sm text-muted-foreground">
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
                Here's the note structure we've extracted so far from your note:
              </p>
              <div className="mt-4 border-l-2 pl-4">
                <p className="text-sm text-muted-foreground">
                  {Object.entries(schema.fields).map(([field, description]) => (
                    <div key={field} className="mb-2">
                      <span className="font-semibold">{field}:</span>{" "}
                      {description}
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

  function noteSchemasToComboBoxItems(noteSchemas: NoteSchema[]) {
    return noteSchemas.map((schema) => ({
      // value: schema.note_type,
      value: schema.id,
      label: schema.note_type,
    }))
  }

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)} className="flex gap-8">
        <section className="w-1/2 space-y-4">
          <div className="space-y-2">
            <h2 className="text-left text-xl font-semibold">Notetaking</h2>
            <p className="text-left text-sm text-muted-foreground">
              Upload high quality examples of the different types of notes that
              you use. These examples will be used to generate future notes for
              your patients, so make sure they are a great representation of the
              structure and quality of note that you want to see from your
              personalized AI!
            </p>
            <p className="text-left text-sm text-muted-foreground">
              For example: if you use the telehealth SOAP note, pick a
              high-quality note for that note type and upload a PDF version of
              this note type.
            </p>
          </div>

          <ImageDropzone onDropFile={setFile}>
            {!file && (
              <div className="flex items-center gap-4 font-semibold">
                <div className="rounded-full bg-muted p-3">
                  <Upload className="h-4 w-4" />
                </div>
                <div className="flex flex-col gap-1">
                  <p>Upload Example Visit Note</p>
                  <p className="text-xs text-muted-foreground">
                    Click here or drag to select an image smaller than 10MB
                  </p>
                </div>
              </div>
            )}
            {file && (
              <div className="flex flex-col gap-1 font-semibold">
                <p>{file.name}</p>
                <p className="text-xs text-muted-foreground">
                  Press the upload button below to add the example patient notes
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
                    items={noteSchemasToComboBoxItems(noteSchemas)}
                    {...field}
                  />
                </FormControl>
                <FormDescription>
                  Enter the type of note you want to upload. For example, "SOAP
                  Note". If you've already uploaded a note for this note type,
                  and want to add more examples to improve our systems
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
              "Upload Example Visit Notes"
            )}
          </Button>
        </section>
        <NoteSchemaDisplay noteSchemas={noteSchemas} />
      </form>
    </Form>
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
        <header className="sticky top-0 flex shrink-0 items-center gap-2 border-b bg-background p-4">
          <h1 className="text-lg font-semibold">Settings</h1>
        </header>
        <div className="flex flex-col gap-8 p-6">
          <NoteUpload noteSchemas={loaderData.notes_schema} />
          <Separator />
          <IntakeUpload />
        </div>
      </SidebarInset>
    </SidebarProvider>
  )
}
