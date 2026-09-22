import { useEffect, useState } from "react"
import {
  Links,
  Meta,
  Outlet,
  Scripts,
  ScrollRestoration,
  isRouteErrorResponse,
  useLocation,
} from "react-router"
import type { LinksFunction } from "react-router"

import { withProviders } from "~/configuration"

import { Loader2 } from "lucide-react"

import type { Route } from "./+types/root"

import { Button } from "@/components/ui/button"
import { Toaster } from "@/components/ui/toaster"
import {
  ViewAsGlobalRouteGuard,
  ViewAsProtectedErrorGuard,
} from "~/components/ViewAsRouteGuard"
import { isBillingRecoveryRedirectError } from "~/lib/billing-recovery"
import { APP_LOADING_DELAY_COPY } from "~/lib/channel"

import "./app.css"
import PageNotFound from "./components/shared/PageNotFound"

export const links: LinksFunction = () => [
  { rel: "icon", href: "/cosmas.ico", sizes: "any" },
  {
    rel: "icon",
    href: "/cosmas-icon-64.png",
    type: "image/png",
    sizes: "64x64",
  },
  { rel: "apple-touch-icon", href: "/cosmas-icon-256.png" },
  { rel: "preconnect", href: "https://fonts.googleapis.com" },
  {
    rel: "preconnect",
    href: "https://fonts.gstatic.com",
    crossOrigin: "anonymous",
  },
  {
    rel: "stylesheet",
    href: "https://fonts.googleapis.com/css2?family=Inter:ital,opsz,wght@0,14..32,100..900;1,14..32,100..900&family=Cormorant+Garamond:wght@500;600;700&display=swap",
  },
]

export function Layout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <head>
        <meta charSet="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <Meta />
        <Links />
      </head>
      <body>
        {children}
        <ScrollRestoration />
        <Toaster />
        <Scripts />
      </body>
    </html>
  )
}

function App() {
  const { pathname } = useLocation()
  return (
    <ViewAsGlobalRouteGuard pathname={pathname}>
      <Outlet />
    </ViewAsGlobalRouteGuard>
  )
}

export const HYDRATE_FALLBACK_TIMEOUT_MS = 5_000

export function HydrateFallback() {
  const [timedOut, setTimedOut] = useState(false)

  useEffect(() => {
    const timeoutId = window.setTimeout(() => {
      setTimedOut(true)
    }, HYDRATE_FALLBACK_TIMEOUT_MS)
    return () => window.clearTimeout(timeoutId)
  }, [])

  if (timedOut) {
    return (
      <div className="flex h-screen w-screen items-center justify-center">
        <div className="space-y-4 text-center" role="status">
          <Loader2 className="text-primary mx-auto h-8 w-8 animate-spin" />
          <p className="text-foreground">{APP_LOADING_DELAY_COPY}</p>
          <p className="text-muted-foreground text-sm">
            You can keep waiting or refresh to try again.
          </p>
          <Button type="button" onClick={() => window.location.reload()}>
            Refresh
          </Button>
        </div>
      </div>
    )
  }

  return (
    <div className="flex h-screen w-screen items-center justify-center">
      <div className="space-y-4 text-center">
        <Loader2 className="text-primary mx-auto h-8 w-8 animate-spin" />
        <p className="text-muted-foreground">Loading application...</p>
      </div>
    </div>
  )
}

const AppWithProviders = withProviders(App)

// React Router 7.9+ requires route modules to export a function component.
// Keep the existing provider composition while presenting that stable shape.
export default function Root() {
  return <AppWithProviders />
}

export function ErrorBoundary({ error }: Route.ErrorBoundaryProps) {
  if (isBillingRecoveryRedirectError(error)) {
    return (
      <ViewAsProtectedErrorGuard>
        <main className="container mx-auto p-4 pt-16" role="status">
          <Loader2 className="text-primary h-6 w-6 animate-spin" />
          <p className="mt-3">Redirecting to restore practice access…</p>
        </main>
      </ViewAsProtectedErrorGuard>
    )
  }

  let message = "Oops!"
  let details = "An unexpected error occurred."
  let stack: string | undefined

  if (isRouteErrorResponse(error)) {
    if (error.status === 404) {
      return (
        <ViewAsProtectedErrorGuard>
          <PageNotFound />
        </ViewAsProtectedErrorGuard>
      )
    }

    message = "Error"
    details = error.statusText || details
  } else if (import.meta.env.DEV && error && error instanceof Error) {
    details = error.message
    stack = error.stack
  }

  return (
    <ViewAsProtectedErrorGuard>
      <main className="container mx-auto p-4 pt-16">
        <h1>{message}</h1>
        <p>{details}</p>
        {stack && (
          <pre className="w-full overflow-x-auto p-4">
            <code>{stack}</code>
          </pre>
        )}
      </main>
    </ViewAsProtectedErrorGuard>
  )
}
