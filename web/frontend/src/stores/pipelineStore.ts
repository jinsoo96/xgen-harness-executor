import { create } from 'zustand'
import type { StageDescription } from '../types/pipeline'

interface PipelineState {
  stages: StageDescription[]
  requiredStages: Set<string>
  loading: boolean
  error: string | null

  // Config — stage_params, Strategy 선택, 동적 옵션
  stageParams: Record<string, Record<string, any>>
  activeStrategies: Record<string, string>
  dynamicOptions: Record<string, string[]>

  loadPipeline: () => Promise<void>
  toggleStage: (stageId: string) => void
  getActiveStageIds: () => string[]

  setStageParam: (stageId: string, fieldId: string, value: any) => void
  getStageParam: (stageId: string, fieldId: string) => any
  setActiveStrategy: (stageId: string, name: string) => void
  fetchDynamicOptions: (source: string) => Promise<void>
}

export const usePipelineStore = create<PipelineState>((set, get) => ({
  stages: [],
  requiredStages: new Set(),
  loading: false,
  error: null,
  stageParams: {},
  activeStrategies: {},
  dynamicOptions: {},

  loadPipeline: async () => {
    set({ loading: true, error: null })
    try {
      const res = await fetch('/api/stages')
      const data = await res.json()
      const required = new Set<string>(data.required || [])
      const stages: StageDescription[] = (data.stages || []).map((s: any) => ({
        ...s,
        active: true,
        required: required.has(s.stage_id),
      }))

      // Strategy 기본값
      const activeStrategies: Record<string, string> = {}
      for (const s of stages) {
        const def = s.strategies.find((st) => st.is_default)
        if (def) activeStrategies[s.stage_id] = def.name
      }

      set({ stages, requiredStages: required, activeStrategies, loading: false })
    } catch (e: any) {
      set({ error: e.message, loading: false })
    }
  },

  toggleStage: (stageId: string) => {
    if (get().requiredStages.has(stageId)) return
    set((state) => ({
      stages: state.stages.map((s) =>
        s.stage_id === stageId ? { ...s, active: !s.active } : s
      ),
    }))
  },

  getActiveStageIds: () => get().stages.filter((s) => s.active).map((s) => s.stage_id),

  setStageParam: (stageId, fieldId, value) => {
    const prev = get().stageParams
    set({
      stageParams: {
        ...prev,
        [stageId]: { ...(prev[stageId] || {}), [fieldId]: value },
      },
    })
  },

  getStageParam: (stageId, fieldId) => {
    return get().stageParams[stageId]?.[fieldId]
  },

  setActiveStrategy: (stageId, name) => {
    set({ activeStrategies: { ...get().activeStrategies, [stageId]: name } })
  },

  fetchDynamicOptions: async (source) => {
    if (get().dynamicOptions[source]) return
    const endpoints: Record<string, string> = {
      mcp_sessions: '/api/options/mcp-sessions',
      rag_collections: '/api/options/rag-collections',
    }
    const path = endpoints[source]
    if (!path) return
    try {
      const res = await fetch(path)
      const data = await res.json()
      const items: string[] = Array.isArray(data)
        ? data.map((d: any) => d.name || d.id || String(d))
        : data.items?.map((d: any) => d.name || d.id || String(d)) || []
      set({ dynamicOptions: { ...get().dynamicOptions, [source]: items } })
    } catch {
      set({ dynamicOptions: { ...get().dynamicOptions, [source]: [] } })
    }
  },
}))
