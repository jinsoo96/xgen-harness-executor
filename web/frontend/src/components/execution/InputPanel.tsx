import { useState, useRef, useCallback } from 'react'
import { useExecutionStore } from '../../stores/executionStore'
import { useWebSocket } from '../../hooks/useWebSocket'

export function InputPanel() {
  const [input, setInput] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [sessionId, setSessionId] = useState('')
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  const { isExecuting, reset } = useExecutionStore()
  const { execute, stop } = useWebSocket()

  const handleSend = useCallback(async () => {
    if (!input.trim() || isExecuting) return

    // 세션 없으면 생성
    let sid = sessionId
    if (!sid) {
      try {
        const res = await fetch('/api/sessions', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ preset: 'standard' }),
        })
        const data = await res.json()
        sid = data.id
        setSessionId(sid)
      } catch {
        return
      }
    }

    reset()
    execute(sid, input.trim(), apiKey || undefined)
    setInput('')
  }, [input, apiKey, sessionId, isExecuting, execute, reset])

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  return (
    <div className="bg-bg-secondary border-t border-white/10 p-4">
      {/* API Key (숨김 가능) */}
      <div className="flex gap-2 mb-2">
        <input
          type="password"
          placeholder="Anthropic API Key (optional if server-configured)"
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
          className="flex-1 bg-bg-tertiary border border-white/10 rounded px-3 py-1.5 text-xs text-white/60 focus:outline-none focus:border-accent/40 placeholder-white/20"
        />
      </div>

      {/* Input */}
      <div className="flex gap-2">
        <textarea
          ref={textareaRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="메시지를 입력하세요... (Enter로 전송, Shift+Enter 줄바꿈)"
          disabled={isExecuting}
          rows={2}
          className="flex-1 bg-bg-tertiary border border-white/10 rounded-lg px-4 py-3 text-sm text-white/90 resize-none focus:outline-none focus:border-accent/40 placeholder-white/20 disabled:opacity-50"
        />
        <div className="flex flex-col gap-1">
          {isExecuting ? (
            <button
              onClick={stop}
              className="px-4 py-2 bg-red-500/20 border border-red-500/40 text-red-300 rounded-lg text-sm hover:bg-red-500/30 transition"
            >
              Stop
            </button>
          ) : (
            <button
              onClick={handleSend}
              disabled={!input.trim()}
              className="px-4 py-2 bg-accent/20 border border-accent/40 text-accent rounded-lg text-sm hover:bg-accent/30 transition disabled:opacity-30 disabled:cursor-not-allowed"
            >
              Send
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
