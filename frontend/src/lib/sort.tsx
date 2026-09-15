import { useMemo, useState } from 'react'

export type SortDir = 'asc' | 'desc'
export interface SortState {
  key: string
  dir: SortDir
}

function isNil(v: unknown): boolean {
  return v === null || v === undefined || v === '' || (typeof v === 'number' && isNaN(v))
}

function compare(a: unknown, b: unknown): number {
  if (typeof a === 'number' && typeof b === 'number') return a - b
  return String(a).localeCompare(String(b), 'en')
}

/** Exported for the test: nulls always sort last regardless of direction. A
 *  missing indicator is not "smaller than everything", and a column of dashes
 *  riding to the top on a desc click is never what was wanted. */
export function compareRows(a: unknown, b: unknown, dir: SortDir): number {
  const aNil = isNil(a)
  const bNil = isNil(b)
  if (aNil || bNil) return aNil && bNil ? 0 : aNil ? 1 : -1
  const r = compare(a, b)
  return dir === 'asc' ? r : -r
}

/**
 * Click-to-sort for a table. `get` maps a column key to the sortable value of
 * a row, so a column can sort on something other than what it renders (a
 * derived percentage, a raw date behind a formatted label).
 *
 * Cycles asc -> desc -> unsorted, so the original server order stays reachable.
 */
export function useSortableRows<T>(rows: T[], get: (row: T, key: string) => unknown) {
  const [sort, setSort] = useState<SortState | null>(null)

  const sorted = useMemo(() => {
    if (!sort) return rows
    return [...rows].sort((a, b) => compareRows(get(a, sort.key), get(b, sort.key), sort.dir))
  }, [rows, sort, get])

  function toggle(key: string) {
    setSort((s) => {
      if (s?.key !== key) return { key, dir: 'asc' }
      return s.dir === 'asc' ? { key, dir: 'desc' } : null
    })
  }

  return { rows: sorted, sort, toggle }
}

function SortArrow({ dir }: { dir: SortDir | null }) {
  if (dir === null) {
    return (
      <svg className="sort-arrow" width="8" height="9" viewBox="0 0 10 12" fill="currentColor" aria-hidden="true">
        <polygon points="5,0 9,4 1,4" /><polygon points="5,12 1,8 9,8" />
      </svg>
    )
  }
  return (
    <svg className="sort-arrow" width="8" height="9" viewBox="0 0 10 12" fill="currentColor" aria-hidden="true">
      {dir === 'asc' ? <polygon points="5,1 9,7 1,7" /> : <polygon points="5,11 1,5 9,5" />}
    </svg>
  )
}

export function SortableTh({
  label, sortKey, sort, onSort, numeric = false,
}: {
  label: string
  sortKey: string
  sort: SortState | null
  onSort: (key: string) => void
  numeric?: boolean
}) {
  const active = sort?.key === sortKey ? sort.dir : null
  return (
    <th
      className={numeric ? 'sortable numeric' : 'sortable'}
      aria-sort={active === 'asc' ? 'ascending' : active === 'desc' ? 'descending' : undefined}
    >
      <button type="button" onClick={() => onSort(sortKey)}>
        {label}
        <SortArrow dir={active} />
      </button>
    </th>
  )
}
