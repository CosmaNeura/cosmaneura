import { useEffect, useState } from "react"
import { useFormContext } from "react-hook-form"

import { CalendarIcon } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Calendar } from "@/components/ui/calendar"
import {
  FormControl,
  FormDescription,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import { standardDateFormat } from "~/lib/date"

export function DatePickerWithInput({
  field,
  label,
  description,
  calendarProps = {},
}: {
  field: ControllerRenderProps<FieldValues, string>
  label: string
  description?: string
  calendarProps?: Record<string, any>
}) {
  const form = useFormContext()
  const [inputValue, setInputValue] = useState(
    field.value ? standardDateFormat(field.value) : "",
  )
  const [isOpen, setIsOpen] = useState(false)

  // Sync input value with field value when it changes externally
  useEffect(() => {
    setInputValue(field.value ? standardDateFormat(field.value) : "")
  }, [field.value])

  // Parse date string to Date object
  const parseDate = (str: string): Date | null => {
    const date = new Date(str)
    return isNaN(date.getTime()) ? null : date
  }

  // Handle input blur: parse date and update field or set error
  const handleBlur = () => {
    const parsedDate = parseDate(inputValue)
    if (parsedDate) {
      field.onChange(parsedDate)
      form.clearErrors(field.name)
    } else if (inputValue) {
      form.setError(field.name, { type: "manual", message: "Invalid date" })
    }
  }

  return (
    <FormItem>
      <FormLabel>{label}</FormLabel>
      <FormControl>
        <Popover open={isOpen} onOpenChange={setIsOpen}>
          <PopoverTrigger asChild>
            <div className="relative">
              <Input
                value={inputValue}
                onChange={(e) => setInputValue(e.target.value)}
                onBlur={handleBlur}
                placeholder="YYYY-MM-DD"
                className="pr-10"
              />
              <Button
                variant="ghost"
                className="absolute top-0 right-0 h-full px-3"
              >
                <CalendarIcon className="h-4 w-4" />
              </Button>
            </div>
          </PopoverTrigger>
          <PopoverContent className="w-auto p-0" align="start">
            <Calendar
              mode="single"
              selected={field.value}
              onSelect={(date) => {
                field.onChange(date)
                setInputValue(date ? standardDateFormat(date) : "")
                setIsOpen(false)
              }}
              {...calendarProps}
            />
          </PopoverContent>
        </Popover>
      </FormControl>
      {description && <FormDescription>{description}</FormDescription>}
      <FormMessage />
    </FormItem>
  )
}
