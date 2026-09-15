export type CsvCell = string | number | null

// Prefix a leading =, +, -, @, tab, or CR with an apostrophe so a formula-like
// cell (e.g. a company name someone entered as "=CMD()") opens as inert text
// instead of running as a formula in Excel/Sheets.
function neutralizeFormula(value: string): string {
  return /^[=+\-@\t\r]/.test(value) ? `'${value}` : value
}

function escapeCell(cell: CsvCell): string {
  if (cell === null) return ''
  const raw = typeof cell === 'number' ? String(cell) : neutralizeFormula(cell)
  if (/[",\n\r]/.test(raw)) return `"${raw.replace(/"/g, '""')}"`
  return raw
}

// Split out from downloadCsv so the escaping rules above can be tested
// without a DOM -- downloadCsv itself is all Blob/anchor side effects.
export function toCsvText(headers: string[], rows: CsvCell[][]): string {
  return [headers, ...rows].map((row) => row.map(escapeCell).join(',')).join('\r\n')
}

export function downloadCsv(filename: string, headers: string[], rows: CsvCell[][]): void {
  const blob = new Blob([toCsvText(headers, rows)], { type: 'text/csv;charset=utf-8;' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  URL.revokeObjectURL(url)
}
