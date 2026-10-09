const UNITS = ["bytes", "KB", "MB", "GB", "TB"] as const

/** Human-readable size in binary units: 2_000 * 1024 * 1024 -> "2 GB". */
export function formatFileSize(bytes: number) {
  let value = bytes
  let unit = 0
  while (value >= 1024 && unit < UNITS.length - 1) {
    value /= 1024
    unit += 1
  }
  const digits = unit === 0 ? 0 : 1
  const rounded = new Intl.NumberFormat("en-US", {
    maximumFractionDigits: digits,
  }).format(value)
  return `${rounded} ${UNITS[unit]}`
}
