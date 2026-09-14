import type { UiRuleGroup } from './types'

export interface ScreenTemplate {
  id: string
  name: string
  description: string
  root: UiRuleGroup
}

export const TEMPLATES: ScreenTemplate[] = [
  {
    id: 'above-200dma', name: 'Above 200 DMA', description: 'Close above the 200-day moving average',
    root: { type: 'group', op: 'AND', children: [{ type: 'rule', field: 'close', operator: '>', value: 'sma_200' }] },
  },
  {
    id: 'golden-cross', name: 'Golden Cross', description: 'SMA 50 just crossed above SMA 200',
    root: { type: 'group', op: 'AND', children: [{ type: 'rule', field: 'sma_50', operator: 'crossed above', value: 'sma_200' }] },
  },
  {
    id: 'volume-breakout', name: 'Volume Breakout x3', description: 'Volume at least 3x its 20-day average',
    root: { type: 'group', op: 'AND', children: [{ type: 'rule', field: 'volume', operator: '>', value: '3 x volume_sma_20' }] },
  },
]
