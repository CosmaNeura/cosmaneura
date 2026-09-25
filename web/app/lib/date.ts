// helpers for date & time formatting
import { formatDuration, intervalToDuration, parseISO } from "date-fns"

/** Appointment datetimes without an offset come from the API's UTC column. */
export function appointmentDateFormat(value: string) {
  const date = /^\d{4}-\d{2}-\d{2}$/.test(value)
    ? parseISO(value)
    : new Date(/(?:[zZ]|[+-]\d{2}:?\d{2})$/.test(value) ? value : `${value}Z`)
  return date.toLocaleString("en-US", { dateStyle: "short" })
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
