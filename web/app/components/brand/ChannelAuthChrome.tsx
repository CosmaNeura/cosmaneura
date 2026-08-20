import { useEffect, useState } from "react"
import { useSearchParams } from "react-router"

import { BrandLogo } from "~/components/brand/BrandLogo"
import { AppFooter } from "~/components/shared/AppFooter"
import {
  type Channel,
  loginCopy,
  parseChannel,
  resolveChannel,
} from "~/lib/channel"
import { MCD_EMAIL_DOMAIN } from "~/lib/email-domain"

export function useAuthChannel() {
  const [params] = useSearchParams()
  const fromSearch = params.get("channel")
  const [channel, setChannel] = useState<Channel>(() =>
    parseChannel(fromSearch),
  )

  useEffect(() => {
    setChannel(resolveChannel(fromSearch))
  }, [fromSearch])

  return channel
}

export function ChannelAuthChrome({
  channel,
  children,
}: {
  channel: Channel
  children: React.ReactNode
}) {
  const copy = loginCopy(channel)

  return (
    <div className="bg-background flex min-h-screen flex-col">
      <header className="border-b px-4 py-5">
        <div className="mx-auto flex w-full max-w-5xl items-center justify-between">
          <BrandLogo variant="dark" size="sm" />
          <span className="text-muted-foreground text-[11px] font-semibold uppercase tracking-[0.22em]">
            {copy.kicker}
          </span>
        </div>
      </header>
      <div className="flex flex-1 flex-col items-center justify-center px-4 py-8">
        <div className="mb-8 max-w-md text-center">
          <h1
            className="text-3xl font-semibold text-[var(--brand-navy-deep)]"
            style={{ fontFamily: "'Cormorant Garamond', serif" }}
          >
            {copy.title}
          </h1>
          <p className="text-muted-foreground mt-3 text-sm leading-relaxed">
            {copy.body}
          </p>
          {channel === "mcd" ? (
            <p className="mt-3 text-sm text-[var(--brand-navy-deep)]">
              Required inbox: @{MCD_EMAIL_DOMAIN}
            </p>
          ) : null}
        </div>
        {children}
      </div>
      <AppFooter />
    </div>
  )
}

export function channelRedirect(channel: Channel) {
  return `/onboarding?channel=${channel}`
}

export function channelSibling(kind: "sign-in" | "sign-up", channel: Channel) {
  const path = kind === "sign-in" ? "/sign-up" : "/sign-in"
  return channel === "direct" ? path : `${path}?channel=${channel}`
}
