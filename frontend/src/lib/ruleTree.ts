import type { Operand, ScreenRule, UiRuleGroup, UiRuleLeaf, UiRuleNode } from './types'

// Mirrors app/schemas/screen.py's field registries exactly -- a field name
// that isn't here can't be screened on server-side either.
export const NUMERIC_FIELDS = [
  'close', 'open', 'high', 'low', 'volume',
  'sma_20', 'sma_50', 'sma_100', 'sma_200', 'ema_20', 'ema_50',
  'rsi_14', 'macd', 'macd_signal', 'macd_histogram', 'atr_14', 'volume_sma_20', 'high_52w', 'low_52w',
  'pe', 'pb', 'roce', 'debt_to_equity', 'market_cap',
]
export const CATEGORICAL_FIELDS = ['sector', 'industry', 'exchange', 'series']
export const ALL_FIELDS = [...NUMERIC_FIELDS, ...CATEGORICAL_FIELDS]
export const CROSSABLE_FIELDS = new Set([
  'close', 'open', 'high', 'low', 'volume',
  'sma_20', 'sma_50', 'sma_100', 'sma_200', 'ema_20', 'ema_50',
  'rsi_14', 'macd', 'macd_signal', 'macd_histogram', 'atr_14', 'volume_sma_20', 'high_52w', 'low_52w',
])

export const FIELD_LABELS: Record<string, string> = {
  close: 'Close', open: 'Open', high: 'High', low: 'Low', volume: 'Volume',
  sma_20: 'SMA 20', sma_50: 'SMA 50', sma_100: 'SMA 100', sma_200: 'SMA 200',
  ema_20: 'EMA 20', ema_50: 'EMA 50', rsi_14: 'RSI 14', macd: 'MACD',
  macd_signal: 'MACD Signal', macd_histogram: 'MACD Hist', atr_14: 'ATR 14',
  volume_sma_20: 'Vol SMA 20', high_52w: '52W High', low_52w: '52W Low',
  pe: 'P/E', pb: 'P/B', roce: 'ROCE', debt_to_equity: 'Debt/Equity', market_cap: 'Mkt Cap',
  sector: 'Sector', industry: 'Industry', exchange: 'Exchange', series: 'Series',
}

const OPERATORS_FOR_NUMERIC = ['>', '<', '>=', '<=', '=', '!=', 'between', 'crossed above', 'crossed below']
const OPERATORS_FOR_CATEGORICAL = ['=', 'in']
export function operatorsFor(field: string): string[] {
  return CATEGORICAL_FIELDS.includes(field) ? OPERATORS_FOR_CATEGORICAL : OPERATORS_FOR_NUMERIC
}

let nextId = 1
export function newLeaf(): UiRuleLeaf {
  return { type: 'rule', field: 'close', operator: '>', value: '' }
}
export function newGroup(): UiRuleGroup {
  return { type: 'group', op: 'AND', children: [newLeaf()] }
}
export function ruleKey(): number {
  return nextId++
}

