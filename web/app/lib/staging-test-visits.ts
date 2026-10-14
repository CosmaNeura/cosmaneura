import { z } from "zod"

import { client } from "~/configuration/client"

const receipt = z.object({
  request_id: z.string().uuid(),
  visit_date: z.string(),
  created: z.boolean(),
  visits: z.tuple([
    z.object({
      appointment_id: z.string().regex(/^appointment_[0-9a-z]{26}$/),
      appointment_name: z.string(),
    }),
    z.object({
      appointment_id: z.string().regex(/^appointment_[0-9a-z]{26}$/),
      appointment_name: z.string(),
    }),
  ]),
})

export type TestVisitRequest = { request_id: string; visit_date: string }
export type TestVisitReceipt = z.infer<typeof receipt>

export async function createTestVisits(
  request: TestVisitRequest,
): Promise<TestVisitReceipt> {
  const { data, error } = await client.post({
    url: "/internal/v1/staging/test-visits",
    body: request,
    headers: { "Content-Type": "application/json" },
    security: [{ scheme: "bearer", type: "http" }],
  })
  if (error) throw new Error("Test visits could not be confirmed")
  const result = receipt.parse(data)
  if (
    result.request_id !== request.request_id ||
    result.visit_date !== request.visit_date ||
    result.visits[0].appointment_id === result.visits[1].appointment_id
  ) {
    throw new Error("Test visit receipt does not match the retained request")
  }
  return result
}
