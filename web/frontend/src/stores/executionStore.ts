import { create } from 'zustand'
import type { PipelineEvent, PipelineResult } from '../types/pipeline'

interface ExecutionState {
  events: PipelineEvent[]
  isExecuting: boolean
  activeStage: string | null
  completedStages: Set<string>
  errorStages: Set<string>
  streamingText: string
  result: PipelineResult | null
  runningCostUsd: number
  addEvent: (event: PipelineEvent) => void
  setExecuting: (flag: boolean) => void
  reset: () => void
}

export const useExecutionStore = create<ExecutionState>((set, get) => ({
  events: [],
  isExecuting: false,
  activeStage: null,
  completedStages: new Set(),
  errorStages: new Set(),
  streamingText: '',
  result: null,
  runningCostUsd: 0,

  addEvent: (event: PipelineEvent) => {
    const state = get()
    const newEvents = [...state.events, event]
    const updates: Partial<ExecutionState> = { events: newEvents }

    switch (event.type) {
      case 'stage.enter':
        updates.activeStage = event.stage_id || event.stage
        break

      case 'stage.exit': {
        const completed = new Set(state.completedStages)
        const sid = event.stage_id || event.stage
        completed.add(sid)
        updates.completedStages = completed
        // bypass된 스테이지도 바로 완료 처리
        if (state.activeStage === sid) {
          updates.activeStage = null
        }
        break
      }

      case 'text.delta':
        updates.streamingText = state.streamingText + ((event.data?.text as string) || '')
        break

      case 'pipeline.metrics': {
        const cost = (event.data?.cost_usd as number) || 0
        updates.runningCostUsd = cost
        break
      }

      case 'pipeline.complete':
        updates.isExecuting = false
        updates.result = {
          success: (event.data?.success as boolean) ?? true,
          text: (event.data?.text as string) || state.streamingText,
          total_cost_usd: state.runningCostUsd,
        }
        break

      case 'pipeline.error': {
        const errStages = new Set(state.errorStages)
        if (event.stage) errStages.add(event.stage)
        updates.errorStages = errStages
        updates.result = {
          success: false,
          text: '',
          error: (event.data?.message as string) || 'Unknown error',
        }
        updates.isExecuting = false
        break
      }
    }

    set(updates)
  },

  setExecuting: (flag: boolean) => set({ isExecuting: flag }),

  reset: () => set({
    events: [],
    isExecuting: false,
    activeStage: null,
    completedStages: new Set(),
    errorStages: new Set(),
    streamingText: '',
    result: null,
    runningCostUsd: 0,
  }),
}))
