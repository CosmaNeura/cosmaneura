import { ClerkProvider } from "@clerk/clerk-react"
import { loadClerkJsScript } from "@clerk/shared/loadClerkJsScript"
import { invariant } from "@epic-web/invariant"

// required in prod and dev
const CLERK_PUBLIC_KEY = import.meta.env.VITE_CLERK_PUBLIC_KEY
invariant(CLERK_PUBLIC_KEY, "Missing Clerk Key")

/*
This whole situation isn't great. Here's what is happening:

- The ClerkProvider is a React component that wraps the entire app. It looks like it hits the Clerk API
  but should also set window.Clerk. However, the clientLoaders seem to be loaded before the providers
  (at least sometimes) so we need to load the clerk client in a way that will play well with the provider
  to avoid additional API calls to Clerk.

- There's not an easy way to do this. This insane `loadClerkJsScript` function is the only way I've found.
  `IsomorphicClerk.getOrCreateInstance(options)` is what react uses, but that is not exposed to us.

- The clerk-js package is not bundled in a way that can be split, so it greatly increases bundle size when
  used directly.

- This method is assumed to be called in a clientLoader.
*/

type ClerkUser = {
  id: string
  username?: string | null
  primaryEmailAddress?: {
    emailAddress?: string | null
  } | null
}

type ClerkClient = {
  loaded: boolean
  load: () => Promise<void>
  user?: ClerkUser
  session?: {
    getToken: () => Promise<string | null>
  }
  redirectToSignIn: () => Promise<void>
}

// Cache the in-flight bootstrap promise so concurrent callers (multiple
// clientLoaders + every API request's auth fn) share one resolution. The
// `if (!clerk.loaded)` check inside is a sequential guard — without memoizing
// at the outer call, parallel callers all observe `loaded === false` and
// each fire their own `clerk.load()`, producing 5x /v1/environment + 5x
// /v1/client per page load (Sentry N+1) and occasionally leaving the SDK in
// a state that hangs React Router hydration ("Loading application…").
let clerkClientPromise: Promise<ClerkClient | undefined> | null = null

export function getClient(): Promise<ClerkClient | undefined> {
  if (!clerkClientPromise) {
    clerkClientPromise = resolveClient()
  }
  return clerkClientPromise
}

async function resolveClient(): Promise<ClerkClient | undefined> {
  const clerkWindow = window as Window & {
    Clerk?: ClerkClient
  }

  if (!clerkWindow.Clerk) {
    // recommended officially here:
    // https://clerk.com/docs/references/sdk/frontend-only#call-window-clerk-load
    await loadClerkJsScript({
      publishableKey: CLERK_PUBLIC_KEY,
    })
  }

  invariant(clerkWindow.Clerk, "Clerk should be defined")

  const clerk = clerkWindow.Clerk

  if (!clerk.loaded) {
    await clerk.load()
  }

  // protect users from hitting the internal API if they aren't authenticated.
  // We bypass clerk.redirectToSignIn() because clerk-js is loaded before the
  // ClerkProvider mounts, so its signInUrl config is not applied and the call
  // would otherwise bounce to the hosted account portal instead of the
  // embedded /sign-in route.
  if (!clerk.user) {
    const redirectUrl = encodeURIComponent(
      window.location.pathname + window.location.search,
    )
    window.location.replace(`/sign-in?redirect_url=${redirectUrl}`)
    return
  }

  return clerk
}

export async function getUserId() {
  const client = await getClient()
  invariant(client && client.user, "client should exist")
  return client.user.id
}

export default function withClerkProvider(Component: React.ComponentType) {
  return (props: React.ComponentProps<typeof Component>) => (
    <ClerkProvider
      publishableKey={CLERK_PUBLIC_KEY}
      signInUrl="/sign-in"
      signUpUrl="/sign-up"
      signInFallbackRedirectUrl="/notes"
      signUpFallbackRedirectUrl="/notes"
    >
      <Component {...props} />
    </ClerkProvider>
  )
}
