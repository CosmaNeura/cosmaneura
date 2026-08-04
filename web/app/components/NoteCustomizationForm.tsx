import { useForm } from "react-hook-form"
import { z } from "zod"

import { Button } from "@/components/ui/button"
import { Icons } from "~/components/icons"
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
} from "~/components/ui/form"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "~/components/ui/select"
import { Switch } from "~/components/ui/switch"
import { updateNoteCustomization } from "~/configuration/client"
import { toast } from "~/hooks/use-toast"
import { useTerminology } from "~/utils/terminology"
import type { LabelSet } from "~/utils/terminology"

import { zodResolver } from "@hookform/resolvers/zod"

type SchemaLite = { id: string; note_type: string }

type Customization = {
  doctor_note_schema_id: string
  subjective_source: "patient_only" | "patient_and_doctor"
  pronoun_style: "infer_from_transcript" | "neutral"
  plan_ordering: "patient_then_doctor" | "as_discussed"
  flag_new_medications: boolean
  require_plan_reason: boolean
  ros_distinct_from_subjective: boolean
  custom_instructions: string | null
}

const formSchema = z.object({
  subjective_source: z.enum(["patient_only", "patient_and_doctor"]),
  pronoun_style: z.enum(["infer_from_transcript", "neutral"]),
  plan_ordering: z.enum(["patient_then_doctor", "as_discussed"]),
  flag_new_medications: z.boolean(),
  require_plan_reason: z.boolean(),
  ros_distinct_from_subjective: z.boolean(),
  custom_instructions: z.string().max(4000),
})

type FormValues = z.infer<typeof formSchema>

const buildSelects = (
  t: LabelSet,
): {
  name: keyof Pick<
    FormValues,
    "subjective_source" | "pronoun_style" | "plan_ordering"
  >
  label: string
  help: string
  options: { value: string; label: string }[]
}[] => [
  {
    name: "subjective_source",
    label: "Subjective source",
    help: "Where the Subjective section draws from.",
    options: [
      { value: "patient_only", label: `${t.patientTitle} statements only` },
      {
        value: "patient_and_doctor",
        label: `${t.patientTitle} + ${t.provider}-elicited history`,
      },
    ],
  },
  {
    name: "pronoun_style",
    label: "Pronouns",
    help: `How the note refers to the ${t.patient}.`,
    options: [
      {
        value: "infer_from_transcript",
        label: "Infer pronouns from the visit",
      },
      { value: "neutral", label: `Always "the ${t.patient}"` },
    ],
  },
  {
    name: "plan_ordering",
    label: "Plan ordering",
    help: "Order of bullets within each condition's plan.",
    options: [
      {
        value: "patient_then_doctor",
        label: `${t.patientTitle} actions first, then ${t.provider}`,
      },
      { value: "as_discussed", label: "As discussed" },
    ],
  },
]

const SWITCHES: {
  name: keyof Pick<
    FormValues,
    | "flag_new_medications"
    | "require_plan_reason"
    | "ros_distinct_from_subjective"
  >
  label: string
}[] = [
  {
    name: "flag_new_medications",
    label: 'Tag newly-prescribed meds "(new this visit)"',
  },
  {
    name: "require_plan_reason",
    label: "Require a reason on every Plan bullet",
  },
  {
    name: "ros_distinct_from_subjective",
    label: "Keep Review of Systems distinct from Subjective",
  },
]

export function NoteCustomizationForm({
  schema,
  customization,
}: {
  schema: SchemaLite
  customization: Customization
}) {
  const t = useTerminology()
  const form = useForm<FormValues>({
    resolver: zodResolver(formSchema),
    defaultValues: {
      subjective_source: customization.subjective_source,
      pronoun_style: customization.pronoun_style,
      plan_ordering: customization.plan_ordering,
      flag_new_medications: customization.flag_new_medications,
      require_plan_reason: customization.require_plan_reason,
      ros_distinct_from_subjective: customization.ros_distinct_from_subjective,
      custom_instructions: customization.custom_instructions ?? "",
    },
  })

  async function onSubmit(values: FormValues) {
    await updateNoteCustomization({
      path: { schema_id: schema.id },
      body: {
        ...values,
        custom_instructions: values.custom_instructions || null,
      },
    })
    toast({
      title: "Customization saved",
      description: `Updated AI output settings for "${schema.note_type}". This affects only this note type.`,
    })
  }

  return (
    <div className="flex flex-col rounded-lg border p-4">
      <div className="font-medium">{schema.note_type}</div>
      <Form {...form}>
        <form onSubmit={form.handleSubmit(onSubmit)} className="mt-3 space-y-4">
          {buildSelects(t).map((s) => (
            <FormField
              key={s.name}
              control={form.control}
              name={s.name}
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{s.label}</FormLabel>
                  <Select
                    value={String(field.value)}
                    onValueChange={field.onChange}
                  >
                    <FormControl>
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                    </FormControl>
                    <SelectContent>
                      {s.options.map((o) => (
                        <SelectItem key={o.value} value={o.value}>
                          {o.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <FormDescription>{s.help}</FormDescription>
                </FormItem>
              )}
            />
          ))}

          {SWITCHES.map((s) => (
            <FormField
              key={s.name}
              control={form.control}
              name={s.name}
              render={({ field }) => (
                <FormItem className="flex items-center justify-between gap-4">
                  <FormLabel className="font-normal">{s.label}</FormLabel>
                  <FormControl>
                    <Switch
                      checked={Boolean(field.value)}
                      onCheckedChange={field.onChange}
                    />
                  </FormControl>
                </FormItem>
              )}
            />
          ))}

          <FormField
            control={form.control}
            name="custom_instructions"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Custom instructions</FormLabel>
                <FormControl>
                  <textarea
                    className="w-full rounded-md border p-2"
                    rows={3}
                    maxLength={4000}
                    placeholder="Additional guidance for this note type (optional)."
                    {...field}
                    value={field.value ?? ""}
                  />
                </FormControl>
                <FormDescription>
                  Appended to the AI&apos;s guidance. Affects only this note
                  type.
                </FormDescription>
              </FormItem>
            )}
          />

          <Button type="submit" disabled={form.formState.isSubmitting}>
            {form.formState.isSubmitting ? (
              <>
                <Icons.spinner className="mr-2 h-4 w-4 animate-spin" />
                Saving...
              </>
            ) : (
              "Save"
            )}
          </Button>
        </form>
      </Form>
    </div>
  )
}
