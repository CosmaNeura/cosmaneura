import { Link } from "react-router"

import { Button } from "@/components/ui/button"
import type { PracticeAgreementBlock } from "~/lib/practice-gate-error"

export function PracticeAgreementWall({
  block,
}: {
  block: PracticeAgreementBlock
}) {
  return (
    <div
      className="flex min-h-screen items-center justify-center p-6"
      role="alert"
    >
      <div className="max-w-lg text-center">
        <h1 className="mb-2 text-xl font-semibold">{block.headline}</h1>
        <p className="text-muted-foreground mb-3 text-sm">{block.detail}</p>
        <p className="mb-6 text-sm">{block.instruction}</p>
        <div className="flex flex-wrap items-center justify-center gap-3">
          <Button asChild>
            <Link to="/onboarding">{block.headline}</Link>
          </Button>
          <Button asChild variant="outline">
            <Link to="/settings">Open practice settings</Link>
          </Button>
        </div>
      </div>
    </div>
  )
}