function parseOperand(value: string): Operand | null {
  const str = value.trim()
  if (!str) return null
  const yesterday = str.match(/^yesterday'?s?\s+(\w+)$/i)
  const yesterdayField = yesterday?.[1]
  if (yesterdayField && NUMERIC_FIELDS.includes(yesterdayField)) return { field: yesterdayField, when: 'yesterday' }
  const mult = str.match(/^([\d.]+)\s*x\s+(\w+)$/i)
  const multScalar = mult?.[1]
  const multField = mult?.[2]
  if (multScalar !== undefined && multField !== undefined) return { field: multField, multiplier: parseFloat(multScalar) }
  if (NUMERIC_FIELDS.includes(str)) return { field: str }
  const num = parseFloat(str)
  return isNaN(num) ? null : num
}

function opToCompareOp(op: string): 'gt' | 'gte' | 'lt' | 'lte' | 'eq' | 'ne' | null {
  switch (op) {
    case '>': return 'gt'
    case '>=': return 'gte'
    case '<': return 'lt'
    case '<=': return 'lte'
    case '=': return 'eq'
    case '!=': return 'ne'
    default: return null
  }
}

const COMPARE_OP_TO_UI: Record<string, string> = { gt: '>', gte: '>=', lt: '<', lte: '<=', eq: '=', ne: '!=' }

function operandToText(v: Operand): string {
  if (typeof v === 'number') return String(v)
  if (v.when === 'yesterday') return `yesterday's ${v.field}`
  return v.multiplier && v.multiplier !== 1 ? `${v.multiplier} x ${v.field}` : v.field
}

/** A leaf converts to a ScreenRule only once field/operator/value are all
 * filled in and the value parses -- an in-progress row (empty value, just
 * added) is skipped rather than sent to the backend half-formed. */
function leafToRule(leaf: UiRuleLeaf): ScreenRule | null {
  const { field, operator, value } = leaf
  if (operator === 'in') {
    const values = value.split(',').map((v) => v.trim()).filter(Boolean)
    return values.length > 0 ? { type: 'in', field, values } : null
  }
  if (operator === 'between') {
    const m = value.match(/([\d.]+)\s*(?:and|-|to)\s*([\d.]+)/i)
    const low = m?.[1]
    const high = m?.[2]
    if (low === undefined || high === undefined) return null
    return { type: 'between', field, low: parseFloat(low), high: parseFloat(high) }
  }
  if (operator === 'crossed above' || operator === 'crossed below') {
    const operand = parseOperand(value)
    if (operand === null) return null
    return { type: operator === 'crossed above' ? 'crossed_above' : 'crossed_below', field, value: operand }
  }
  const compareOp = opToCompareOp(operator)
  if (compareOp === null) return null
  if (compareOp === 'eq' && CATEGORICAL_FIELDS.includes(field)) {
    // Compare only accepts numeric fields server-side -- categorical equality
    // is expressed as a single-value "in" instead.
    return value.trim() ? { type: 'in', field, values: [value.trim()] } : null
  }
  const operand = parseOperand(value)
  return operand === null ? null : { type: 'compare', op: compareOp, field, value: operand }
}

/** Converts the UI tree to a backend ScreenRule, dropping incomplete leaves
 * and empty groups. Returns null if nothing in the tree is ready yet. */
export function uiTreeToScreenRule(node: UiRuleNode): ScreenRule | null {
  if (node.type === 'rule') return leafToRule(node)
  const rules = node.children.map(uiTreeToScreenRule).filter((r): r is ScreenRule => r !== null)
  return rules.length > 0 ? { type: 'group', op: node.op === 'OR' ? 'or' : 'and', rules } : null
}

/** Reverse of uiTreeToScreenRule -- converts a backend ScreenRule (e.g. from
 * the natural-language endpoint) back into the UI's editable tree shape. */
function ruleToLeaf(rule: Exclude<ScreenRule, { type: 'group' }>): UiRuleLeaf {
  switch (rule.type) {
    case 'compare':
      return { type: 'rule', field: rule.field, operator: COMPARE_OP_TO_UI[rule.op] ?? '>', value: operandToText(rule.value) }
    case 'between':
      return { type: 'rule', field: rule.field, operator: 'between', value: `${rule.low} and ${rule.high}` }
    case 'in':
      return rule.values.length === 1
        ? { type: 'rule', field: rule.field, operator: '=', value: rule.values[0] ?? '' }
        : { type: 'rule', field: rule.field, operator: 'in', value: rule.values.join(', ') }
    case 'crossed_above':
      return { type: 'rule', field: rule.field, operator: 'crossed above', value: operandToText(rule.value) }
    case 'crossed_below':
      return { type: 'rule', field: rule.field, operator: 'crossed below', value: operandToText(rule.value) }
  }
}

export function screenRuleToUiTree(rule: ScreenRule): UiRuleGroup {
  if (rule.type !== 'group') return { type: 'group', op: 'AND', children: [ruleToLeaf(rule)] }
  const children = rule.rules.map((r) => (r.type === 'group' ? screenRuleToUiTree(r) : ruleToLeaf(r)))
  return { type: 'group', op: rule.op === 'or' ? 'OR' : 'AND', children: children.length > 0 ? children : [newLeaf()] }
}

export function collectFields(node: UiRuleNode, out: Set<string> = new Set()): Set<string> {
  if (node.type === 'rule') {
    if (node.field !== 'close' && node.field !== 'sector' && node.value.trim()) out.add(node.field)
    return out
  }
  node.children.forEach((c) => collectFields(c, out))
  return out
}

/** Path-addressed mutation: path is a sequence of child indices from the
 * root group down to the target node (empty path = the root itself). */
function updateAtPath(node: UiRuleGroup, path: number[], fn: (n: UiRuleNode) => UiRuleNode): UiRuleGroup {
  if (path.length === 0) return fn(node) as UiRuleGroup
  const [i, ...rest] = path
  // `path` is always built by walking this exact tree (RuleGroup.tsx), so
  // `i` is always a valid in-bounds index into node.children -- neither of
  // these can actually be undefined/out-of-range at runtime, just not
  // something noUncheckedIndexedAccess can prove from the types alone.
  // Falling back to `node` unchanged (rather than throwing) if that
  // invariant is ever violated is a deliberate no-op, not a crash.
  if (i === undefined) return node
  const target = node.children[i]
  if (target === undefined) return node
  const children = node.children.slice()
  children[i] = rest.length === 0 ? fn(target) : updateAtPath(target as UiRuleGroup, rest, fn)
  return { ...node, children }
}

export type RuleAction = 'setOp' | 'setField' | 'setOperator' | 'setValue' | 'addRule' | 'addGroup' | 'remove'

/** Mirrors the design's mutateScreener switch -- one entry point for every
 * edit the rule builder (root or any nested group) can make. */
export function applyRuleAction(root: UiRuleGroup, path: number[], action: RuleAction, payload?: string): UiRuleGroup {
  if (action === 'remove') {
    if (path.length === 0) return root // the root group itself can't be removed
    const parentPath = path.slice(0, -1)
    const idx = path[path.length - 1]
    return updateAtPath(root, parentPath, (node) => ({
      ...(node as UiRuleGroup),
      children: (node as UiRuleGroup).children.filter((_, i) => i !== idx),
    }))
  }
  return updateAtPath(root, path, (node) => {
    switch (action) {
      case 'setOp':
        return { ...(node as UiRuleGroup), op: payload as 'AND' | 'OR' }
      case 'setField': {
        const field = payload!
        return { type: 'rule', field, operator: operatorsFor(field)[0] ?? '>', value: '' }
      }
      case 'setOperator':
        return { ...(node as UiRuleLeaf), operator: payload! }
      case 'setValue':
        return { ...(node as UiRuleLeaf), value: payload! }
      case 'addRule':
        return { ...(node as UiRuleGroup), children: [...(node as UiRuleGroup).children, newLeaf()] }
      case 'addGroup':
        return { ...(node as UiRuleGroup), children: [...(node as UiRuleGroup).children, newGroup()] }
      default:
        return node
    }
  })
}
