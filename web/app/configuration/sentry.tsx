import { ViewAsProtectedErrorGuard } from "~/components/ViewAsRouteGuard"
import { isVisitPrepPatientBrowser } from "~/lib/visit-prep-browser"
import {
  environmentName,
  isDevelopment,
  isProduction,
  isTesting,
  requireEnv,
} from "~/utils/environment"

import * as Sentry from "@sentry/react"
import {
  scrubSentryBreadcrumb,
  scrubSentryErrorEvent,
  scrubSentrySpan,
  scrubSentryTransaction,
  shouldCreateSentryRequestSpan,
} from "./sentry-privacy"

function SentryFallback() {
  return (
    <ViewAsProtectedErrorGuard>
      <main className="container mx-auto p-4 pt-16" role="alert">
        <h1>Page unavailable</h1>
        <p>An unexpected error occurred.</p>
      </main>
    </ViewAsProtectedErrorGuard>
  )
}

if (isProduction() && !isVisitPrepPatientBrowser()) {
  // TODO fix up react router when rr7 is supported https://docs.sentry.io/platforms/javascript/guides/react/features/react-router/
  Sentry.init({
    dsn: requireEnv("VITE_SENTRY_DSN"),

    environment: environmentName(),
    // TODO this may not be needed if we integrate sentry release, unsure
    release: requireEnv("VITE_BUILD_COMMIT"),

    integrations: [
      Sentry.browserTracingIntegration({
        shouldCreateSpanForRequest: (url) =>
          !isVisitPrepPatientBrowser() && shouldCreateSentryRequestSpan(url),
      }),
      // TODO(mbianco) we should add react-router 7 integration when it's available
      // Session Replay is intentionally disabled for the clinical app. Its
      // rrweb envelope records URL/DOM data outside the beforeSend scrubbers.
      Sentry.feedbackIntegration({
        colorScheme: "system",
        enableScreenshot: false,
        showBranding: false,
      }),
    ],

    // Set tracesSampleRate to 1.0 to capture 100%
    // of transactions for performance monitoring.
    // We recommend adjusting this value in production
    tracesSampleRate: 1.0,

    // Azure upload URLs and public intake paths carry bearer credentials. Keep
    // normal request telemetry, but prevent those credentials from entering
    // any Sentry payload shape even if SDK instrumentation changes.
    // Recheck at collection time as well: a client-side navigation can enter
    // the patient boundary after the account app initialized Sentry.
    beforeBreadcrumb: (breadcrumb) =>
      isVisitPrepPatientBrowser() ? null : scrubSentryBreadcrumb(breadcrumb),
    beforeSend: (event) =>
      isVisitPrepPatientBrowser() ? null : scrubSentryErrorEvent(event),
    beforeSendSpan: scrubSentrySpan,
    beforeSendTransaction: (event) =>
      isVisitPrepPatientBrowser() ? null : scrubSentryTransaction(event),

    // TODO(mbianco) pull domain from ENV, maybe using a python CLI to inject the domain?
    // TODO(mbianco) should pull localias as well
    // Set `tracePropagationTargets` to control for which URLs distributed tracing should be enabled
    // tracePropagationTargets: ["localhost", /^https:\/\/yourserver\.io\/api/],

    replaysSessionSampleRate: 0,
    replaysOnErrorSampleRate: 0,
  })
}

export default function withSentryProvider(Component: React.ComponentType) {
  // if this is enabled in testing, it impacts the resulting debugging output provided
  // https://discord.com/channels/621778831602221064/1307425055667519578
  // TODO should add env var for testing the sentry error reporting experience
  if (isDevelopment() || isTesting()) {
    return Component
  }

  // this seems to take precedence over the root.tsx ErrorBoundary
  // TODO maybe set Sentry.setUser here?

  return Sentry.withErrorBoundary(Component, {
    showDialog: true,
    fallback: <SentryFallback />,
  })
}
