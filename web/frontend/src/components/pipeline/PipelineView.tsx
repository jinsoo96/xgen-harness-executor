import { useMemo, useState, useCallback, useRef } from 'react'
import { usePipelineStore } from '../../stores/pipelineStore'
import { useExecutionStore } from '../../stores/executionStore'
import { useUIStore } from '../../stores/uiStore'
import type { StageDescription } from '../../types/pipeline'

const R = 38
const GX = 155
const GY = 130
const LEFT_M = 130  // 왼쪽 Phase 라벨 여백

export function PipelineView() {
  const { stages } = usePipelineStore()
  const toggleStage = usePipelineStore((s) => s.toggleStage)
  const exec = useExecutionStore()
  const { selectedStageOrder, selectStage, locale } = useUIStore()

  const initialLayout = useMemo(() => buildLayout(stages), [stages])
  const [offsets, setOffsets] = useState<Record<string, { dx: number; dy: number }>>({})
  const dragRef = useRef<{ id: string; startX: number; startY: number; origDx: number; origDy: number; moved: boolean } | null>(null)
  const svgRef = useRef<SVGSVGElement>(null)

  const getPos = useCallback((n: { x: number; y: number; s: StageDescription }) => {
    const o = offsets[n.s.stage_id]
    return { x: n.x + (o?.dx || 0), y: n.y + (o?.dy || 0) }
  }, [offsets])

  // 드래그 시작
  const onPointerDown = useCallback((e: React.PointerEvent, stageId: string) => {
    if (e.button !== 0) return
    e.stopPropagation()
    const o = offsets[stageId] || { dx: 0, dy: 0 }
    dragRef.current = { id: stageId, startX: e.clientX, startY: e.clientY, origDx: o.dx, origDy: o.dy, moved: false }
    ;(e.target as Element).setPointerCapture(e.pointerId)
  }, [offsets])

  // 드래그 중
  const onPointerMove = useCallback((e: React.PointerEvent) => {
    if (!dragRef.current) return
    const d = dragRef.current
    const svg = svgRef.current
    if (!svg) return

    const movedDist = Math.abs(e.clientX - d.startX) + Math.abs(e.clientY - d.startY)
    if (movedDist > 5) d.moved = true  // 5px 이상 움직이면 드래그로 판정

    if (!d.moved) return

    const rect = svg.getBoundingClientRect()
    const scale = Math.max(initialLayout.w / rect.width, initialLayout.h / rect.height)
    const dx = d.origDx + (e.clientX - d.startX) * scale
    const dy = d.origDy + (e.clientY - d.startY) * scale
    setOffsets(prev => ({ ...prev, [d.id]: { dx, dy } }))
  }, [initialLayout])

  // 드래그 끝 / 클릭 판정
  const onPointerUp = useCallback((e: React.PointerEvent, stageId?: string) => {
    const d = dragRef.current
    dragRef.current = null
    if (!d) return

    // 드래그 안 했으면 클릭으로 처리 → 상세 패널
    if (!d.moved && stageId) {
      const stage = stages.find(s => s.stage_id === stageId)
      if (stage) {
        selectStage(selectedStageOrder === stage.order ? null : stage.order)
      }
    }
  }, [stages, selectedStageOrder, selectStage])

  // 위치 리셋
  const resetPositions = useCallback(() => setOffsets({}), [])

  const nodes = initialLayout.nodes.map(n => ({ ...n, ...getPos(n) }))
  const hasOffsets = Object.keys(offsets).length > 0

  return (
    <div className="flex-1 min-w-0 overflow-auto bg-bg-primary relative">
      {/* 리셋 버튼 */}
      {hasOffsets && (
        <button onClick={resetPositions}
          className="absolute top-3 right-3 z-10 px-3 py-1.5 text-[11px] font-mono text-white/40 bg-white/5 border border-white/10 rounded-lg hover:bg-white/10 hover:text-white/60 transition">
          ↺ Reset
        </button>
      )}

      <svg ref={svgRef}
        viewBox={`0 0 ${initialLayout.w} ${initialLayout.h}`}
        className="w-full h-full"
        style={{ minWidth: initialLayout.w, minHeight: initialLayout.h }}
        onPointerMove={onPointerMove}
        onPointerUp={() => { dragRef.current = null }}>
        <defs>
          <filter id="glow"><feGaussianBlur stdDeviation="10" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
          <filter id="glowG"><feGaussianBlur stdDeviation="5" result="b"/><feFlood floodColor="#22c55e" floodOpacity=".25" result="c"/><feComposite in="c" in2="b" operator="in" result="d"/><feMerge><feMergeNode in="d"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
          <radialGradient id="nf" cx="38%" cy="32%" r="65%"><stop offset="0%" stopColor="#2e2e2e"/><stop offset="100%" stopColor="#1a1a1a"/></radialGradient>
          <radialGradient id="nfOff" cx="38%" cy="32%" r="65%"><stop offset="0%" stopColor="#171717"/><stop offset="100%" stopColor="#0f0f0f"/></radialGradient>
          <radialGradient id="nfAct" cx="38%" cy="32%" r="65%"><stop offset="0%" stopColor="#4a3a1a"/><stop offset="100%" stopColor="#2a2010"/></radialGradient>
          <radialGradient id="nfDone" cx="38%" cy="32%" r="65%"><stop offset="0%" stopColor="#1a3a1a"/><stop offset="100%" stopColor="#0f1f0f"/></radialGradient>
          <radialGradient id="nfErr" cx="38%" cy="32%" r="65%"><stop offset="0%" stopColor="#3a1a1a"/><stop offset="100%" stopColor="#1f0f0f"/></radialGradient>
          <style>{`
            @keyframes df{to{stroke-dashoffset:-20}}.dash{stroke-dasharray:10 6;animation:df 1.5s linear infinite}
            @keyframes pr{0%,100%{r:${R+5};opacity:.2}50%{r:${R+13};opacity:.6}}.pr{animation:pr 2.5s ease-in-out infinite}
          `}</style>
        </defs>

        {/* Phase labels — 왼쪽 고정 */}
        {initialLayout.phases.map(p => (
          <g key={p.id}>
            <text x={18} y={p.y} className="fill-white/30 font-bold" style={{ fontSize: 15, fontFamily: 'Playfair Display, serif' }}>{p.letter}</text>
            <text x={18} y={p.y + 17} className="fill-white/15" style={{ fontSize: 10, fontFamily: 'Inter' }}>{locale === 'ko' ? p.ko : p.en}</text>
          </g>
        ))}

        {/* 연결선 */}
        {initialLayout.connections.map((c, i) => {
          const from = nodes.find(n => n.s.stage_id === c.from)
          const to = nodes.find(n => n.s.stage_id === c.to)
          if (!from || !to) return null
          const dx = to.x - from.x, dy = to.y - from.y
          const dist = Math.sqrt(dx * dx + dy * dy)
          if (dist < 1) return null
          const nx = dx / dist, ny = dy / dist
          const x1 = from.x + nx * (R + 4), y1 = from.y + ny * (R + 4)
          const x2 = to.x - nx * (R + 4), y2 = to.y - ny * (R + 4)

          if (c.type === 'loopback') {
            const leftX = Math.min(from.x, to.x) - GX * 0.85
            return <path key={i} d={`M${x1} ${from.y}C${leftX} ${from.y},${leftX} ${to.y},${x2} ${to.y}`}
              fill="none" stroke="rgba(200,164,92,.30)" strokeWidth={1.8} className="dash" />
          }

          if (Math.abs(dy) > R * 2) {
            const midY = (from.y + to.y) / 2
            return <path key={i} d={`M${from.x} ${y1}C${from.x} ${midY},${to.x} ${midY},${to.x} ${y2}`}
              fill="none" stroke="rgba(255,255,255,.08)" strokeWidth={1.5} />
          }
          return <line key={i} x1={x1} y1={y1} x2={x2} y2={y2} stroke="rgba(255,255,255,.08)" strokeWidth={1.5} />
        })}

        {/* 노드 */}
        {nodes.map(n => {
          const s = n.s
          const off = !s.active
          const act = exec.activeStage === s.stage_id
          const done = exec.completedStages.has(s.stage_id)
          const err = exec.errorStages.has(s.stage_id)
          const sel = selectedStageOrder === s.order

          let fill = off ? 'url(#nfOff)' : 'url(#nf)'
          let stroke = off ? 'rgba(255,255,255,.04)' : 'rgba(255,255,255,.12)'
          let sw = 1.5, flt = '', nc = off ? 'rgba(255,255,255,.18)' : 'rgba(255,255,255,.6)'
          let lc = off ? 'rgba(255,255,255,.12)' : 'rgba(255,255,255,.42)'

          if (err) { fill = 'url(#nfErr)'; stroke = '#ef4444'; sw = 2; flt = 'url(#glow)'; nc = '#ef4444'; lc = '#ef4444' }
          else if (act) { fill = 'url(#nfAct)'; stroke = '#c8a45c'; sw = 2.5; flt = 'url(#glow)'; nc = '#fff'; lc = '#c8a45c' }
          else if (done) { fill = 'url(#nfDone)'; stroke = '#22c55e'; sw = 1.5; flt = 'url(#glowG)'; nc = 'rgba(255,255,255,.8)'; lc = 'rgba(255,255,255,.55)' }
          else if (sel) { stroke = 'rgba(200,164,92,.5)'; sw = 2; nc = 'rgba(255,255,255,.8)'; lc = 'rgba(255,255,255,.6)' }

          const nm = locale === 'ko' ? s.display_name_ko : s.display_name

          return (
            <g key={s.stage_id} style={{ cursor: 'grab' }}
              onDoubleClick={e => { e.stopPropagation(); toggleStage(s.stage_id) }}
              onPointerDown={e => onPointerDown(e, s.stage_id)}
              onPointerUp={e => onPointerUp(e, s.stage_id)}>
              {act && <circle cx={n.x} cy={n.y} r={R + 6} fill="none" stroke="#c8a45c" strokeWidth={2} className="pr" opacity={.5} />}
              {done && !err && <circle cx={n.x} cy={n.y} r={R + 3} fill="none" stroke="#22c55e" strokeWidth={1} opacity={.3} />}
              <circle cx={n.x} cy={n.y} r={R} fill={fill} stroke={stroke} strokeWidth={sw} filter={flt} style={{ transition: 'fill .4s, stroke .4s' }} />
              <text x={n.x} y={n.y + 7} textAnchor="middle" fill={nc}
                style={{ fontSize: 20, fontWeight: 700, fontFamily: 'Inter', userSelect: 'none', pointerEvents: 'none' }}>{s.order}</text>
              <text x={n.x} y={n.y + R + 22} textAnchor="middle" fill={lc}
                style={{ fontSize: 12, fontFamily: 'Inter', userSelect: 'none', pointerEvents: 'none' }}>{nm}</text>
              <circle cx={n.x} cy={n.y} r={R + 16} fill="transparent" />
            </g>
          )
        })}
      </svg>
    </div>
  )
}

