import { MemoryRouter } from "react-router"
import { describe, expect, it } from "vitest"

import { loginCopy } from "~/lib/channel"

import {
  ChannelAuthChrome,
  isMcdHostname,
  resolveVisibleChannel,
} from "./ChannelAuthChrome"
import { render, screen } from "@testing-library/react"

describe("resolveVisibleChannel", () => {
  it("treats an explicit channel search param as authoritative", () => {
    expect(resolveVisibleChannel("mcd", "app.cosmaneura.com")).toBe("mcd")
    expect(resolveVisibleChannel("catholic", "app.mycatholicdoctor.com")).toBe(
      "catholic",
    )
    expect(resolveVisibleChannel("direct", "mycatholicdoctor.com")).toBe(
      "direct",
    )
  })

  it("treats mycatholicdoctor.com and its subdomains as MCD", () => {
    expect(resolveVisibleChannel(null, "mycatholicdoctor.com")).toBe("mcd")
    expect(resolveVisibleChannel(null, "app.mycatholicdoctor.com")).toBe("mcd")
    expect(resolveVisibleChannel("nope", "login.mycatholicdoctor.com")).toBe(
      "mcd",
    )
  })

  it("rejects lookalike hosts", () => {
    expect(isMcdHostname("evil-mycatholicdoctor.com")).toBe(false)
    expect(isMcdHostname("mycatholicdoctor.com.evil.example")).toBe(false)
    expect(resolveVisibleChannel(null, "evil-mycatholicdoctor.com")).toBe(
      "direct",
    )
    expect(resolveVisibleChannel(null, "app.cosmaneura.com")).toBe("direct")
  })
})

describe("ChannelAuthChrome", () => {
  it("shows MCD sign-in guidance above the stock Clerk controls", () => {
    const copy = loginCopy("mcd")

    render(
      <MemoryRouter>
        <ChannelAuthChrome channel="mcd">
          <div>Clerk sign-in controls</div>
        </ChannelAuthChrome>
      </MemoryRouter>,
    )

    expect(screen.getByText(copy.title)).toBeInTheDocument()
    expect(screen.getByText(copy.methodHint!)).toBeInTheDocument()
    expect(screen.getByText(/required inbox/i)).toBeInTheDocument()
    expect(screen.getByText("Clerk sign-in controls")).toBeInTheDocument()
  })

  it("keeps non-MCD chrome free of the MCD guidance", () => {
    render(
      <MemoryRouter>
        <ChannelAuthChrome channel="direct">
          <div>Clerk sign-in controls</div>
        </ChannelAuthChrome>
      </MemoryRouter>,
    )

    expect(
      screen.queryByText(/request another code when available/i),
    ).not.toBeInTheDocument()
    expect(screen.queryByText(/newest message/i)).not.toBeInTheDocument()
  })
})
