const UNITS = ["bytes", "KB", "MB", "GB", "TB"] as const

/**
 * Human-readable size in binary units. Round limits down so a stated ceiling
 * never exceeds what the server accepts: 2_000 MiB -> "1.9 GB".
 */
export function formatFileSize(
  bytes: number,
  rounding: "nearest" | "down" = "nearest",
) {
  let value = bytes
  let unit = 0
  while (value >= 1024 && unit < UNITS.length - 1) {
    value /= 1024
    unit += 1
  }
  const scale = unit === 0 ? 1 : 10
  if (rounding === "down") value = Math.floor(value * scale) / scale
  const rounded = new Intl.NumberFormat("en-US", {
    maximumFractionDigits: unit === 0 ? 0 : 1,
  }).format(value)
  return `${rounded} ${UNITS[unit]}`
}
