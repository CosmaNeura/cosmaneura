export const BAA_VERSION = "2026-08-19-ux-1"
export const COMPANY = "Cosmaneura Inc"
export const PRODUCT = "Cosmas"

export type BaaSection = {
  num: string
  title: string
  body: string
}

export function baaSections(coveredEntity: string): BaaSection[] {
  const ce = coveredEntity.trim() || "the Covered Entity"
  return [
    {
      num: "1",
      title: "Parties",
      body: `This Business Associate Agreement (“BAA”) is between ${COMPANY}, a Delaware corporation (“Business Associate”), and ${ce} (“Covered Entity”). ${PRODUCT} is the service operated by ${COMPANY}. This BAA is the agreement under which ${COMPANY} may create, receive, maintain, or transmit Protected Health Information (“PHI”) on behalf of Covered Entity.`,
    },
    {
      num: "2",
      title: "One agreement for the practice",
      body: `This BAA is with the practice, not with each clinician. Covered Entity’s workforce — employed or contracted physicians, nurses, medical assistants, billers, and other staff authorized by Covered Entity — may use ${PRODUCT} under this BAA. Invited workforce members attest to that authorization; they do not execute a separate BAA. If Covered Entity later adds providers, this BAA continues to apply unless terminated.`,
    },
    {
      num: "3",
      title: "Permitted uses and disclosures",
      body: `${COMPANY} may use and disclose PHI only to provide ${PRODUCT} to Covered Entity (including ambient capture, draft clinical documentation, coding suggestions, intake, and related administrative workflows), for ${COMPANY}’s proper management and administration, to fulfill legal duties, and for data aggregation relating to Covered Entity’s health care operations, each as permitted by 45 C.F.R. § 164.504(e) and this BAA. ${COMPANY} will not use PHI for marketing or sell PHI.`,
    },
    {
      num: "4",
      title: "Safeguards",
      body: `${COMPANY} will implement administrative, physical, and technical safeguards that reasonably and appropriately protect the confidentiality, integrity, and availability of electronic PHI as required by the HIPAA Security Rule, and will ensure that any subcontractors that create, receive, maintain, or transmit PHI on ${COMPANY}’s behalf agree to the same restrictions and conditions.`,
    },
    {
      num: "5",
      title: "Minimum necessary and fail-closed processing",
      body: `${COMPANY} will not process PHI through ${PRODUCT} for Covered Entity until this BAA is accepted. After acceptance, ${COMPANY} will limit PHI to the minimum necessary for the requested workflow. ${PRODUCT} does not write autonomously to an EHR; a human remains the transmitting actor for clinical documentation and financial submissions.`,
    },
    {
      num: "6",
      title: "Breach and incident notice",
      body: `${COMPANY} will report to Covered Entity any use or disclosure of PHI not provided for by this BAA, and any Security Incident or Breach of Unsecured PHI, without unreasonable delay and in no case later than sixty (60) calendar days after discovery, with the information required by 45 C.F.R. § 164.410.`,
    },
    {
      num: "7",
      title: "Access, amendment, and accounting",
      body: `To the extent ${COMPANY} maintains PHI in a Designated Record Set, ${COMPANY} will make that PHI available to Covered Entity to satisfy Covered Entity’s obligations under 45 C.F.R. §§ 164.524, 164.526, and 164.528, and will make its internal practices, books, and records relating to PHI available to the Secretary of HHS for determining compliance.`,
    },
    {
      num: "8",
      title: "Subcontractors",
      body: `${COMPANY} may engage subcontractors (including cloud infrastructure and transcription processors) that handle PHI only under written agreements requiring HIPAA-equivalent protections. A current list of subprocessors is available on request at info@cosmaneura.com.`,
    },
    {
      num: "9",
      title: "Term and termination",
      body: `This BAA is effective on the date Covered Entity accepts it in ${PRODUCT} and continues until terminated. Either party may terminate for material breach if the breach is not cured within thirty (30) days of written notice. Upon termination, ${COMPANY} will return or destroy PHI, or if return or destruction is infeasible, will continue to protect it and limit further uses to those that make return or destruction infeasible.`,
    },
    {
      num: "10",
      title: "No medical advice; draft status of this text",
      body: `${PRODUCT} is a documentation and workflow assistant. It is not a medical device and does not practice medicine. This clickwrap text is a product-UX draft for onboarding. Counsel for ${COMPANY} and for Covered Entity must review and ratify a production BAA before this language is treated as the operative legal instrument.`,
    },
  ]
}

export const OWNER_CHECKS = [
  {
    id: "authority",
    label:
      "I have authority to bind this practice as a HIPAA covered entity (or as a business associate of one).",
  },
  {
    id: "read",
    label: `I have read the ${PRODUCT} Business Associate Agreement with ${COMPANY}.`,
  },
  {
    id: "accept",
    label: `On behalf of the practice named above, I accept this BAA. ${COMPANY} will act as our business associate for ${PRODUCT}.`,
  },
  {
    id: "phi",
    label: `I will not introduce patient information into ${PRODUCT} except under this BAA.`,
  },
] as const

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
