// Zone classifier parameters. Mirrors app/api/zone.py's _params_from_query
// defaults; the bounds mirror what that endpoint will accept, so a bad value
// is caught here rather than coming back as a 422 the user has to decode.

export const ZONE_PARAM_DEFAULTS = {
  macro_sma_period: '200',
  fast_ema_period: '9',
  slow_ema_period: '21',
  rsi_period: '14',
  rsi_zone_a_max: '55',
  rsi_zone_b_low: '56',
  rsi_zone_b_high: '65',
  rsi_zone_c_low: '66',
  rsi_zone_c_high: '71',
  rsi_zone_d_min: '72',
  atr_period: '14',
  atr_limit_multiplier: '0.25',
  rvol_period: '20',
  near_ema_pct: '0.02',
}

export type ZoneParamsState = typeof ZONE_PARAM_DEFAULTS
export type ZoneParamKey = keyof ZoneParamsState

export interface ZoneField {
  key: ZoneParamKey
  label: string
  min: number
  max: number
  step?: string
  int?: boolean
  width?: number
}

export const ZONE_PARAM_GROUPS: { title: string; fields: ZoneField[] }[] = [
  {
    title: 'Moving averages',
    fields: [
      { key: 'macro_sma_period', label: 'Macro SMA period', min: 2, max: 400, int: true },
      { key: 'fast_ema_period', label: 'Fast EMA period', min: 2, max: 400, int: true },
      { key: 'slow_ema_period', label: 'Slow EMA period', min: 2, max: 400, int: true },
      { key: 'near_ema_pct', label: 'Near-EMA (fraction)', min: 0, max: 1, step: '0.001', width: 130 },
    ],
  },
  {
    title: 'RSI zones',
    fields: [
      { key: 'rsi_period', label: 'RSI period', min: 2, max: 100, int: true },
      { key: 'rsi_zone_a_max', label: 'Zone A max', min: 0, max: 100 },
      { key: 'rsi_zone_b_low', label: 'Zone B low', min: 0, max: 100 },
      { key: 'rsi_zone_b_high', label: 'Zone B high', min: 0, max: 100 },
      { key: 'rsi_zone_c_low', label: 'Zone C low', min: 0, max: 100 },
      { key: 'rsi_zone_c_high', label: 'Zone C high', min: 0, max: 100 },
      { key: 'rsi_zone_d_min', label: 'Zone D min', min: 0, max: 100 },
    ],
  },
  {
    title: 'ATR / volume',
    fields: [
      { key: 'atr_period', label: 'ATR period', min: 2, max: 100, int: true },
      { key: 'atr_limit_multiplier', label: 'ATR limit multiplier', min: 0, max: 10, step: '0.01', width: 130 },
      { key: 'rvol_period', label: 'RVol period', min: 2, max: 400, int: true },
    ],
  },
]

export const ZONE_FIELDS: ZoneField[] = ZONE_PARAM_GROUPS.flatMap((g) => g.fields)

export const ZONE_FIELD_LABELS = Object.fromEntries(
  ZONE_FIELDS.map((f) => [f.key, f.label]),
) as Record<ZoneParamKey, string>

export function zoneFieldLabel(key: string): string {
  return ZONE_FIELD_LABELS[key as ZoneParamKey] ?? key
}

/**
 * Returns the first problem with the parameter set, or null when it is usable.
 * Ordering matters as much as the individual ranges: overlapping RSI bands
 * silently produce nonsense zones rather than an error, so they are checked too.
 */
export function validateZoneParams(params: ZoneParamsState): string | null {
  for (const f of ZONE_FIELDS) {
    const raw = params[f.key]
    const n = Number(raw)
    if (raw.trim() === '' || !Number.isFinite(n)) return `${f.label} must be a number.`
    if (f.int && !Number.isInteger(n)) return `${f.label} must be a whole number.`
    if (n < f.min || n > f.max) return `${f.label} must be between ${f.min} and ${f.max}.`
  }

  const n = (key: ZoneParamKey) => Number(params[key])
  if (n('fast_ema_period') >= n('slow_ema_period')) return 'Fast EMA period must be shorter than the slow EMA period.'

  const bands: [string, ZoneParamKey, ZoneParamKey][] = [
    ['Zone A max', 'rsi_zone_a_max', 'rsi_zone_b_low'],
    ['Zone B low', 'rsi_zone_b_low', 'rsi_zone_b_high'],
    ['Zone B high', 'rsi_zone_b_high', 'rsi_zone_c_low'],
    ['Zone C low', 'rsi_zone_c_low', 'rsi_zone_c_high'],
    ['Zone C high', 'rsi_zone_c_high', 'rsi_zone_d_min'],
  ]
  for (const [label, lower, upper] of bands) {
    if (n(lower) > n(upper)) return `${label} must not be above ${ZONE_FIELD_LABELS[upper]}.`
  }
  return null
}
