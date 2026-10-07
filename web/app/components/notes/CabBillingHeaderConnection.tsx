import { useCallback, useEffect, useRef, useState } from "react"

import { Button } from "@/components/ui/button"
import {
  type BillingHeaderSource,
  billingContinuationAuth,
  billingHeaderCab,
  billingHeaderGenerationContinue,
  billingHeaderOperation,
} from "~/configuration/client"
import {
  type CabContinueRequest,
  type CabRequest,
  type CabSource,
  sameCabValue,
} from "~/lib/cab-provider-header"
import { withReadDeadline } from "~/lib/read-deadline"

import { CabBillingHeaderPanel } from "./CabBillingHeaderPanel"

export function CabBillingHeaderConnection({
  operationId,
  source,
  onLocked,
  sourceStale = false,
  onUpdateVisit,
}: {
  operationId: string
  source: BillingHeaderSource["source"]
  onLocked: (locked: boolean) => void
  sourceStale?: boolean
  onUpdateVisit?: (visit: CabSource["visitContext"]) => void
}) {
  const [state, setState] = useState<
    "loading" | "configured" | "not_configured" | "error"
  >("loading")
  const [asyncEnabled, setAsyncEnabled] = useState(false)
  const [continuationEnabled, setContinuationEnabled] = useState(false)
  const [attempt, setAttempt] = useState(0)
  const active = useRef(true)
  const continuationAbort = useRef<AbortController | null>(null)
  const [scope] = useState(() => ({
    operationId,
    source: structuredClone(source),
  }))
  const revoked = useRef(false)
  revoked.current ||=
    sourceStale ||
    operationId !== scope.operationId ||
    !sameCabValue(source, scope.source)
  useEffect(() => {
    active.current = true
    if (revoked.current) return
    const controller = new AbortController()
    setState("loading")
    void (async () => {
      try {
        const response = await withReadDeadline(
          (signal) =>
            billingHeaderOperation({
              path: { operation_id: scope.operationId },
              signal,
              throwOnError: false,
            }),
          { signal: controller.signal },
        )
        const receipt = response.data
        if (
          !response.response?.ok ||
          !receipt ||
          receipt.schemaVersion !== "core.billing-header-operation.v1" ||
          receipt.operationId !== scope.operationId ||
          receipt.state !== "prepared" ||
          !sameCabValue(receipt.source, scope.source) ||
          !["configured", "not_configured"].includes(
            receipt.transport?.state ?? "",
          )
        )
          throw new Error("Invalid operation")
        if (active.current && !controller.signal.aborted && !revoked.current) {
          setAsyncEnabled(receipt.transport?.asyncGeneration === true)
          setContinuationEnabled(
            receipt.transport?.asyncGeneration === true &&
              receipt.transport?.generationContinuation === true,
          )
          setState(receipt.transport!.state as "configured" | "not_configured")
        }
      } catch {
        if (active.current && !controller.signal.aborted && !revoked.current)
          setState("error")
      }
    })()
    return () => {
      active.current = false
      continuationAbort.current?.abort()
      controller.abort()
    }
  }, [scope, attempt])

  const request = useCallback<CabRequest>(
    async (action, command) => {
      const current = () => {
        if (!active.current || revoked.current)
          throw Object.assign(new Error("Billing source changed"), {
            status: 409,
          })
      }
      current()
      const response = await withReadDeadline((signal) =>
        billingHeaderCab({
          path: { operation_id: scope.operationId },
          body: { action, command },
          signal,
          throwOnError: false,
        }),
      )
      current()
      if (!response.response?.ok)
        throw Object.assign(new Error("Billing request unavailable"), {
          status: response.response?.status,
        })
      return response.data
    },
    [scope],
  )

  useEffect(() => {
    if (revoked.current) continuationAbort.current?.abort()
  }, [sourceStale, operationId, source])

  const continueGeneration = useCallback<CabContinueRequest>(
    async (command, signal) => {
      if (!active.current || revoked.current || signal?.aborted)
        throw Object.assign(new Error("Billing source changed"), {
          status: 409,
        })
      const controller = new AbortController()
      continuationAbort.current = controller
      let response
      try {
        response = await withReadDeadline(
          (signal) =>
            billingHeaderGenerationContinue({
              auth: billingContinuationAuth,
              path: { operation_id: scope.operationId },
              body: { purpose: "header.generation-continue", command },
              signal,
              throwOnError: false,
            }),
          {
            signal: AbortSignal.any([
              controller.signal,
              ...(signal ? [signal] : []),
            ]),
          },
        )
      } finally {
        if (continuationAbort.current === controller)
          continuationAbort.current = null
      }
      if (!active.current || revoked.current || signal?.aborted)
        throw Object.assign(new Error("Billing source changed"), {
          status: 409,
        })
      if (!response.response?.ok)
        throw Object.assign(new Error("Continuation unavailable"), {
          status: response.response?.status,
        })
      return response.data
    },
    [scope],
  )

  if (state === "configured")
    return (
      <CabBillingHeaderPanel
        key={scope.operationId}
        operationId={scope.operationId}
        expectedSource={scope.source}
        sourceStale={revoked.current}
        request={request}
        asyncEnabled={asyncEnabled}
        continuationEnabled={continuationEnabled}
        continueGeneration={continueGeneration}
        onLocked={onLocked}
        onUpdateVisit={onUpdateVisit}
      />
    )
  if (revoked.current)
    return (
      <p role="alert" className="text-sm">
        The saved billing source is no longer current. Reload the note before
        continuing.
      </p>
    )
  if (state === "loading")
    return (
      <p role="status" className="text-muted-foreground text-sm">
        Opening billing header…
      </p>
    )
  if (state === "error")
    return (
      <div role="alert" className="space-y-2 text-sm">
        <p>
          The billing connection could not be confirmed. Your saved note
          confirmation is retained.
        </p>
        <Button
          type="button"
          variant="outline"
          onClick={() => setAttempt((current) => current + 1)}
        >
          Retry connection
        </Button>
      </div>
    )
  return (
    <div role="status" className="bg-muted space-y-2 rounded-md p-4 text-sm">
      <p className="font-medium">Billing confirmation saved</p>
      <p>
        The billing connection is not configured yet. A header has not been
        generated.
      </p>
    </div>
  )
}
