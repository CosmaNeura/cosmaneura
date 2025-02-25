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
  generateDateTimeString,
  isValidDateFormat,
} from "./datetime-utils"

const DEFAULT_SUGGESTIONS = [
  "Tomorrow",
  "Tomorrow morning",
  "Tomorrow night",
  "Next Monday",
  "Next Sunday",
]

interface Suggestion {
  date: Date
  inputString: string
}

interface DateTimePickerProps
  extends Omit<
    React.InputHTMLAttributes<HTMLInputElement>,
    "value" | "onChange"
  > {
  showTimePicker?: boolean
  suggestions?: string[]
  value: Date
  onChange: (date?: Date) => void
}

function generateSuggestions(
  // raw input from the input field
  inputValue: string,
  suggestion: Suggestion | null,
  defaultSuggestions: string[],
): Suggestion[] {
  if (!inputValue.length) {
    // if there's currently no text input, just show the default suggestions
    return defaultSuggestions.map((sugg) => ({
      date: generateDate(sugg),
      inputString: sugg,
    }))
  }

  // if there is a text input, match it against the default suggestions
  const filteredDefaultSuggestions = defaultSuggestions.filter((sugg) =>
    sugg.toLowerCase().includes(inputValue.toLowerCase()),
  )

  if (filteredDefaultSuggestions.length) {
    return filteredDefaultSuggestions.map((sugg) => ({
      date: generateDate(sugg),
      inputString: sugg,
    }))
  }

  // if no default suggestions match, then use the generated suggestion
  return suggestion ? [suggestion] : []
}

/**
 * - Allow dates, and optionally times, to be chosen in two ways: natural language and a picker UI.
 *   - Type in a date, tab to the next field, and the date is parsed and set.
 *   - Type in a date, select from a suggestion dropdown
 *   - Select from a preset list of suggestions
 * - Allow dates to be set programmatically, from a parent element or otherwise
 * - Allow <Input/> props to be easily customized
 *
 * There are a couple of defaults to keep in mind:
 *
 * - There are two states for the input field: the date object and the text representation that is bound to the input.
 * - The date object is set in the `FormField` state and a separate text state is used for the <Input/> component.
 */
