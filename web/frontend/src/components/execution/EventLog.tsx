import { useRef, useEffect, useState, useCallback } from 'react'
import { useExecutionStore } from '../../stores/executionStore'

const TYPE_COLORS: Record<string, string> = {
  'stage.enter': 'text-blue-400',
  'stage.exit': 'text-green-400',
  'text.delta': 'text-white/50',
  'tool.call': 'text-cyan-400',
  'tool.result': 'text-cyan-300',
  'pipeline.complete': 'text-green-500',
  'pipeline.error': 'text-red-400',
  'pipeline.metrics': 'text-amber-400',
  'evaluation': 'text-purple-400',
}

const MIN_W = 200
const MAX_W = 700
const DEFAULT_W = 360

export function EventLog() {
  const { events, isExecuting, activeStage } = useExecutionStore()
  const [width, setWidth] = useState(DEFAULT_W)
  const [dragging, setDragging] = useState(false)
  const scrollRef = useRef<HTMLDivElement>(null)
  const dragStartRef = useRef<{ x: number; w: number } | null>(null)

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [events.length])

  // 리사이즈 핸들 드래그
  const onResizeStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    dragStartRef.current = { x: e.clientX, w: width }
    setDragging(true)
  }, [width])

  useEffect(() => {
    if (!dragging) return
    const onMove = (e: MouseEvent) => {
      if (!dragStartRef.current) return
      const diff = dragStartRef.current.x - e.clientX
      setWidth(Math.min(MAX_W, Math.max(MIN_W, dragStartRef.current.w + diff)))
    }
    const onUp = () => { setDragging(false); dragStartRef.current = null }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
    return () => { window.removeEventListener('mousemove', onMove); window.removeEventListener('mouseup', onUp) }
  }, [dragging])

  return (
    <div className="relative flex-shrink-0 bg-bg-secondary border-l border-transparent flex flex-col"
      style={{ width }}>

      {/* 리사이즈 핸들 — 왼쪽 경계 */}
      <div
        className={`absolute left-0 top-0 bottom-0 w-1.5 cursor-col-resize z-10 transition
          ${dragging ? 'bg-accent/30' : 'bg-transparent hover:bg-white/8'}`}
        onMouseDown={onResizeStart}
      />

      {/* Header */}
      <div className="h-10 px-4 flex items-center justify-between shrink-0">
        <div className="flex items-center gap-2">
          <span className="text-[11px] font-mono text-white/40">Events</span>
          {isExecuting && (
            <span className="flex items-center gap-1.5">
              <span className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse" />
              <span className="text-[10px] text-accent/70 truncate max-w-[120px]">{activeStage}</span>
            </span>
          )}
        </div>
        <span className="text-[10px] text-white/20 font-mono">{events.length}</span>
      </div>

      {/* Event List */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto px-3 py-1 min-h-0">
        {events.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-white/15">
            <div className="text-3xl mb-2 opacity-30">⚡</div>
            <span className="text-[11px]">이벤트가 여기에 스트리밍됩니다</span>
          </div>
        ) : (
          events.map((event, i) => <EventItem key={i} event={event} />)
        )}
      </div>
    </div>
  )
}

function EventItem({ event }: { event: any }) {
  const [expanded, setExpanded] = useState(false)
  const colorClass = TYPE_COLORS[event.type] || 'text-white/30'
  const time = event.timestamp ? new Date(event.timestamp).toLocaleTimeString('ko-KR', { hour12: false }) : ''

  if (event.type === 'text.delta') {
    const text = event.data?.text || ''
    if (!text.trim()) return null
    return <div className="py-0.5 text-[10px] font-mono text-white/25 truncate">{text.slice(0, 60)}</div>
  }

  const hasData = event.data && Object.keys(event.data).length > 0

  return (
    <div className="py-1.5 border-b border-white/[.02]">
      <div
        className={`flex items-center gap-2 ${hasData ? 'cursor-pointer hover:bg-white/[.03] rounded px-1 -mx-1 py-0.5' : ''}`}
        onClick={() => hasData && setExpanded(!expanded)}
      >
        <span className="text-white/15 font-mono text-[9px] shrink-0 w-[52px]">{time}</span>
        <span className={`font-mono text-[10px] font-medium ${colorClass} shrink-0`}>
          {event.type.replace('pipeline.', '').replace('stage.', '')}
        </span>
        {event.stage && <span className="text-white/20 text-[10px] truncate">{event.stage}</span>}
        {hasData && (
          <span className="text-white/15 ml-auto shrink-0 text-[10px] font-mono">{expanded ? '−' : '+'}</span>
        )}
      </div>
      {expanded && hasData && (
        <pre className="mt-1.5 text-[9px] text-white/25 font-mono bg-black/30 rounded-lg p-2.5 overflow-x-auto max-h-36 whitespace-pre-wrap border border-white/[.04]">
          {JSON.stringify(event.data, null, 2)}
        </pre>
      )}
    </div>
  )
}
