export type VisitPrepScheduleResult =
  | {
      ok: true
      scheduledAt: string
      timezoneName: string
    }
  | {
      ok: false
      reason: "ambiguous" | "invalid" | "not_future" | "timezone"
    }

type WallTime = {
  year: number
  month: number
  day: number
  hour: number
  minute: number
}

const LOCAL_DATE_TIME =
  /^(?<year>\d{4})-(?<month>\d{2})-(?<day>\d{2})T(?<hour>\d{2}):(?<minute>\d{2})$/u
const OFFSET_PROBES_IN_HOURS = [-36, -24, -12, 0, 12, 24, 36] as const
const UNUSED_HARD_PURGE_AFTER_MS = 90 * 24 * 60 * 60 * 1000
const MAX_BACKEND_INSTANT_MS = Date.UTC(9999, 11, 31, 23, 59, 59, 999)

function parseWallTime(value: string): WallTime | null {
  const match = LOCAL_DATE_TIME.exec(value)
  if (!match?.groups) return null

  const wallTime = {
    year: Number(match.groups.year),
    month: Number(match.groups.month),
    day: Number(match.groups.day),
    hour: Number(match.groups.hour),
    minute: Number(match.groups.minute),
  }
  if (
    wallTime.year < 1000 ||
    wallTime.month < 1 ||
    wallTime.month > 12 ||
    wallTime.day < 1 ||
    wallTime.day > 31 ||
    wallTime.hour > 23 ||
    wallTime.minute > 59
  ) {
    return null
  }

  const normalized = new Date(
    Date.UTC(
      wallTime.year,
      wallTime.month - 1,
      wallTime.day,
      wallTime.hour,
      wallTime.minute,
    ),
  )
  return normalized.getUTCFullYear() === wallTime.year &&
    normalized.getUTCMonth() === wallTime.month - 1 &&
    normalized.getUTCDate() === wallTime.day &&
    normalized.getUTCHours() === wallTime.hour &&
    normalized.getUTCMinutes() === wallTime.minute
    ? wallTime
    : null
}

function formatterFor(timezoneName: string): Intl.DateTimeFormat {
  return new Intl.DateTimeFormat("en-US-u-ca-gregory-nu-latn", {
    timeZone: timezoneName,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  })
}

function wallTimeAt(
  formatter: Intl.DateTimeFormat,
  instantMs: number,
): WallTime & { second: number } {
  const parts = new Map(
    formatter
      .formatToParts(new Date(instantMs))
      .filter((part) => part.type !== "literal")
      .map((part) => [part.type, Number(part.value)]),
  )
  return {
    year: parts.get("year") ?? Number.NaN,
    month: parts.get("month") ?? Number.NaN,
    day: parts.get("day") ?? Number.NaN,
    hour: parts.get("hour") ?? Number.NaN,
    minute: parts.get("minute") ?? Number.NaN,
    second: parts.get("second") ?? Number.NaN,
  }
}

function sameWallTime(
  expected: WallTime,
  actual: WallTime & { second: number },
): boolean {
  return (
    actual.year === expected.year &&
    actual.month === expected.month &&
    actual.day === expected.day &&
    actual.hour === expected.hour &&
    actual.minute === expected.minute &&
    actual.second === 0
  )
}

function offsetAt(formatter: Intl.DateTimeFormat, instantMs: number): number {
  const wall = wallTimeAt(formatter, instantMs)
  const representedAsUtc = Date.UTC(
    wall.year,
    wall.month - 1,
    wall.day,
    wall.hour,
    wall.minute,
    wall.second,
  )
  return representedAsUtc - instantMs
}

function offsetText(offsetMs: number): string {
  const totalMinutes = Math.round(offsetMs / 60_000)
  const sign = totalMinutes >= 0 ? "+" : "-"
  const absoluteMinutes = Math.abs(totalMinutes)
  const hours = Math.floor(absoluteMinutes / 60)
  const minutes = absoluteMinutes % 60
  return `${sign}${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}`
}

/**
 * Convert a datetime-local wall clock into the API's offset-bearing timestamp.
 *
 * Candidate offsets are verified through Intl so DST gaps and ambiguous
 * fall-back wall times are rejected instead of silently shifting a visit.
 */
export function visitPrepScheduleFromLocal(
  value: string,
  options: { now?: Date; timezoneName?: string } = {},
): VisitPrepScheduleResult {
  const wallTime = parseWallTime(value)
  if (!wallTime) return { ok: false, reason: "invalid" }

  const timezoneName =
    options.timezoneName ?? Intl.DateTimeFormat().resolvedOptions().timeZone
  if (!timezoneName) return { ok: false, reason: "timezone" }

  let formatter: Intl.DateTimeFormat
  try {
    formatter = formatterFor(timezoneName)
  } catch {
    return { ok: false, reason: "timezone" }
  }

  const wallAsUtc = Date.UTC(
    wallTime.year,
    wallTime.month - 1,
    wallTime.day,
    wallTime.hour,
    wallTime.minute,
  )
  const candidates = new Map<number, number>()
  for (const probeHours of OFFSET_PROBES_IN_HOURS) {
    const probe = wallAsUtc + probeHours * 60 * 60 * 1000
    const offset = offsetAt(formatter, probe)
    const instant = wallAsUtc - offset
    if (sameWallTime(wallTime, wallTimeAt(formatter, instant))) {
      candidates.set(instant, offset)
    }
  }

  if (candidates.size > 1) return { ok: false, reason: "ambiguous" }
  const firstInstant = [...candidates.keys()].sort(
    (left, right) => left - right,
  )[0]
  if (firstInstant === undefined) return { ok: false, reason: "invalid" }
  if (firstInstant <= (options.now ?? new Date()).getTime()) {
    return { ok: false, reason: "not_future" }
  }
  if (firstInstant > MAX_BACKEND_INSTANT_MS - UNUSED_HARD_PURGE_AFTER_MS) {
    return { ok: false, reason: "invalid" }
  }

  const offset = candidates.get(firstInstant)
  if (offset === undefined || offset % 60_000 !== 0) {
    return { ok: false, reason: "timezone" }
  }

  return {
    ok: true,
    scheduledAt: `${value}:00${offsetText(offset)}`,
    timezoneName,
  }
}
