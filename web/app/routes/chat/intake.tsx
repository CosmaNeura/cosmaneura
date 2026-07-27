import { useEffect, useState } from "react"
import { MetaFunction, useParams } from "react-router"

import type { Route } from "./+types/intake"

import { IntakeRuntimeProvider } from "~/components/chat/intake/IntakeRuntimeProvider"
import { IntakeThread } from "~/components/chat/intake/IntakeThread"
import { requireEnv } from "~/utils/environment"

import { invariant } from "@epic-web/invariant"

const VITE_PYTHON_URL = requireEnv("VITE_PYTHON_URL")

function chatEndpoint(intakeId: string) {
  return `${VITE_PYTHON_URL}/external/chat/intake/${intakeId}/question`
}

export const meta: MetaFunction = () => {
  return [
    { title: "Your Doctor's Intake" },
    {
      name: "description",
      content: "Your doctor is requesting information about your health.",
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

  const response = await fetch(chatEndpoint(intakeId))

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

function IntakeComplete() {
  return (
    <IntakeLayout>
      <p className="mt-4 text-lg font-medium">Intake Complete</p>
      <p className="mt-4">Thank you for your responses! </p>
    </IntakeLayout>
  )
}

function IntakeProcessing() {
  // reload every 10s until we are ready
  useEffect(() => {
    const reloadTimer = setTimeout(() => {
      window.location.reload()
    }, 10000)

    return () => clearTimeout(reloadTimer)
  }, [])

  return (
    <IntakeLayout>
      <h1 className="text-2xl font-bold">Loading...</h1>
      <p className="mt-4 text-lg">Please wait while we load your intake.</p>
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

  if (loaderData.status === "processing") {
    return <IntakeProcessing />
  }

  // TODO transform API shape to what assistant-ui expects https://www.assistant-ui.com/docs/runtimes/custom/external-store and https://discord.com/channels/1251324227668283443/1251324228171726889/1354153714029563935
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
