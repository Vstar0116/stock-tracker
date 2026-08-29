/** Centralized query-key factory. Every useQuery/useMutation in the app
 * builds its key from here rather than inlining array literals per call
 * site -- keeps an invalidateQueries() call at one call site from silently
 * drifting out of sync with the key a query elsewhere actually used.
 * Invalidating a `.all` key (e.g. queryKeys.alerts.all) matches every key
 * nested under it (TanStack Query's default partial-match behavior), which
 * is how e.g. AlertsPage's "mark seen" mutation refreshes AppShell's
 * independently-polled unseen-count badge immediately instead of waiting
 * for its next 5-minute poll. */
export const queryKeys = {
  watchlists: {
    all: ['watchlists'] as const,
    list: () => [...queryKeys.watchlists.all, 'list'] as const,
    view: (id: number) => [...queryKeys.watchlists.all, 'view', id] as const,
  },
  instruments: {
    all: ['instruments'] as const,
    search: (q: string) => [...queryKeys.instruments.all, 'search', q] as const,
    detail: (id: number) => [...queryKeys.instruments.all, 'detail', id] as const,
    prices: (id: number, since: string) => [...queryKeys.instruments.all, 'prices', id, since] as const,
    crossover: (id: number, fast: number, slow: number, maType: string) =>
      [...queryKeys.instruments.all, 'crossover', id, fast, slow, maType] as const,
  },
  screens: {
    all: ['screens'] as const,
    list: () => [...queryKeys.screens.all, 'list'] as const,
    // `definition` is the debounced live rule-tree state, not a saved
    // screen's id -- this key changes on every meaningfully-different rule,
    // which is exactly what makes React Query treat each distinct rule as
    // its own cache entry (retyping back to a rule you already tried in
    // this session is served from cache instead of re-hitting the API).
    preview: (definition: unknown) => [...queryKeys.screens.all, 'preview', definition] as const,
  },
  alerts: {
    all: ['alerts'] as const,
    list: () => [...queryKeys.alerts.all, 'list'] as const,
    unseenCount: () => [...queryKeys.alerts.all, 'unseenCount'] as const,
  },
  status: {
    all: ['status'] as const,
    summary: () => [...queryKeys.status.all, 'summary'] as const,
    detail: () => [...queryKeys.status.all, 'detail'] as const,
  },
  portfolioReports: {
    all: ['portfolioReports'] as const,
    list: () => [...queryKeys.portfolioReports.all, 'list'] as const,
    detail: (id: number) => [...queryKeys.portfolioReports.all, 'detail', id] as const,
  },
} as const
