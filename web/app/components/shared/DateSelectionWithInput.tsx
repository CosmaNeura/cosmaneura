/* eslint-disable max-lines */
"use client"

import React, { type FC, JSX, useEffect, useRef, useState } from "react"
import { Button } from "react-day-picker"

import {
  Calendar,
  CheckIcon,
  ChevronDownIcon,
  ChevronUpIcon,
} from "lucide-react"

import { cn } from "@/lib/utils"

import { DateInput } from "./DateInput"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@radix-ui/react-popover"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@radix-ui/react-select"

/* eslint-disable max-lines */

/* eslint-disable max-lines */

/* eslint-disable max-lines */

export interface DatePickerProps {
  /** Click handler for applying the selected date from DatePicker. */
  onUpdate?: (selectedDate: Date) => void
  /** Initial value for the date */
  initialDate?: Date | string
  /** Alignment of popover */
  align?: "start" | "center" | "end"
  /** Option for locale */
  locale?: string
}

const formatDate = (date: Date, locale: string = "en-us"): string => {
  return date.toLocaleDateString(locale, {
    month: "short",
    day: "numeric",
    year: "numeric",
  })
}

const getDateAdjustedForTimezone = (dateInput: Date | string): Date => {
  if (typeof dateInput === "string") {
    const parts = dateInput.split("-").map((part) => parseInt(part, 10))
    const date = new Date(parts[0], parts[1] - 1, parts[2])
    return date
  } else {
    return dateInput
  }
}

interface Preset {
  name: string
  label: string
}

const PRESETS: Preset[] = [
  { name: "today", label: "Today" },
  { name: "yesterday", label: "Yesterday" },
  { name: "lastWeek", label: "7 days ago" },
  { name: "lastMonth", label: "30 days ago" },
]

export const DatePicker: FC<DatePickerProps> & {
  filePath: string
} = ({
  initialDate = new Date(),
  onUpdate,
  align = "end",
  locale = "en-US",
}): JSX.Element => {
  const [isOpen, setIsOpen] = useState(false)
  const [selectedDate, setSelectedDate] = useState<Date>(
    getDateAdjustedForTimezone(initialDate),
  )
  const [selectedPreset, setSelectedPreset] = useState<string | undefined>(
    undefined,
  )
  const [isSmallScreen, setIsSmallScreen] = useState(
    typeof window !== "undefined" ? window.innerWidth < 960 : false,
  )

  const openedSelectedDateRef = useRef<Date>()

  useEffect(() => {
    const handleResize = (): void => {
      setIsSmallScreen(window.innerWidth < 960)
    }
    window.addEventListener("resize", handleResize)
    return () => {
      window.removeEventListener("resize", handleResize)
    }
  }, [])

  useEffect(() => {
    if (isOpen) {
      openedSelectedDateRef.current = selectedDate
    }
  }, [isOpen])

  const getPresetDate = (presetName: string): Date => {
    const preset = PRESETS.find(({ name }) => name === presetName)
    if (!preset) throw new Error(`Unknown date preset: ${presetName}`)
    const date = new Date()
    switch (preset.name) {
      case "today":
        break
      case "yesterday":
        date.setDate(date.getDate() - 1)
        break
      case "lastWeek":
        date.setDate(date.getDate() - 7)
        break
      case "lastMonth":
        date.setDate(date.getDate() - 30)
        break
    }
    date.setHours(0, 0, 0, 0)
    return date
  }

  const checkPreset = (): void => {
    for (const preset of PRESETS) {
      const presetDate = getPresetDate(preset.name)
      if (selectedDate.getTime() === presetDate.getTime()) {
        setSelectedPreset(preset.name)
        return
      }
    }
    setSelectedPreset(undefined)
  }

  useEffect(() => {
    checkPreset()
  }, [selectedDate])

  const resetValues = (): void => {
    setSelectedDate(getDateAdjustedForTimezone(initialDate))
  }

  return (
    <Popover
      modal={true}
      open={isOpen}
      onOpenChange={(open: boolean) => {
        if (!open) {
          resetValues()
        }
        setIsOpen(open)
      }}
    >
      <PopoverTrigger asChild>
        <Button size={"lg"} variant="outline">
          <div className="text-right">
            <div className="py-1">
              <div>{formatDate(selectedDate, locale)}</div>
            </div>
          </div>
          <div className="-mr-2 scale-125 pl-1 opacity-60">
            {isOpen ? (
              <ChevronUpIcon width={24} />
            ) : (
              <ChevronDownIcon width={24} />
            )}
          </div>
        </Button>
      </PopoverTrigger>
      <PopoverContent align={align} className="w-auto">
        <div className="flex py-2">
          <div className="flex flex-col">
            <div className="flex flex-col gap-2 px-3 pb-4">
              <DateInput
                value={selectedDate}
                onChange={(date) => {
                  setSelectedDate(date)
                  setSelectedPreset(undefined)
                }}
              />
            </div>
            {isSmallScreen && (
              <Select
                value={selectedPreset}
                onValueChange={(value) => {
                  const presetDate = getPresetDate(value)
                  setSelectedDate(presetDate)
                  setSelectedPreset(value)
                }}
              >
                <SelectTrigger className="mx-auto mb-2 w-[180px]">
                  <SelectValue placeholder="Select preset" />
                </SelectTrigger>
                <SelectContent>
                  {PRESETS.map((preset) => (
                    <SelectItem key={preset.name} value={preset.name}>
                      {preset.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
            <Calendar
              mode="single"
              selected={selectedDate}
              onSelect={(date) => {
                if (date) {
                  setSelectedDate(date)
                  setSelectedPreset(undefined)
                }
              }}
              defaultMonth={selectedDate}
              numberOfMonths={isSmallScreen ? 1 : 2}
            />
          </div>
          {!isSmallScreen && (
            <div className="flex flex-col items-end gap-1 pr-2 pb-6 pl-6">
              {PRESETS.map((preset) => (
                <Button
                  key={preset.name}
                  className={cn(
                    selectedPreset === preset.name && "pointer-events-none",
                  )}
                  variant="ghost"
                  onClick={() => {
                    const presetDate = getPresetDate(preset.name)
                    setSelectedDate(presetDate)
                    setSelectedPreset(preset.name)
                  }}
                >
                  <>
                    <span
                      className={cn(
                        "pr-2 opacity-0",
                        selectedPreset === preset.name && "opacity-70",
                      )}
                    >
                      <CheckIcon width={18} height={18} />
                    </span>
                    {preset.label}
                  </>
                </Button>
              ))}
            </div>
          )}
        </div>
        <div className="flex justify-end gap-2 py-2 pr-4">
          <Button
            onClick={() => {
              setIsOpen(false)
              resetValues()
            }}
            variant="ghost"
          >
            Cancel
          </Button>
          <Button
            onClick={() => {
              setIsOpen(false)
              if (
                selectedDate.getTime() !==
                openedSelectedDateRef.current?.getTime()
              ) {
                onUpdate?.(selectedDate)
              }
            }}
          >
            Update
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  )
}

DatePicker.displayName = "DatePicker"
DatePicker.filePath = "libs/shared/ui-kit/src/lib/date-picker/date-picker.tsx"
