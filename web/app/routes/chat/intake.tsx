import { useEffect, useState } from "react"
import {
  type MetaFunction,
  isRouteErrorResponse,
  useParams,
  useRevalidator,
} from "react-router"

import { Loader2 } from "lucide-react"

import type { Route } from "./+types/intake"

import { Button } from "@/components/ui/button"
import { IntakeRuntimeProvider } from "~/components/chat/intake/IntakeRuntimeProvider"
import { IntakeThread } from "~/components/chat/intake/IntakeThread"
import { AnimatedCheckmark } from "~/components/shared/AnimatedCheckmark"
import { requireEnv } from "~/utils/environment"

import { invariant } from "@epic-web/invariant"

const VITE_PYTHON_URL = requireEnv("VITE_PYTHON_URL")

function chatEndpoint(intakeId: string) {
  return `${VITE_PYTHON_URL}/external/chat/intake/${intakeId}/question`
}

export const meta: MetaFunction = () => {
  return [
    { title: "Your visit intake" },
    {
      name: "description",
      content: "Your provider would like a few details before your visit.",
    },
  ]
}

/**
 * Unlike other endpoints, this is not authenticated *and* does not require an api key. We could generate another openapi
 * file for this part of the system and have another client library, but that's overkill, especially as we figure out what this feature really is
 */
export async function clientLoader(loaderArgs: Route.ClientLoaderArgs) {
  const intakeId = loaderArgs.params.id

  if (!intakeId) {
    throw new Response("Intake ID is required", { status: 400 })
  }

  const response = await fetch(chatEndpoint(intakeId), {
    // The chat UI is served from a sibling origin. Include the signed platform
    // session when one exists so the server can enforce active view-as state;
    // ordinary patient browsers simply send no platform session cookie.
    credentials: "include",
  })

  if (!response.ok) {
    throw new Response(`Failed to load intake data: ${response.statusText}`, {
      status: response.status,
    })
  }

  return await response.json()
}

function IntakeLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="h-full">
      <div className="bg-background box-border flex h-full flex-col overflow-hidden">
        <div className="flex h-full flex-col items-center overflow-y-scroll scroll-smooth bg-inherit px-4 pt-8">
          <div className="flex w-full flex-grow flex-col">
            <div className="flex w-full flex-grow flex-col items-center justify-center">
              {children}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

export function IntakeComplete() {
  return (
    <IntakeLayout>
      <div className="flex max-w-md flex-col items-center gap-4 text-center">
        <AnimatedCheckmark />
        <h1 className="mt-2 text-2xl font-semibold">Thank you</h1>
        <p className="text-muted-foreground">
          Your answers are on their way to your provider. You can close this
          page.
        </p>
      </div>
    </IntakeLayout>
  )
}

export const INTAKE_PROCESSING_POLL_MS = 10_000

export function IntakeProcessing() {
  const revalidator = useRevalidator()

  // Re-run the loader rather than reloading the page; the timer restarts
  // after each check and stops once the questions are ready.
  useEffect(() => {
    if (revalidator.state !== "idle") return
    const timer = setTimeout(
      () => void revalidator.revalidate(),
      INTAKE_PROCESSING_POLL_MS,
    )
    return () => clearTimeout(timer)
  }, [revalidator])

  return (
    <IntakeLayout>
      <div
        role="status"
        className="flex max-w-md flex-col items-center gap-4 text-center"
      >
        <Loader2
          className="text-muted-foreground size-8 motion-safe:animate-spin"
          aria-hidden="true"
        />
        <h1 className="text-2xl font-semibold">Preparing your questions…</h1>
        <p className="text-muted-foreground">
          This usually takes less than a minute. You can keep this page open.
        </p>
      </div>
    </IntakeLayout>
  )
}

// Patients reach this page from a link, so failures explain what to do next
// without technical detail or any route into the clinician app.
export function ErrorBoundary({ error }: Route.ErrorBoundaryProps) {
  const deadLink = isRouteErrorResponse(error) && error.status === 404
  return (
    <IntakeLayout>
      <div className="flex max-w-md flex-col items-center gap-3 text-center">
        {deadLink ? (
          <>
            <h1 className="text-2xl font-semibold">
              This link isn&apos;t working
            </h1>
            <p className="text-muted-foreground">
              It may have expired. Please contact your provider&apos;s office
              for a new link.
            </p>
          </>
        ) : (
          <>
            <h1 className="text-2xl font-semibold">
              We couldn&apos;t load your questions
            </h1>
            <p className="text-muted-foreground">
              This is usually a brief connection problem. Please try again.
            </p>
            <Button
              type="button"
              className="mt-2"
              onClick={() => window.location.reload()}
            >
              Try again
            </Button>
          </>
        )}
      </div>
    </IntakeLayout>
  )
}

export default function IntakeChat({ loaderData }: Route.ComponentProps) {
  // hook to disable the page once intake is finished
  const [isFinished, setIsFinished] = useState(false)

  const params = useParams()
  const intakeId = params.id

  invariant(intakeId, "Intake ID is required")

  if (loaderData.status == "completed" || isFinished) {
    return <IntakeComplete />
  }

  // While the intake is still being prepared the API answers "in_progress"
  // with no question yet, rather than "processing".
  if (loaderData.status === "processing" || !loaderData.question) {
    return <IntakeProcessing />
  }

  // TODO transform API shape to what assistant-ui expects https://www.assistant-ui.com/docs/runtimes/custom/external-store and https://discord.com/channels/1251324227668283443/1251324228171726889/1354153714029563935
  // TODO mark as complete depending on the response
  // TODO handle errors from chat API
  // TODO make sure UI disables while loading
  // TODO test slow connection
  // TODO integration tests

  return (
    <div className="h-full">
      <IntakeRuntimeProvider
        setIsFinished={setIsFinished}
        api={chatEndpoint(intakeId)}
        initialMessages={[
          {
            role: "assistant",
            content: loaderData.question,
          },
        ]}
      >
        <IntakeThread />
      </IntakeRuntimeProvider>
    </div>
  )
}
