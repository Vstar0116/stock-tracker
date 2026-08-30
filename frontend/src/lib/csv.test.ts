import { describe, expect, it } from 'vitest'
import { toCsvText } from './csv'

// Renders a single data row on its own, so the per-cell escaping assertions
// below read as the exact bytes a spreadsheet would open. Passing no headers
// still emits a leading empty header line, so drop that first CRLF.
function row(...cells: (string | number | null)[]): string {
  return toCsvText([], [cells]).slice('\r\n'.length)
}

describe('toCsvText', () => {
  it('joins headers and rows with CRLF', () => {
    expect(toCsvText(['Symbol', 'Price'], [['TCS', 4060], ['INFY', 1835]])).toBe(
      'Symbol,Price\r\nTCS,4060\r\nINFY,1835',
    )
  })

  it('renders null as an empty field', () => {
    expect(row('TCS', null, 10)).toBe('TCS,,10')
  })

  it('quotes fields containing a comma, quote, or newline', () => {
    expect(row('Reliance, Industries')).toBe('"Reliance, Industries"')
    expect(row('line1\nline2')).toBe('"line1\nline2"')
  })

  it('escapes embedded quotes by doubling them', () => {
    expect(row('say "hi"')).toBe('"say ""hi"""')
  })

  // Security: a cell starting with one of these is executed as a formula by
  // Excel/Sheets on open. Prefixing an apostrophe makes it inert text.
  it.each(['=CMD()', '+1+1', '-1+1', '@SUM(A1)', '\tlead', '\rlead'])(
    'neutralizes formula-injection prefix %j',
    (value) => {
      expect(row(value)).toContain(`'${value}`)
    },
  )

  it('leaves an interior =, +, - or @ alone', () => {
    expect(row('A=B')).toBe('A=B')
    expect(row('Tata-Motors')).toBe('Tata-Motors')
  })

  it('does not treat a negative number as a formula', () => {
    expect(row(-1.46)).toBe('-1.46')
  })
})
