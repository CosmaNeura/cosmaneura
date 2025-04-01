import { SkeletonMessage } from "../skeleton-message"

export function InnerScrollableContent({
  isLoading = false,
  children,
}: {
  isLoading?: boolean
  children: React.ReactNode
}) {
  return (
    <div className="flex flex-col gap-4 overflow-y-auto p-4">
      {isLoading ? (
        <SkeletonMessage />
      ) : (
        <div className="bg-card space-y-6 rounded-lg border p-6 shadow-sm">
          {children}
        </div>
      )}
    </div>
  )
}
