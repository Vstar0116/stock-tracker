// Mirrors app/schemas/*.py. Keep field names identical to the backend --
// these are deserialized straight from JSON, no mapping layer.

export interface Page<T> {
  items: T[]
  total: number
  limit: number
  offset: number
}

export interface UserOut {
  id: number
  email: string
  name: string
}

export interface LoginResponse {
  access_token: string
  token_type: string
  expires_in: number
}

export interface InstrumentOut {
  id: number
  symbol: string
  exchange: string
  bse_scrip_code: string | null
  company_name: string
  series: string | null
  sector: string | null
  industry: string | null
  is_active: boolean
}

export interface IndicatorOut {
  trade_date: string
  sma_20: number | null
  sma_50: number | null
  sma_100: number | null
  sma_200: number | null
  ema_20: number | null
  ema_50: number | null
  rsi_14: number | null
  macd: number | null
  macd_signal: number | null
  macd_histogram: number | null
  atr_14: number | null
  volume_sma_20: number | null
  high_52w: number | null
  low_52w: number | null
}

export interface InstrumentDetail extends InstrumentOut {
  isin: string | null
  listed_date: string | null
  latest_indicators: IndicatorOut | null
  latest_trade_date: string | null
  latest_close: number | null
  day_change_abs: number | null
  day_change_pct: number | null
  tv_symbol: string
}

export interface PriceOut {
  trade_date: string
  open: number
  high: number
  low: number
  close: number
  adjusted_close: number
  volume: number
}

export type TrendState = 'uptrend' | 'downtrend' | 'neutral' | 'unknown'

export interface WatchlistOut {
  id: number
  name: string
  created_at: string
}

export interface WatchlistViewRow {
  instrument_id: number
  symbol: string
  exchange: string
  company_name: string
  sector: string | null
  trade_date: string | null
  close: number | null
  day_change_pct: number | null
  volume: number | null
  indicators: IndicatorOut | null
  trend_state: TrendState
  notes: string | null
  added_at: string
}

export interface ScreenOut {
  id: number
  name: string
  description: string | null
  definition: Record<string, unknown>
  is_active: boolean
  created_at: string
  updated_at: string
}

export interface ScreenMatchOut {
  instrument_id: number
  symbol: string
  exchange: string
  sector: string | null
  trade_date: string
  close: number | null
  day_change_pct: number | null
  values: Record<string, number | string | null>
}

// Mirrors app/schemas/screen.py's ScreenRule discriminated union.
export type Operand = number | { field: string; multiplier?: number; when?: 'today' | 'yesterday' }

export type ScreenRule =
  | { type: 'compare'; op: 'gt' | 'gte' | 'lt' | 'lte' | 'eq' | 'ne'; field: string; value: Operand }
  | { type: 'between'; field: string; low: number; high: number }
  | { type: 'in'; field: string; values: string[] }
  | { type: 'crossed_above'; field: string; value: Operand }
  | { type: 'crossed_below'; field: string; value: Operand }
  | { type: 'group'; op: 'and' | 'or'; rules: ScreenRule[] }

// UI-side rule tree (flat field/operator/value strings), the shape the rule
// builder edits directly -- translated to/from ScreenRule at the API
// boundary (see lib/ruleTree.ts). Kept separate from ScreenRule because the
// builder needs a single generic "rule" shape while the user is mid-edit
// (an empty value, an operator not yet chosen) that wouldn't validate as a
// real ScreenRule yet.
export interface UiRuleLeaf {
  type: 'rule'
  field: string
  operator: string
  value: string
}
export interface UiRuleGroup {
  type: 'group'
  op: 'AND' | 'OR'
  children: UiRuleNode[]
}
export type UiRuleNode = UiRuleLeaf | UiRuleGroup

export interface AlertOut {
  id: number
  screen_id: number
  screen_name: string
  instrument_id: number
  symbol: string
  exchange: string
  trade_date: string
  triggered_at: string
  snapshot: Record<string, number | null>
  seen: boolean
}

export interface StatusOut {
  latest_trade_date: string | null
  expected_trade_date: string
  is_current: boolean
  last_pipeline_run_at: string | null
  last_pipeline_status: string | null
}

export interface JobRunOut {
  job_name: string
  status: string
  started_at: string
  finished_at: string | null
  duration_seconds: number | null
  rows_processed: number | null
}

export interface StatusDetailOut extends StatusOut {
  last_successful_pipeline_run_at: string | null
  instrument_count: number
  daily_price_count: number
  indicator_count: number
  recent_job_runs: JobRunOut[]
  nl_screen_configured: boolean
  nl_screen_reachable: boolean
}

export interface DownloadOut {
  exchange: 'NSE' | 'BSE'
  trade_date: string
  status: string
  rows_processed: number | null
  started_at: string
  finished_at: string | null
  error_message: string | null
}

export interface TriggerPipelineOut {
  triggered: boolean
}

// Mirrors app/schemas/crossover.py

export type MaType = 'sma' | 'ema'
export type CrossoverSignal = 'crossed_above' | 'crossed_below'
export type ScanDirection = CrossoverSignal | 'any'

export interface CrossoverPoint {
  trade_date: string
  fast: number | null
  slow: number | null
  signal: CrossoverSignal | null
}

export interface CrossoverSeriesOut {
  instrument_id: number
  fast: number
  slow: number
  ma_type: MaType
  points: CrossoverPoint[]
}

export interface ScanStats {
  evaluated: number
  matched: number
  skipped_insufficient_history: number
  skipped_stale: number
  elapsed_ms: number
  cached: boolean
}

export interface ScanMatchOut {
  instrument_id: number
  symbol: string
  exchange: string
  sector: string | null
  latest_close: number | null
  signal: CrossoverSignal
}

export interface ScanResponse {
  as_of: string
  params: { fast: number; slow: number; ma_type: MaType; direction: ScanDirection }
  stats: ScanStats
  matches: ScanMatchOut[]
}

// Mirrors app/schemas/zone.py

export type Zone = 'A' | 'B' | 'C' | 'D' | 'Unclassified' | 'Insufficient Data'

export interface ZoneOut {
  instrument_id: number
  ticker: string
  zone: Zone
  zone_label: string
  rsi: number | null
  price: number | null
  macro_sma: number | null
  fast_ema: number | null
  slow_ema: number | null
  atr_band_lower: number | null
  atr_band_upper: number | null
  rvol: number | null
  reason: string
}

export interface ZoneScanResponse {
  as_of: string
  matches: ZoneOut[]
  skipped: { instrument_id: number; ticker: string; reason: string }[]
  evaluated: number
  cached: boolean
  elapsed_ms: number
}
