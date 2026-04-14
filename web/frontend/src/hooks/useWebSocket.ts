import { useRef, useCallback } from 'react'
import { useExecutionStore } from '../stores/executionStore'
import { usePipelineStore } from '../stores/pipelineStore'
import type { PipelineEvent } from '../types/pipeline'

export function useWebSocket() {
  const wsRef = useRef<WebSocket | null>(null)
  const addEvent = useExecutionStore((s) => s.addEvent)
  const setExecuting = useExecutionStore((s) => s.setExecuting)

  const execute = useCallback((sessionId: string, input: string, apiKey?: string) => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const ws = new WebSocket(`${protocol}//${window.location.host}/ws/execute/${sessionId}`)
    wsRef.current = ws

    // stage_params, active_strategies 수집
    const { stages, stageParams, activeStrategies } = usePipelineStore.getState()
    const disabledStages = stages.filter((s) => !s.active).map((s) => s.stage_id)
    const filteredParams: Record<string, Record<string, any>> = {}
    for (const [sid, params] of Object.entries(stageParams)) {
      if (Object.keys(params).length > 0) filteredParams[sid] = params
    }

    ws.onopen = () => {
      setExecuting(true)
      ws.send(JSON.stringify({
        type: 'execute',
        input,
        api_key: apiKey || '',
        disabled_stages: disabledStages,
        stage_params: filteredParams,
        active_strategies: activeStrategies,
      }))
    }

    ws.onmessage = (ev) => {
      try {
        const event: PipelineEvent = JSON.parse(ev.data)
        addEvent(event)
      } catch {
        // ignore parse errors
      }
    }

    ws.onerror = () => {
      setExecuting(false)
    }

    ws.onclose = () => {
      setExecuting(false)
      wsRef.current = null
    }
  }, [addEvent, setExecuting])

  const stop = useCallback(() => {
    wsRef.current?.close()
    wsRef.current = null
    setExecuting(false)
  }, [setExecuting])

  return { execute, stop }
}
