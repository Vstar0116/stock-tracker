import type { CSSProperties } from 'react'
import { ALL_FIELDS, FIELD_LABELS, operatorsFor } from '../lib/ruleTree'
import type { RuleAction } from '../lib/ruleTree'
import type { UiRuleGroup, UiRuleNode } from '../lib/types'

const TOGGLE_BASE: CSSProperties = { padding: '3px 11px', fontFamily: 'var(--font-body)', fontSize: 12, fontWeight: 600, letterSpacing: '0.03em', border: 'none', cursor: 'pointer' }
const TOGGLE_ON: CSSProperties = { ...TOGGLE_BASE, background: 'var(--color-accent-600)', color: '#fff' }
const TOGGLE_OFF: CSSProperties = { ...TOGGLE_BASE, background: 'var(--color-neutral-100)', color: 'var(--color-neutral-700)' }

interface Props {
  group: UiRuleGroup
  path: number[]
  onMutate: (path: number[], action: RuleAction, payload?: string) => void
  depth: number
}

export function RuleGroup({ group, path, onMutate, depth }: Props) {
  const isAnd = group.op !== 'OR'

  const body = (
    <>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8, flexWrap: 'wrap' }}>
        <div style={{ display: 'inline-flex', border: '1px solid var(--color-neutral-400)' }}>
          <button type="button" onClick={() => onMutate(path, 'setOp', 'AND')} style={isAnd ? TOGGLE_ON : TOGGLE_OFF}>AND</button>
          <button type="button" onClick={() => onMutate(path, 'setOp', 'OR')} style={!isAnd ? TOGGLE_ON : TOGGLE_OFF}>OR</button>
        </div>
        <span style={{ fontFamily: 'var(--font-body)', fontSize: 11, color: 'var(--color-neutral-600)', textTransform: 'uppercase', letterSpacing: '0.06em', whiteSpace: 'nowrap' }}>
          {depth === 0 ? 'Match stocks where' : 'Nested group'}
        </span>
        <div style={{ flex: 1 }} />
        <button type="button" className="btn btn-ghost" style={{ fontSize: 12, padding: '4px 10px' }} onClick={() => onMutate(path, 'addRule')}>+ Condition</button>
        {depth < 2 && (
          <button type="button" className="btn btn-ghost" style={{ fontSize: 12, padding: '4px 10px' }} onClick={() => onMutate(path, 'addGroup')}>+ Nested group</button>
        )}
        {depth > 0 && (
          <button type="button" className="btn btn-icon" title="Remove group" onClick={() => onMutate(path, 'remove')}>×</button>
        )}
      </div>

      {group.children.map((child, i) => {
        const childPath = [...path, i]
        return (
          <RuleNodeRow key={childPath.join('.')} node={child} path={childPath} onMutate={onMutate} depth={depth} />
        )
      })}
    </>
  )

  if (depth === 0) return body
  return (
    <div style={{ borderLeft: '2px solid var(--color-accent-400)', padding: '10px 0 10px 14px', margin: '6px 0', background: 'var(--color-accent-100)' }}>
      {body}
    </div>
  )
}

function RuleNodeRow({ node, path, onMutate, depth }: { node: UiRuleNode; path: number[]; onMutate: Props['onMutate']; depth: number }) {
  if (node.type === 'group') {
    return <RuleGroup group={node} path={path} onMutate={onMutate} depth={depth + 1} />
  }
  const ops = operatorsFor(node.field)
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6, margin: '5px 0', flexWrap: 'wrap' }}>
      <select className="input" value={node.field} onChange={(e) => onMutate(path, 'setField', e.target.value)} style={{ width: 150, fontSize: 13, padding: '5px 8px' }}>
        {ALL_FIELDS.map((f) => <option key={f} value={f}>{FIELD_LABELS[f] ?? f}</option>)}
      </select>
      <select className="input" value={node.operator} onChange={(e) => onMutate(path, 'setOperator', e.target.value)} style={{ width: 132, fontSize: 13, padding: '5px 8px' }}>
        {ops.map((op) => <option key={op} value={op}>{op}</option>)}
      </select>
      <input
        className="input"
        value={node.value}
        onChange={(e) => onMutate(path, 'setValue', e.target.value)}
        placeholder={node.operator === 'between' ? 'e.g. 30 and 70' : node.operator === 'in' ? 'e.g. IT, Pharma' : 'value'}
        style={{ width: 170, fontSize: 13, padding: '5px 8px' }}
      />
      <button type="button" className="btn btn-icon" title="Remove condition" onClick={() => onMutate(path, 'remove')}>×</button>
    </div>
  )
}
