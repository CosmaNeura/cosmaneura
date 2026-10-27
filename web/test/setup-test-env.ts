// adapted from: https://johnsmilga.com/articles/2024/10/15
import { afterEach, expect } from "vitest"

import * as matchers from "@testing-library/jest-dom/matchers"
import "@testing-library/jest-dom/vitest"
import { cleanup } from "@testing-library/react"

expect.extend(matchers)

// Happy DOM eagerly creates Animation.finished even when a consumer only uses
// onfinish (as Motion does). Observe expected cancellation without changing the
// original promise: callers still receive its rejection, and other errors fail.
const animate = Element.prototype.animate
Element.prototype.animate = function (...args) {
  const animation = animate.apply(this, args)
  void animation.finished.catch((error: unknown) => {
    if (!(error instanceof DOMException && error.name === "AbortError"))
      throw error
  })
  return animation
}

afterEach(() => {
  cleanup()
})
