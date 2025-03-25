// TODO broken on RR7
// https://discord.com/channels/1251324227668283443/1251324228171726889/1341403438151503994
import { settingsData } from "client"

import type { Route } from "./+types/intake"

import { IntakeThread } from "~/components/chat/intake/IntakeThread"

import { AssistantRuntimeProvider, useEdgeRuntime } from "@assistant-ui/react"
import { invariant } from "@epic-web/invariant"
import type { Route } from "../+types/settings"
import { useParams } from "react-router"

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
    `https://api.cosma.localhost/external/chat/intake/${intakeId}/question`,
  )

  if (!response.ok) {
    throw new Response(`Failed to load intake data: ${response.statusText}`, {
      status: response.status,
    })
  }

  return await response.json()
}

export default function IntakeChat({ loaderData }: Route.ComponentProps) {
  const params = useParams();
  const intakeId = params.id;

  const runtime = useEdgeRuntime({
    api: `https://api.cosma.localhost/external/chat/intake/${intakeId}/question`,
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
    // unstable_AISDKInterop: true,
  })

  if (loaderData.complete) {
    // tell the user that the intake is complete
    return (
      <div className="flex h-full items-center justify-center">
        <h1 className="text-2xl font-bold">Intake Complete</h1>
        <p className="mt-4 text-lg">Thank you for your responses!</p>
      </div>
    )
  }

  // TODO handle errors
  // TODO load first message
  // TODO handle invalid auth
  // TODO add intake ID verification to backend

  return (
    <div className="h-full">
      <AssistantRuntimeProvider runtime={runtime}>
        <IntakeThread />
      </AssistantRuntimeProvider>
    </div>
  )
}
