import { z } from "zod"

import type { TestVisitRequest } from "~/lib/staging-test-visits"

const key = "cosmaneura:staging-test-visit-request:v1"
const requestSchema = z
  .object({
    request_id: z.string().uuid(),
    visit_date: z.string().regex(/^\d{4}-\d{2}-\d{2}$/),
  })
  .strict()

/** Store only the invented test's request ID/date before any service write. */
export function retainedTestVisitRequest(): TestVisitRequest {
  const saved = sessionStorage.getItem(key)
  if (saved !== null) return requestSchema.parse(JSON.parse(saved))
  const request = {
    request_id: crypto.randomUUID(),
    visit_date: new Date().toISOString().slice(0, 10),
  }
  sessionStorage.setItem(key, JSON.stringify(request))
  return request
}

/** A fresh pair is available only after the current pair has been confirmed. */
export function newTestVisitRequest() {
  sessionStorage.removeItem(key)
}
