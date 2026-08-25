/*
The posthog JS library is a mess. The context provider has to be recreated here because of the following issues:

- https://github.com/PostHog/posthog-js/pull/1293
- https://github.com/PostHog/posthog.com/pull/9830
- https://github.com/PostHog/posthog-js/issues/908
*/
import React, { createContext, useContext, useRef } from "react"

import { posthog } from "posthog-js"

import { isDevelopment, isTesting, requireEnv } from "~/utils/environment"

type PosthogType = typeof posthog | undefined

const PosthogContext = createContext<PosthogType>(undefined)

interface PosthogProviderProps {
  children: React.ReactNode
}

type BrowserLocation = Pick<Location, "hostname" | "pathname">
type PosthogInitializer = Pick<typeof posthog, "init">

export function isPublicIntakeLocation(
  location: BrowserLocation,
  intakeChatDomain: string,
) {
  const hostname = location.hostname.trim().toLowerCase().replace(/\.$/, "")
  const configuredDomain = intakeChatDomain
    .trim()
    .toLowerCase()
    .replace(/\.$/, "")
  const pathname = location.pathname.replace(/\/+$/, "")

  return (
    hostname === configuredDomain ||
    pathname === "/chat/intake" ||
    pathname.startsWith("/chat/intake/")
  )
}

export function initializePostHogForLocation(
  location: BrowserLocation,
  client: PosthogInitializer = posthog,
) {
  if (isPublicIntakeLocation(location, requireEnv("VITE_INTAKE_CHAT_DOMAIN"))) {
    return undefined
  }

  return client.init(requireEnv("VITE_POSTHOG_KEY"), {
    api_host: requireEnv("VITE_POSTHOG_HOST"),
    person_profiles: "identified_only",

    // The clinical platform uses explicit, reviewed analytics only. Automatic
    // DOM, URL, and session capture can contain PHI or bearer credentials.
    autocapture: false,
    capture_pageview: false,
    capture_pageleave: false,
    disable_session_recording: true,
  })
}

export function PostHogProvider({ children }: PosthogProviderProps) {
  const posthogInstanceRef = useRef<PosthogType>(undefined)

  // https://react.dev/reference/react/useRef#avoiding-recreating-the-ref-contents
  // Note that in StrictMode, this will run twice.
  function getPosthogInstance() {
    if (posthogInstanceRef.current) return posthogInstanceRef.current
    if (typeof window === "undefined") return undefined

    posthogInstanceRef.current = initializePostHogForLocation(window.location)

    return posthogInstanceRef.current
  }

  return (
    <PosthogContext.Provider value={getPosthogInstance()}>
      {children}
    </PosthogContext.Provider>
  )
}

export const usePosthog = () => useContext(PosthogContext)

export default function withPostHogProvider(Component: React.ComponentType) {
  if (isDevelopment() || isTesting()) {
    return Component
  }

  return function WithPostHogProviderWrapper(
    props: React.ComponentProps<typeof Component>,
  ) {
    return (
      <PostHogProvider>
        <Component {...props} />
      </PostHogProvider>
    )
  }
}
