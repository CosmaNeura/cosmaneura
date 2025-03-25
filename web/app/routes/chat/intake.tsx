import { useEffect } from "react"
import { useParams } from "react-router"

import type { Route } from "./+types/intake"

import { IntakeThread } from "~/components/chat/intake/IntakeThread"

import { AssistantRuntimeProvider, useEdgeRuntime } from "@assistant-ui/react"

const API_BASE_URL = "https://api.cosma.localhost"
const INTAKE_PATH = "/external/chat/intake"

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

  const response = await fetch(
    `${API_BASE_URL}${INTAKE_PATH}/${intakeId}/question`,
  )

  if (!response.ok) {
    throw new Response(`Failed to load intake data: ${response.statusText}`, {
      status: response.status,
    })
  }

  return await response.json()
}

function IntakeComplete() {
  return (
    <div className="flex h-full items-center justify-center">
      <h1 className="text-2xl font-bold">Intake Complete</h1>
      <p className="mt-4 text-lg">Thank you for your responses!</p>
    </div>
  )
}

function IntakeProcessing() {
  useEffect(() => {
    const reloadTimer = setTimeout(() => {
      window.location.reload()
    }, 10000)

    // Clean up timer on unmount
    return () => clearTimeout(reloadTimer)
  }, [])

  return (
    <div className="flex h-full items-center justify-center">
      <h1 className="text-2xl font-bold">Loading...</h1>
      <p className="mt-4 text-lg">Please wait while we load your intake.</p>
    </div>
  )
}

export default function IntakeChat({ loaderData }: Route.ComponentProps) {
  const params = useParams()
  const intakeId = params.id

  const runtime = useEdgeRuntime({
    api: `${API_BASE_URL}${INTAKE_PATH}/${intakeId}/question`,
    initialMessages: [
      {
        role: "assistant",
        content: loaderData.question,
      },
    ],

    onResponse(response) {
      console.log("Response:", response)
    },
    onError: (error) => {
      console.error("Error:", error)
    },
  })

  if (loaderData.status == "complete") {
    return <IntakeComplete />
  }

  if (loaderData.status === "processing") {
    return <IntakeProcessing />
  }

  // TODO transform API shape to what assistant-ui expects
  // TODO mark as complete depending on the response
  // TODO handle errors from chat API
  // TODO make sure UI disables while loading
  // TODO test slow connection
  // TODO integration tests

  return (
    <div className="h-full">
      <AssistantRuntimeProvider runtime={runtime}>
        <IntakeThread />
      </AssistantRuntimeProvider>
    </div>
  )
}
