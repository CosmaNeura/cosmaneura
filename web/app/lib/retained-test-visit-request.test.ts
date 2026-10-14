import { beforeEach, expect, it } from "vitest"

import {
  newTestVisitRequest,
  retainedTestVisitRequest,
} from "./retained-test-visit-request"

beforeEach(() => sessionStorage.clear())

it("recovers the request from storage after a component remount", () => {
  const first = retainedTestVisitRequest()
  expect(retainedTestVisitRequest()).toEqual(first)
  expect(JSON.parse(sessionStorage.getItem(sessionStorage.key(0)!)!)).toEqual(
    first,
  )
})

it("starts a new request only on explicit reset", () => {
  const first = retainedTestVisitRequest()
  newTestVisitRequest()
  expect(retainedTestVisitRequest().request_id).not.toEqual(first.request_id)
})

it("refuses a corrupt retained request instead of replacing it", () => {
  retainedTestVisitRequest()
  const key = sessionStorage.key(0)!
  sessionStorage.setItem(key, "{broken")
  expect(() => retainedTestVisitRequest()).toThrow()
  expect(sessionStorage.getItem(key)).toBe("{broken")
})
