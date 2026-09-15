import { describe, expect, it } from 'vitest'
import { validateZoneParams, ZONE_PARAM_DEFAULTS } from './zoneParams'

const withParams = (overrides: Partial<typeof ZONE_PARAM_DEFAULTS>) =>
  validateZoneParams({ ...ZONE_PARAM_DEFAULTS, ...overrides })

describe('validateZoneParams', () => {
  it('accepts the defaults', () => {
    expect(validateZoneParams(ZONE_PARAM_DEFAULTS)).toBeNull()
  })

  it.each([
    ['empty', { rsi_period: '' }],
    ['non-numeric', { rsi_period: 'fourteen' }],
    ['below min', { rsi_period: '1' }],
    ['above max', { rsi_period: '500' }],
    ['fractional where whole is required', { rsi_period: '14.5' }],
  ])('rejects a %s value', (_label, overrides) => {
    expect(withParams(overrides)).toContain('RSI period')
  })

  it('allows fractions on the fractional fields', () => {
    expect(withParams({ near_ema_pct: '0.035', atr_limit_multiplier: '1.5' })).toBeNull()
  })

  it('requires the fast EMA to be shorter than the slow EMA', () => {
    expect(withParams({ fast_ema_period: '21', slow_ema_period: '21' })).toMatch(/shorter/)
    expect(withParams({ fast_ema_period: '30', slow_ema_period: '21' })).toMatch(/shorter/)
  })

  // An overlapping band produces nonsense zone assignments rather than an
  // error, which is exactly the kind of thing nobody notices in the results.
  it('rejects overlapping RSI bands', () => {
    expect(withParams({ rsi_zone_b_low: '70' })).not.toBeNull()
    expect(withParams({ rsi_zone_c_high: '50' })).not.toBeNull()
  })

  it('accepts contiguous RSI bands', () => {
    expect(withParams({
      rsi_zone_a_max: '50', rsi_zone_b_low: '50', rsi_zone_b_high: '60',
      rsi_zone_c_low: '60', rsi_zone_c_high: '70', rsi_zone_d_min: '70',
    })).toBeNull()
  })
})
