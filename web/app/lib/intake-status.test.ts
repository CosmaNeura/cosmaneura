import { describe, expect, it } from "vitest"

import { intakeStatusBadge } from "./intake-status"

describe("intake status badge", () => {
  it.each([
    ["complete", "completed", "Completed"],
    ["processing", "in-progress", "Processing"],
    ["pending", "not-started", "Awaiting client"],
    ["none", "not-started", "Not sent"],
  ] as const)("describes %s as %s / %s", (status, badge, label) => {
    expect(intakeStatusBadge(status, { patient: "client" })).toEqual({
      status: badge,
      label,
    })
  })
})
