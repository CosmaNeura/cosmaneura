import { ReactNode } from 'react';
import { cn } from '@/lib/utils';

interface PageTransitionProps {
  children: ReactNode;
}

export function PageTransition({ children }: PageTransitionProps) {
  const [sidebar, content] = Array.isArray(children) ? children : [null, children];

  return (
    <div className="flex h-full">
      {/* Left sidebar */}
      {sidebar && (
        <div className="w-16 flex-shrink-0">
          {sidebar}
        </div>
      )}
      
      {/* Main content area */}
      <div className="flex-1">
        <div className={cn(
          "relative flex min-h-svh flex-1 flex-col bg-background",
          "peer-data-[variant=inset]:min-h-[calc(100svh-theme(spacing.4))] md:peer-data-[variant=inset]:m-2 md:peer-data-[state=collapsed]:peer-data-[variant=inset]:ml-2 md:peer-data-[variant=inset]:ml-0 md:peer-data-[variant=inset]:rounded-xl md:peer-data-[variant=inset]:shadow"
        )}>
          {content}
        </div>
      </div>
    </div>
  );
}