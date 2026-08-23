// should be imported *first* before any other application logic
import "./bootstrap-desktop-capture"
import withClerkProvider from "./clerk"
import "./client"
import "./logging"
import withPostHogProvider from "./posthog"
import withSentryProvider from "./sentry"
import withTanstackQueryProvider from "./tanstack-query"

type AppComponent = React.ComponentType
type Provider = (app: AppComponent) => AppComponent

const PROVIDERS: Provider[] = [
  withPostHogProvider,
  withClerkProvider,
  withSentryProvider,
  withTanstackQueryProvider,
]

// TODO(mbianco) figure out type issue here + move to config index
export function withProviders(app: AppComponent): AppComponent {
  return PROVIDERS.reduce((acc, provider) => provider(acc), app)
}
