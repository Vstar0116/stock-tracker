import type { ReactNode } from 'react'

export interface DataTableColumn<T> {
  header: ReactNode
  align?: 'right'
  render: (row: T) => ReactNode
  /** Stops a click on this cell from bubbling to the row's onRowClick --
   * for cells containing their own interactive control (a button, a
   * select), so clicking it doesn't also trigger row navigation. */
  stopRowClick?: boolean
}

/** The `<table className="table">` shell, copy-pasted (thead/tbody/map)
 * identically in 5 page files before this. Deliberately doesn't own the
 * empty-state case -- each page's "no rows" block differs enough (a
 * bordered placeholder outside the table vs. StatusPage's spanning row
 * inside it) that forcing one shape here would be a visual change, not a
 * duplication fix; callers keep rendering their own empty state and only
 * mount this once there are rows. */
export function DataTable<T>({
  columns,
  rows,
  rowKey,
  onRowClick,
}: {
  columns: DataTableColumn<T>[]
  rows: T[]
  rowKey: (row: T) => string | number
  onRowClick?: (row: T) => void
}) {
  return (
    <table className="table">
      <thead>
        <tr>
          {columns.map((c, i) => (
            <th key={i} style={c.align === 'right' ? { textAlign: 'right' } : undefined}>
              {c.header}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={rowKey(row)} onClick={onRowClick ? () => onRowClick(row) : undefined} style={onRowClick ? { cursor: 'pointer' } : undefined}>
            {columns.map((c, i) => (
              <td
                key={i}
                onClick={c.stopRowClick ? (e) => e.stopPropagation() : undefined}
                style={c.align === 'right' ? { textAlign: 'right', fontVariantNumeric: 'tabular-nums' } : undefined}
              >
                {c.render(row)}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  )
}
