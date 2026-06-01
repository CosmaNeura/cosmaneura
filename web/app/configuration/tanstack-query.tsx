import { isDevelopment } from "~/utils/environment"

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { ReactQueryDevtools } from "@tanstack/react-query-devtools"

// client must be set globally to avoid this error
// https://stackoverflow.com/questions/65590195/error-no-queryclient-set-use-queryclientprovider-to-set-one
// Exported so clientLoaders can prefetch into the same cache the components read
// from (React Query's default is a private instance per provider).
export const queryClient = new QueryClient()

// staleTime shared between the authenticated-layout loader prefetch and the
// ZoomConnectionAlert useQuery. With the QueryClient default of 0 the prefetched
// settings would be stale the instant the component mounts, so useQuery would
// refetch and we'd hit /internal/v1/settings twice on every authenticated load.
// Treating settings as fresh for a short window dedupes that pair. Explicit
// invalidateQueries (e.g. after zoom-revoked-ack) still forces a refetch.
export const SETTINGS_STALE_TIME = 60_000

// Same dedup rationale as SETTINGS_STALE_TIME but for the notes list. The
// notes-layout loader prefetches via ensureQueryData; any component that
// later mounts and calls useQuery({...noteListOptions()}) with this same
// staleTime reads the cached prefetch instead of issuing a second
// /internal/v1/notes request.
export const NOTES_STALE_TIME = 60_000

export default function withTanstackQueryProvider(
  Component: React.ComponentType,
) {
  return (props: React.ComponentProps<typeof Component>) => (
    <QueryClientProvider client={queryClient}>
      <Component {...props} />
      {isDevelopment() && (
        <ReactQueryDevtools
          buttonPosition={"top-right"}
          initialIsOpen={false}
        />
      )}
    </QueryClientProvider>
  )
}
