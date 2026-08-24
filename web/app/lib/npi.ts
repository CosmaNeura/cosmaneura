/** Validate a 10-digit NPI with the CMS Luhn check-digit rule. */
export function isValidNpi(value: string): boolean {
  if (!/^[12]\d{9}$/.test(value)) return false

  let total = 24
  for (const [index, character] of [...value.slice(0, 9)].entries()) {
    const digit = Number(character)
    const product = digit * (index % 2 === 0 ? 2 : 1)
    total += Math.floor(product / 10) + (product % 10)
  }

  const expectedCheckDigit = (10 - (total % 10)) % 10
  return Number(value.at(-1)) === expectedCheckDigit
}
