import { useEffect, useRef, useState } from "react"

import { useViewAsActive } from "~/components/ViewAsRouteGuard"
import {
  sameBrowserPrincipal,
  useBrowserPrincipal,
} from "~/configuration/clerk"

export function useTemplateOperationContext(doctorId: string, resource = "") {
  const { identity } = useBrowserPrincipal()
  const principal = { ...identity.current() }
  const viewAsActive = useViewAsActive()
  const observed = useRef(principal)
  const revision = useRef(0)
  const [, render] = useState(0)
  useEffect(() => {
    const update = (next: typeof principal) => {
      if (!sameBrowserPrincipal(observed.current, next)) {
        observed.current = { ...next }
        revision.current += 1
        render(revision.current)
      }
    }
    update(identity.current())
    return identity.subscribe(update)
  }, [identity])
  const key = JSON.stringify([
    principal.isLoaded,
    principal.userId,
    principal.sessionId,
    revision.current,
    doctorId,
    resource,
    viewAsActive,
  ])
  const currentKey = useRef(key)
  currentKey.current = key
  const expectedRevision = revision.current
  return {
    key,
    principal,
    available: Boolean(
      principal.isLoaded &&
        principal.userId &&
        principal.sessionId &&
        doctorId &&
        !viewAsActive,
    ),
    isCurrent: () =>
      currentKey.current === key &&
      revision.current === expectedRevision &&
      sameBrowserPrincipal(identity.current(), principal) &&
      !viewAsActive,
  }
}
export type TemplateOperationContext = ReturnType<
  typeof useTemplateOperationContext
>
