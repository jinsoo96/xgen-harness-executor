export interface StrategyInfo {
  name: string
  description: string
  is_default: boolean
}

export interface StageField {
  id: string
  label: string
  type: 'select' | 'multi_select' | 'slider' | 'number' | 'toggle' | 'textarea'
  options?: string[]
  options_source?: string
  default: any
  min?: number
  max?: number
  step?: number
  placeholder?: string
  description?: string
  depends_on?: string
}

export interface StageConfig {
  description_ko: string
  description_en: string
  fields: StageField[]
  behavior: string[]
}

export interface StageDescription {
  stage_id: string
  display_name: string
  display_name_ko: string
  phase: string
  order: number
  active: boolean
  required?: boolean
  artifacts: string[]
  strategies: StrategyInfo[]
  config: StageConfig | null
}

export interface PresetInfo {
  name: string
  description: string
  stage_count: number
}

export interface PipelineEvent {
  type: string
  stage: string
  stage_id?: string
  iteration: number
  timestamp: string
  data: Record<string, unknown>
}

export interface PipelineResult {
  success: boolean
  text: string
  error?: string
  iterations?: number
  total_cost_usd?: number
  total_tokens?: number
  model?: string
  duration_ms?: number
}
