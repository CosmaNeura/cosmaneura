import { Link } from "react-router"

import { SkeletonMessage } from "../skeleton-message"

function PageFooter() {
  return (
    <footer className="flex justify-center gap-6 pt-2 pb-4 text-xs">
      <Link
        to="/privacy-policy"
        className="text-muted-foreground hover:text-foreground transition-colors"
      >
        Privacy Policy
      </Link>
      <Link
        to="/terms-of-service"
        className="text-muted-foreground hover:text-foreground transition-colors"
      >
        Terms of Service
      </Link>
      <Link
        to="/support"
        className="text-muted-foreground hover:text-foreground transition-colors"
      >
        Support
      </Link>
    </footer>
  )
}

export function InnerScrollableContent({
  isLoading = false,
  children,
}: {
  isLoading?: boolean
  children: React.ReactNode
}) {
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto p-4">
      {isLoading ? (
        <SkeletonMessage />
      ) : (
        <div className="bg-card space-y-6 rounded-lg border p-6 shadow-sm">
          {children}
        </div>
      )}
      <PageFooter />
    </div>
  )
}