// === Layout ===

interface N { x: number; y: number; s: StageDescription }
interface Conn { from: string; to: string; type: 'line' | 'curve' | 'loopback' }
interface Ph { id: string; letter: string; ko: string; en: string; y: number }
interface LO { nodes: N[]; connections: Conn[]; phases: Ph[]; w: number; h: number }

function buildLayout(stages: StageDescription[]): LO {
  const nodes: N[] = []
  const conns: Conn[] = []
  const phases: Ph[] = []

  const input = stages.find(s => s.stage_id === 's01_input')
  const ingress = stages.filter(s => s.phase === 'ingress' && s.stage_id !== 's01_input')
  const loop = stages.filter(s => s.phase === 'loop')
  const egress = stages.filter(s => s.phase === 'egress')

  const COLS = 3
  const rowW = (COLS - 1) * GX
  const cx = LEFT_M + rowW / 2

  let y = 55

  // Phase A
  phases.push({ id: 'a', letter: 'A', ko: '초기화', en: 'Ingress', y: y + 5 })
  if (input) {
    y += 38
    nodes.push({ x: cx, y, s: input })
  }

  // Phase B
  y += GY - 15
  phases.push({ id: 'b', letter: 'B', ko: '에이전트 루프', en: 'Agentic Loop', y: y + 5 })
  y += 32

  const sx = LEFT_M

  // ingress row (2,3,4)
  if (ingress.length) {
    for (let i = 0; i < Math.min(ingress.length, COLS); i++) {
      nodes.push({ x: sx + i * GX, y, s: ingress[i] })
    }
  }

  // loop row 1 (5,6,7)
  y += GY
  const loopR1 = loop.slice(0, COLS)
  for (let i = 0; i < loopR1.length; i++) {
    nodes.push({ x: sx + i * GX, y, s: loopR1[i] })
  }

  // loop row 2 reversed (10,9,8)
  const loopR2 = loop.slice(COLS)
  if (loopR2.length) {
    y += GY
    for (let i = 0; i < loopR2.length; i++) {
      nodes.push({ x: sx + (Math.min(COLS, loopR2.length) - 1 - i) * GX, y, s: loopR2[i] })
    }
  }

  // Phase C
  if (egress.length) {
    y += GY
    phases.push({ id: 'c', letter: 'C', ko: '최종', en: 'Egress', y: y + 5 })
    y += 32
    const ew = (egress.length - 1) * GX
    const esx = LEFT_M + (rowW - ew) / 2
    for (let i = 0; i < egress.length; i++) {
      nodes.push({ x: esx + i * GX, y, s: egress[i] })
    }
  }

  // 순서대로 연결
  const sorted = [...nodes].sort((a, b) => a.s.order - b.s.order)
  for (let i = 0; i < sorted.length - 1; i++) {
    conns.push({ from: sorted[i].s.stage_id, to: sorted[i + 1].s.stage_id, type: 'line' })
  }

  // 루프백
  if (loop.length > 1) {
    conns.push({ from: loop[loop.length - 1].stage_id, to: loop[0].stage_id, type: 'loopback' })
  }

  const maxX = Math.max(...nodes.map(n => n.x)) + LEFT_M + R
  const maxY = y + 75
  return { nodes, connections: conns, phases, w: Math.max(maxX, 600), h: Math.max(maxY, 500) }
}
