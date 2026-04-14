import { useExecutionStore } from '../../stores/executionStore'

export function ResultPanel() {
  const { isExecuting, streamingText, result, runningCostUsd } = useExecutionStore()

  if (!isExecuting && !result && !streamingText) {
    return null
  }

  return (
    <div className="border-t border-white/10 bg-bg-secondary p-4 max-h-60 overflow-y-auto">
      {/* Streaming */}
      {isExecuting && (
        <div className="flex items-center gap-2 mb-2">
          <span className="w-2 h-2 rounded-full bg-accent animate-pulse" />
          <span className="text-xs text-accent">Streaming...</span>
          {runningCostUsd > 0 && (
            <span className="text-[10px] text-white/30 ml-auto">
              ${runningCostUsd.toFixed(4)}
            </span>
          )}
        </div>
      )}

      {/* Result text */}
      {(streamingText || result?.text) && (
        <div className="text-sm text-white/80 whitespace-pre-wrap leading-relaxed">
          {result?.text || streamingText}
          {isExecuting && <span className="inline-block w-1.5 h-4 bg-accent animate-pulse ml-0.5" />}
        </div>
      )}

      {/* Error */}
      {result && !result.success && result.error && (
        <div className="mt-2 p-3 bg-red-500/10 border border-red-500/30 rounded text-red-300 text-sm">
          {result.error}
        </div>
      )}

      {/* Completion stats */}
      {result && result.success && (
        <div className="mt-3 flex items-center gap-4 text-[10px] text-white/30">
          {result.total_cost_usd !== undefined && (
            <span>${result.total_cost_usd.toFixed(4)}</span>
          )}
          {result.total_tokens && <span>{result.total_tokens.toLocaleString()} tokens</span>}
          {result.duration_ms && <span>{result.duration_ms}ms</span>}
        </div>
      )}
    </div>
  )
}
