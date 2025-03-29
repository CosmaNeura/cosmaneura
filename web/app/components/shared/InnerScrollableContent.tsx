import { SkeletonMessage } from "../skeleton-message"
import { FeedbackWidget } from "./FeedbackWidget"

function Content({
  isLoading = false,
  children,
}: {
  isLoading?: boolean
  children: React.ReactNode
}) {
  return (
    <div className="flex flex-col gap-4 p-4">
      {isLoading ? (
        <SkeletonMessage />
      ) : (
        <div className="translate-y-0 transform opacity-100 transition-all duration-300 ease-in-out">
          <div className="space-y-6">
            {/* TODO pretty sure I should remove all of this transition stuff... */}
            <div
              className={"transform transition-all duration-150 ease-in-out"}
            >
              <div className="bg-card rounded-lg border p-6 shadow-sm">
                {children}
              </div>
            </div>

            <div className="flex flex-col gap-4 sm:flex-row">
              <FeedbackWidget />
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
