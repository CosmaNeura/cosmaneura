import { beforeEach, expect, it, vi } from "vitest"

import {
  TestVisitRequestExpired,
  createTestVisits,
} from "./staging-test-visits"

const post = vi.hoisted(() => vi.fn())
vi.mock("~/configuration/client", () => ({ client: { post } }))
const request = {
  request_id: "87d91f77-0fda-4e94-b06c-671b3d848200",
  visit_date: "2026-10-14",
}
const result = {
  ...request,
  created: true,
  visits: [
    {
      appointment_id: "appointment_01m4x83v1zfa4bttxs033hchjw",
      appointment_name: "Invented one",
    },
    {
      appointment_id: "appointment_01m4x7f54ffsfavptkpcfyadk2",
      appointment_name: "Invented two",
    },
  ],
}
beforeEach(() => post.mockReset())

it("uses the authenticated internal client and matching receipt", async () => {
  post.mockResolvedValue({ data: result })
  await expect(createTestVisits(request)).resolves.toEqual(result)
  expect(post).toHaveBeenCalledWith(
    expect.objectContaining({
      body: request,
      security: [{ scheme: "bearer", type: "http" }],
    }),
  )
})

it.each([
  { ...result, request_id: "87d91f77-0fda-4e94-b06c-671b3d848201" },
  { ...result, visit_date: "2026-10-13" },
  { ...result, visits: [result.visits[0], result.visits[0]] },
  { ...result, visits: [result.visits[0]] },
])("rejects an unconfirmed or mismatched receipt", async (data) => {
  post.mockResolvedValue({ data })
  await expect(createTestVisits(request)).rejects.toThrow()
})

const expired = {
  detail: {
    code: "test_visit_request_expired_without_pair",
    ...request,
  },
}

it("permits a fresh request only on exact service-confirmed expired absence", async () => {
  post.mockResolvedValue({ response: { status: 410 }, error: expired })
  await expect(createTestVisits(request)).rejects.toBeInstanceOf(
    TestVisitRequestExpired,
  )
})

it.each([
  { response: { status: 409 }, error: expired },
  { response: { status: 410 }, error: { detail: "Unconfirmed request" } },
  {
    response: { status: 410 },
    error: { detail: { ...expired.detail, visit_date: "2026-10-13" } },
  },
  {
    response: { status: 410 },
    error: {
      detail: {
        ...expired.detail,
        request_id: "87d91f77-0fda-4e94-b06c-671b3d848201",
      },
    },
  },
])(
  "keeps uncertain or mismatched failures as retained-request errors",
  async (failure) => {
    post.mockResolvedValue(failure)
    const error = await createTestVisits(request).catch((error) => error)
    expect(error).toBeInstanceOf(Error)
    expect(error).not.toBeInstanceOf(TestVisitRequestExpired)
  },
)
