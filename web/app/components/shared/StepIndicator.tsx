import { cn } from "~/lib/utils"
import { Check } from "lucide-react"

interface StepIndicatorProps {
  currentStep: number
  totalSteps: number
  labels: string[]
}

export function StepIndicator({ currentStep, totalSteps, labels }: StepIndicatorProps) {
  return (
    <div className="flex items-center justify-center gap-0 w-full">
      {Array.from({ length: totalSteps }, (_, i) => {
        const stepNum = i + 1
        const isCompleted = stepNum < currentStep
        const isActive = stepNum === currentStep

        return (
          <div key={i} className="flex items-center flex-1 last:flex-none">
            <div className="flex flex-col items-center gap-3">
              <div
                className={cn(
                  "relative flex items-center justify-center size-14 rounded-full text-lg font-bold transition-all duration-500 ease-out",
                  isCompleted && "bg-primary text-primary-foreground scale-100",
                  isActive &&
                    "bg-primary text-primary-foreground scale-110 shadow-[0_0_28px_rgba(0,200,180,0.5)]",
                  !isCompleted && !isActive && "bg-secondary text-muted-foreground border border-border",
                )}
              >
                {isCompleted ? (
                  <Check className="size-6 animate-in zoom-in-50 duration-300" />
                ) : (
                  <span>{stepNum}</span>
                )}
                {isActive && (
                  <span className="absolute inset-0 rounded-full animate-ping bg-primary/25" />
                )}
              </div>
              <span
                className={cn(
                  "text-sm font-semibold transition-colors duration-300 whitespace-nowrap",
                  isActive ? "text-primary" : isCompleted ? "text-foreground" : "text-muted-foreground",
                )}
              >
                {labels[i]}
              </span>
            </div>
            {i < totalSteps - 1 && (
              <div className="flex-1 mx-4 mb-8">
                <div className="relative h-0.5 bg-border rounded-full overflow-hidden">
                  <div
                    className="absolute inset-y-0 left-0 bg-primary rounded-full transition-all duration-700 ease-out"
                    style={{ width: isCompleted ? "100%" : isActive ? "50%" : "0%" }}
                  />
                </div>
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}
