import { QueryClient } from '@tanstack/react-query'

// A moderate staleTime cuts down on redundant refetches when navigating back
// to a page you were just on (watchlists <-> stock detail, etc.) without
// risking data going stale for long -- every mutation elsewhere in the app
// explicitly invalidates the query keys it affects (see queryKeys.ts)
// rather than relying on this window to expire on its own.
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: 1,
    },
  },
})
