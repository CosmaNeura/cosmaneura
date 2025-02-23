"use client"

import * as chrono from "chrono-node"
import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
} from "react"

import { CalendarDays } from "lucide-react"

import { Button } from "~/components/ui/button"
import { Input } from "~/components/ui/input"
import { cn } from "~/lib/utils"

import { DateTimePickerPopover } from "./datetime-picker-popover"
import {
  generateDate,
  generateDateString,
  isValidDateFormat,
} from "./datetime-utils"

const defaultSuggestions = [
  "Tomorrow",
  "Tomorrow morning",
  "Tomorrow night",
  "Next Monday",
  "Next Sunday",
]

function generateSuggestions(
  inputValue: string,
  suggestion: Suggestion | null,
): Suggestion[] {
  if (!inputValue.length) {
    return defaultSuggestions.map((suggestion) => ({
      date: generateDate(suggestion),
      inputString: suggestion,
    }))
  }

  const filteredDefaultSuggestions = defaultSuggestions.filter((suggestion) =>
    suggestion.toLowerCase().includes(inputValue.toLowerCase()),
  )
  if (filteredDefaultSuggestions.length) {
    return filteredDefaultSuggestions.map((suggestion) => ({
      date: generateDate(suggestion),
      inputString: suggestion,
    }))
  }

  return [suggestion].filter((suggestion) => suggestion !== null)
}

interface Suggestion {
  date: Date
  inputString: string
}

interface DateTimePickerProps
  extends React.InputHTMLAttributes<HTMLInputElement> {
  dateTime: Date | undefined
  setDateTime: React.Dispatch<React.SetStateAction<Date | undefined>>
}

export const DateTimePicker = forwardRef<HTMLInputElement, DateTimePickerProps>(
  ({ dateTime, setDateTime, ...props }, ref) => {
    const [suggestion, setSuggestion] = useState<Suggestion | null>(null)
    const [inputValue, setInputValue] = useState("")
    const [isOpen, setIsOpen] = useState(false)
    const [isClosing, setClosing] = useState(false)
    const [selectedIndex, setSelectedIndex] = useState(0)

    const inputRef = useRef<HTMLInputElement>(null)
    const dropdownRef = useRef<HTMLDivElement>(null)

    useImperativeHandle(ref, () => inputRef.current!)

    const suggestions = generateSuggestions(inputValue, suggestion)

    function handleInputChange(e: React.ChangeEvent<HTMLInputElement>) {
      setInputValue(e.target.value)
      setIsOpen(true)
      setSelectedIndex(0)
      const result = chrono.parseDate(e.target.value)
      if (result) {
        setSuggestion({ date: result, inputString: e.target.value })
      } else {
        setSuggestion(null)
      }
    }

    function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
      if (e.key === "ArrowDown") {
        e.preventDefault()
        setSelectedIndex((prevIndex) =>
          prevIndex < suggestions.length - 1 ? prevIndex + 1 : prevIndex,
        )
      } else if (e.key === "ArrowUp") {
        e.preventDefault()
        setSelectedIndex((prevIndex) => (prevIndex > 0 ? prevIndex - 1 : 0))
      } else if (e.key === "Enter" && isOpen && suggestions.length > 0) {
        e.preventDefault()
        const dateStr = generateDateString(suggestions[selectedIndex].date)
        setInputValue(dateStr)
        setDateTime(suggestions[selectedIndex].date)
        closeDropdown()
      } else if (e.key === "Escape" || e.key === "Tab") {
        closeDropdown()
      }
    }

    function closeDropdown() {
      setClosing(true)
      setSelectedIndex(0)
      setTimeout(() => {
        setIsOpen(false)
        setClosing(false)
      }, 200)
    }

    useEffect(() => {
      if (!inputValue) setDateTime(undefined)
      if (!isValidDateFormat(inputValue)) setDateTime(undefined)
    }, [inputValue, setDateTime])

    useEffect(() => {
      function handleClickOutside(e: MouseEvent) {
        if (
          dropdownRef.current &&
          !dropdownRef.current.contains(e.target as Node) &&
          inputRef.current &&
          !inputRef.current.contains(e.target as Node)
        ) {
          closeDropdown()
        }
      }

      document.addEventListener("mousedown", handleClickOutside)

      return () => {
        document.removeEventListener("mousedown", handleClickOutside)
      }
    }, [])

    return (
      <div className="relative">
        <div className="relative">
          <Input
            placeholder="Tomorrow morning"
            {...props}
            ref={inputRef}
            type="text"
            value={inputValue}
            onChange={handleInputChange}
            onKeyDown={handleKeyDown}
            onFocus={() => setIsOpen(true)}
            onClick={() => setIsOpen(true)}
          />
          <DateTimePickerPopover
            onOpen={() => setSuggestion(null)}
            dateTime={dateTime}
            setDateTime={setDateTime}
            setInputValue={setInputValue}
          >
            <Button
              size="icon"
              variant="ghost"
              className="text-muted-foreground absolute top-1/2 right-3 size-4 -translate-y-1/2 rounded-sm"
            >
              <span className="sr-only">Open normal date time picker</span>
              <CalendarDays />
            </Button>
          </DateTimePickerPopover>
        </div>

        {isOpen && suggestions.length > 0 && (
          <div
            ref={dropdownRef}
            role="dialog"
            className={cn(
              "bg-popover animate-in fade-in-0 zoom-in-95 slide-in-from-top-2 absolute z-10 mt-2 w-full rounded-md border p-0 shadow-md transition-all",
              isClosing && "animate-out fade-out-0 zoom-out-95 duration-300",
            )}
            tabIndex={-1}
            aria-label="Suggestions"
          >
            <ul
              role="listbox"
              aria-label="Suggestions"
              className="max-h-56 overflow-auto p-1"
            >
              {suggestions.map((suggestion, index) => (
                <li
                  key={suggestion.inputString}
                  role="option"
                  aria-selected={selectedIndex === index}
                  className={cn(
                    "flex cursor-pointer items-center justify-between gap-1 rounded px-2.5 py-2 text-sm",
                    index === selectedIndex &&
                      "bg-accent text-accent-foreground",
                  )}
                  onClick={() => {
                    const dateStr = generateDateString(suggestion.date)
                    setInputValue(dateStr)
                    setDateTime(suggestion.date)
                    closeDropdown()
                    inputRef.current?.focus()
                  }}
                  onMouseEnter={() => setSelectedIndex(index)}
                >
                  <span className="xs:w-auto w-[110px] truncate">
                    {suggestion.inputString}
                  </span>
                  <span className="text-muted-foreground shrink-0 text-xs">
                    {generateDateString(suggestion.date)}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    )
  },
)

DateTimePicker.displayName = "DateTimePicker"
