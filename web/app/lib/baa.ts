export const COMPANY = "Cosmaneura Inc"
export const PRODUCT = "Cosmas"

/** Canonical clickwrap BAA identity. The legal text itself is served
 * hash-verified by the backend (`GET /internal/v1/baa/current`) from
 * `app/assets/baa/` — never inline it here. */
export const BAA_DOC_ID = "CN-BAA-CUST-CW"
export const BAA_VERSION = "2.3"

export const BAA_PDF_URL = "https://cosmaneura.com/baa-v2-3.pdf"

/** Exact label pattern required by CLAUDE_BAA_IMPLEMENTATION step 3. */
export function baaCheckboxLabel(legalName: string) {
  return `I have read and agree to the HIPAA Business Associate Agreement (${BAA_DOC_ID} v${BAA_VERSION}) on behalf of ${legalName}.`
}

export function baaAuthorityLine(legalName: string) {
  return `By clicking I Agree, you represent that you are authorized to bind ${legalName} to this agreement.`
}

export const WORKFORCE_CHECKS = [
  {
    id: "workforce",
    label:
      "I am a member of this practice’s workforce (employed, contracted, or otherwise authorized).",
  },
  {
    id: "not-party",
    label: `I understand the BAA is between ${COMPANY} and the practice — not a personal contract with me.`,
  },
  {
    id: "direction",
    label: `I will use ${PRODUCT} only as directed by the practice, and only for its patients.`,
  },
  {
    id: "phi",
    label:
      "I will not capture or enter patient information unless the practice’s BAA is in force.",
  },
] as const
