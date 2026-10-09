// helpers for date & time formatting
import { formatDuration, intervalToDuration, parseISO } from "date-fns"

/** Appointment datetimes without an offset come from the API's UTC column. */
export function parseAppointmentDate(value: string) {
  return /^\d{4}-\d{2}-\d{2}$/.test(value)
    ? parseISO(value)
    : new Date(/(?:[zZ]|[+-]\d{2}:?\d{2})$/.test(value) ? value : `${value}Z`)
}

export function appointmentDateFormat(value: string) {
  return parseAppointmentDate(value).toLocaleString("en-US", {
    dateStyle: "short",
  })
}

const DAY_MS = 24 * 60 * 60 * 1000

function startOfLocalDay(date: Date) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate())
}

// Whole calendar days from `now` to `date` in local time; negative is past.
// Rounding absorbs the 23- and 25-hour days around DST changes.
export function calendarDayOffset(date: Date, now: Date = new Date()) {
  return Math.round(
    (startOfLocalDay(date).getTime() - startOfLocalDay(now).getTime()) / DAY_MS,
  )
}

/**
 * "Today", "Yesterday", "Tomorrow", a weekday within the past week, otherwise
 * a short date (with the year only when it differs from this year).
 */
export function relativeDayLabel(date: Date, now: Date = new Date()) {
  const offset = calendarDayOffset(date, now)
  if (offset === 0) return "Today"
  if (offset === -1) return "Yesterday"
  if (offset === 1) return "Tomorrow"
  if (offset < 0 && offset > -7) {
    return date.toLocaleDateString("en-US", { weekday: "long" })
  }
  return date.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    ...(date.getFullYear() === now.getFullYear() ? {} : { year: "numeric" }),
  })
}

/** True when `date` falls in the Sunday-to-Saturday week containing `now`. */
export function isInCurrentWeek(date: Date, now: Date = new Date()) {
  const offset = calendarDayOffset(date, now)
  const dayOfWeek = now.getDay()
  return offset >= -dayOfWeek && offset <= 6 - dayOfWeek
}

export function timeOfDay(date: Date) {
  return date.toLocaleTimeString("en-US", {
    hour: "numeric",
    minute: "2-digit",
  })
}

/** "Today · 2:30 PM", "Tomorrow · 9:00 AM", "Mon, Oct 12 · 1:00 PM". */
export function meetingTimeLabel(date: Date, now: Date = new Date()) {
  const offset = calendarDayOffset(date, now)
  const day =
    Math.abs(offset) <= 1
      ? relativeDayLabel(date, now)
      : date.toLocaleDateString("en-US", {
          weekday: "short",
          month: "short",
          day: "numeric",
          ...(date.getFullYear() === now.getFullYear()
            ? {}
            : { year: "numeric" }),
        })
  return `${day} · ${timeOfDay(date)}`
}

export function secondsToMinutes(seconds?: number | undefined) {
  if (!seconds) return "0 minutes"

  return formatDuration(intervalToDuration({ start: 0, end: seconds * 1000 }), {
    format: ["minutes"],
  })
}

export function standardDateTimeFormat(date: string) {
  return new Date(date).toLocaleString("en-US", {
    dateStyle: "short",
    timeStyle: "short",
  })
}

export function standardDateFormat(date: string) {
  return new Date(date).toLocaleString("en-US", {
    dateStyle: "short",
  })
}