export const DateTimePicker = forwardRef<HTMLInputElement, DateTimePickerProps>(
  ({ showTimePicker = true, suggestions: suggestionsProp, ...props }, ref) => {
    const dateTimeFormatFunction = showTimePicker
      ? generateDateTimeString
      : generateDateString

    const [inputValue, setInputValue] = useState(
      // inherit the default value applied to the <FormField/>
      (props.value && dateTimeFormatFunction(props.value)) ?? "",
    )

    // combobox state
    const [suggestion, setSuggestion] = useState<Suggestion | null>(null)
    const [isOpen, setIsOpen] = useState(false)
    const [isClosing, setClosing] = useState(false)
    const [selectedIndex, setSelectedIndex] = useState(0)

    const inputRef = useRef<HTMLInputElement>(null)
    const dropdownRef = useRef<HTMLDivElement>(null)

    useImperativeHandle(ref, () => inputRef.current!)

    // TODO should use more exact types here :/
    const dateTime = props.value
    const setDateTime = props.onChange

    // Use passed suggestions if provided, otherwise fall back to DEFAULT_SUGGESTIONS
    const defaultSuggestions = suggestionsProp ?? DEFAULT_SUGGESTIONS
    const computedSuggestions = generateSuggestions(
      inputValue,
      suggestion,
      defaultSuggestions,
    )

    // attempt to parse user input and convert it to a suggestion
    function updateSuggestionFromInput(dateString: string) {
      if (suggestion?.inputString === dateString) return

      const result = chrono.parseDate(dateString)

      if (result) {
        setSuggestion({ date: result, inputString: dateString })
      } else {
        setSuggestion(null)
      }
    }

    // handle natural language from the <Input/> field
    function handleInputChange(e: React.ChangeEvent<HTMLInputElement>) {
      setInputValue(e.target.value)

      // open the dropdown
      setIsOpen(true)
      setSelectedIndex(0)

      // continually update the suggestions list
      updateSuggestionFromInput(e.target.value)
    }

    /**
     * if the user clicks off the date picker we want to provide sane defaults:
     *
     * - if a valid date was inputted, use that
     * - if no valid date was inputted, blank it out to make it clear you must enter a date
     *
     * However, this becomes tricky since blur event is triggered *before* the click handler is executed.
     * This means the dateTime is not yet updated when this event is triggered.
     *
     * - If a user clicks a suggestion the `dateTime` is not yet updated and this function will run
     */
    function handleOnBlur(event: React.FocusEvent<HTMLInputElement>) {
      const blurBecauseSuggestionClick =
        event.relatedTarget == dropdownRef.current

      // if the blur is occuring because the user clicked a suggestion, let that logic play out
      if (blurBecauseSuggestionClick) return

      // otherwise, we assume the user tabbed/clicked away from the input

      // if the date is set, but the input value is the same as the formatted date, do nothin
      if (dateTime && inputValue == dateTimeFormatFunction(dateTime)) return

      // if a date has NOT been set, but there's a valid suggestion generated from the input text, let's pick it
      if (!dateTime && suggestion) {
        setDateTime(suggestion.date)
        return
      }

      // if the input value is empty, clear the date, it was intentional
      if (inputValue.trim() === "") {
        setDateTime(undefined)
        return
      }

      if (dateTime) {
        setInputValue(dateTimeFormatFunction(dateTime))
        return
      }

      setDateTime(undefined)
      setInputValue("")
    }

    // implement basic combobox keyboard navigation, this is listening to events from the <Input/>
    function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
      if (e.key === "ArrowDown") {
        e.preventDefault()
        setSelectedIndex((prevIndex) =>
          prevIndex < computedSuggestions.length - 1
            ? prevIndex + 1
            : prevIndex,
        )
      } else if (e.key === "ArrowUp") {
        e.preventDefault()
        setSelectedIndex((prevIndex) => (prevIndex > 0 ? prevIndex - 1 : 0))
      } else if (
        e.key === "Enter" &&
        isOpen &&
        computedSuggestions.length > 0
      ) {
        // select the currently highlighted suggestion
        e.preventDefault()
        handleSelectedSuggestion(computedSuggestions[selectedIndex])
      } else if (e.key === "Escape" || e.key === "Tab") {
        // if the user navigates away from the input
        closeDropdown()
      }
    }

    /**
     * This is a simple, but carefully crafted selection function:
     *
     * - Do not update the text representation, that is handled by useEffect
     * - Meant to be used by the combobox dropdown, which is why we close the dropdown
     * - In the combobox logic, this is the only selection logic that should be used
     * - Should not be used outside the combobox logic
     */
    function handleSelectedSuggestion(sugg: Suggestion) {
      setDateTime(sugg.date)
      closeDropdown()

      // TODO it's unclear if this is necessary
      inputRef.current?.focus()
    }

    function openDropdown() {
      // suggestion is cleared in many cases (including first load)
      // if input text already exists, let's attempt to generate a suggestion from it
      if (!suggestion) {
        updateSuggestionFromInput(inputValue)
      }

      setIsOpen(true)
    }

    function closeDropdown() {
      setClosing(true)
      setSelectedIndex(0)

      setTimeout(() => {
        setIsOpen(false)
        setClosing(false)
        setSuggestion(null)
      }, 200)
    }

    // if the dateTime prop changes, update the input value. This can occur when the field date is programatically
    // updated from the parent component
    useEffect(() => {
      if (dateTime) {
        setInputValue(dateTimeFormatFunction(dateTime))
      }
    }, [dateTime, setInputValue, dateTimeFormatFunction])

    // when `setDateTime` is called via the originating `FormField` state hook, this is called twice
    // since setDateTime reference *and* inputValue change. This causes issues if we are attempting
    useEffect(() => {
      // if (!inputValue && !dateTime) {
      //   setDateTime(dateTimeFormatFunction(dateTime))
      // }
      // if there's no text input, clear the date
      // if (!inputValue) setDateTime(undefined)

      console.log("input value", inputValue)
      console.log("datet time", dateTime)

      // TODO onBlur handles most of this now, but leaving this here in case we introduced an odd bug
      // if (!isValidDateFormat(inputValue)) setDateTime(undefined)
    }, [inputValue, dateTime, setDateTime])

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
            // TODO can we do this in a way that allows for overrides
            aria-describedby={undefined}
            autoComplete={"off"}
            {...props}
            // expose the <input> element as the ref
            ref={inputRef}
            type="text"
            value={inputValue}
            onChange={handleInputChange}
            onKeyDown={handleKeyDown}
            onBlur={handleOnBlur}
            // open the dropdown when the input is focused
            onFocus={openDropdown}
            onClick={openDropdown}
          />
          <DateTimePickerPopover
            // don't display input suggestions when visually selecting the date
            onOpen={() => setSuggestion(null)}
            dateTime={dateTime}
            setDateTime={setDateTime}
            setInputValue={setInputValue}
            showTimePicker={showTimePicker}
            dateTimeFormatFunction={dateTimeFormatFunction}
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

        {/* a custom popover dropdown is implemented here */}
        {isOpen && computedSuggestions.length > 0 && (
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
              {computedSuggestions.map((sugg, index) => (
                <li
                  key={sugg.inputString}
                  role="option"
                  aria-selected={selectedIndex === index}
                  className={cn(
                    "flex cursor-pointer items-center justify-between gap-1 rounded px-2.5 py-2 text-sm",
                    index === selectedIndex &&
                      "bg-accent text-accent-foreground",
                  )}
                  onClick={() => handleSelectedSuggestion(sugg)}
                  // highlight the suggestion as the mouse hovers over it
                  onMouseEnter={() => setSelectedIndex(index)}
                >
                  <span className="xs:w-auto w-[110px] truncate">
                    {sugg.inputString}
                  </span>
                  <span className="text-muted-foreground shrink-0 text-xs">
                    {dateTimeFormatFunction(sugg.date)}
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
