import { beforeEach, describe, expect, it, vi } from "vitest"

import { queryClient } from "~/configuration/tanstack-query"

import {
  findUploadedAppointmentId,
  knownAppointmentIds,
} from "./uploaded-visit"

const noteList = vi.hoisted(() => vi.fn())
vi.mock("client/@tanstack/react-query.gen", () => ({
  noteListOptions: () => ({ queryKey: ["noteList"], queryFn: noteList }),
}))

const listing = (...ids: string[]) => ({
  appointments: ids.map((id) => ({ id })),
})

beforeEach(() => {
  queryClient.clear()
  noteList.mockReset()
})

describe("finding the uploaded visit", () => {
  it("reads the visits already listed", () => {
    expect(knownAppointmentIds()).toBeNull()
    queryClient.setQueryData(["noteList"], listing("a", "b"))
    expect(knownAppointmentIds()).toEqual(new Set(["a", "b"]))
  })

  it("returns the single visit that appeared", async () => {
    noteList.mockResolvedValue(listing("a", "b", "new"))
    await expect(findUploadedAppointmentId(new Set(["a", "b"]))).resolves.toBe(
      "new",
    )
  })

  it.each([
    ["nothing new", listing("a")],
    ["several new", listing("a", "x", "y")],
  ])("does not guess when there is %s", async (_, after) => {
    noteList.mockResolvedValue(after)
    await expect(findUploadedAppointmentId(new Set(["a"]))).resolves.toBeNull()
  })

  it("does not guess without a prior list or when the read fails", async () => {
    await expect(findUploadedAppointmentId(null)).resolves.toBeNull()
    noteList.mockRejectedValue(new Error("offline"))
    await expect(findUploadedAppointmentId(new Set(["a"]))).resolves.toBeNull()
  })
})
