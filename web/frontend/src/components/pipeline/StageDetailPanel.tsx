import { useEffect } from 'react'
import { usePipelineStore } from '../../stores/pipelineStore'
import { useUIStore } from '../../stores/uiStore'
import type { StageDescription, StageField } from '../../types/pipeline'

const STAGE_DETAILS: Record<string, { desc_ko: string; desc_en: string; behavior: string; bypass: string }> = {
  s01_input: {
    desc_ko: '사용자 입력을 검증하고, LLM 프로바이더를 초기화합니다. API 키를 확인하고, MCP 도구를 자동 수집합니다.',
    desc_en: 'Validates user input, initializes LLM provider, verifies API key, auto-discovers MCP tools.',
    behavior: 'API key resolution: env > xgen-core config > fallback\nMCP tool discovery from workflow nodes\nMultimodal input normalization (text + images + files)',
    bypass: '항상 실행 — 비활성화 불가',
  },
  s02_memory: {
    desc_ko: '이전 대화 이력과 실행 결과를 로드합니다. DB에서 최근 5개 결과를 가져와 컨텍스트에 주입합니다.',
    desc_en: 'Loads conversation history and previous execution results from database.',
    behavior: 'harness_execution_log > execution_io fallback\nMax 5 recent results, 2K chars each\nInjected as system prompt sections',
    bypass: '이전 결과가 없으면 건너뜀',
  },
  s03_system_prompt: {
    desc_ko: '시스템 프롬프트를 섹션 우선순위에 따라 조립합니다. Identity > Rules > Tools > RAG > History 순서.',
    desc_en: 'Assembles system prompt by section priority: Identity > Rules > Tools > RAG > History.',
    behavior: 'Priority-based section assembly\nLower priority sections removed first during compaction\n9 section slots with configurable content',
    bypass: '없음 — 항상 실행',
  },
  s04_tool_index: {
    desc_ko: 'Progressive Disclosure 3단계로 도구를 관리합니다.',
    desc_en: '3-level progressive disclosure: metadata in prompt > discover_tools for schema > execution.',
    behavior: 'Level 1: ~40 tokens/tool (name + description)\nLevel 2: discover_tools built-in for on-demand schema\nLevel 3: actual tool execution in s08',
    bypass: '등록된 도구가 없으면 건너뜀',
  },
  s05_plan: {
    desc_ko: 'Chain-of-Thought 계획 수립 단계.',
    desc_en: 'Chain-of-Thought planning. Instructs LLM to create a step-by-step plan before execution.',
    behavior: 'Adds planning instruction to system prompt\nOnly runs on first loop iteration\nOptional — can be disabled without impact',
    bypass: '첫 번째 루프 이후 건너뜀',
  },
  s06_context: {
    desc_ko: '토큰 윈도우를 관리합니다. 예산 80% 초과 시 3단계 압축을 실행합니다.',
    desc_en: 'Token window management. 3-tier compaction when budget exceeds 80%.',
    behavior: 'Tier 1: Remove old messages (keep first + last 3)\nTier 2: Drop low-priority prompt sections\nTier 3: Summarize remaining context',
    bypass: '첫 번째 루프에서만 실행',
  },
  s07_llm: {
    desc_ko: 'LLM API를 호출합니다. SSE 스트리밍으로 실시간 응답을 받고, 재시도와 모델 폴백을 지원합니다.',
    desc_en: 'LLM API call with SSE streaming, retry logic, and model fallback.',
    behavior: 'httpx SSE streaming (Anthropic / OpenAI)\nRetry: 429 > 10/20/40s, 529 > 1/2/4s\nModel fallback: Anthropic > OpenAI\nExtended Thinking support',
    bypass: '없음 — 항상 실행',
  },
  s08_execute: {
    desc_ko: '도구를 실행합니다. MCP 도구, 빌트인 도구를 순차 실행하고 결과를 메시지에 추가합니다.',
    desc_en: 'Executes tool calls. Runs MCP tools and built-in tools, adds results to messages.',
    behavior: 'Sequential execution with 60s timeout per tool\n50K char result budget (truncate excess)\nMCP routing: session_id > xgen-mcp-station',
    bypass: '도구 호출이 없으면 건너뜀',
  },
  s09_validate: {
    desc_ko: '독립 LLM 호출로 응답 품질을 평가합니다.',
    desc_en: 'Independent LLM evaluation with 4 weighted criteria.',
    behavior: 'Relevance x 0.3 + Completeness x 0.3\nAccuracy x 0.2 + Clarity x 0.2\nSeparate LLM call (low temperature)',
    bypass: '텍스트 응답이 없으면 건너뜀',
  },
  s10_decide: {
    desc_ko: '루프를 계속할지 완료할지 판단합니다.',
    desc_en: 'Loop control: evaluates tool calls, validation score, and budget.',
    behavior: 'Budget overflow > complete\nIteration limit > complete\nPending tool calls > continue\nValidation < threshold > retry',
    bypass: '없음 — 항상 실행',
  },
  s11_save: {
    desc_ko: '실행 결과를 DB에 저장합니다.',
    desc_en: 'Persists execution results to database with metrics and I/O data.',
    behavior: 'Target: harness_execution_log table\nIncludes: tokens, cost, duration, model\nGraceful skip if no DB connection',
    bypass: 'DB 연결이 없으면 건너뜀',
  },
  s12_complete: {
    desc_ko: '최종 출력을 확정하고 메트릭스를 수집합니다.',
    desc_en: 'Finalizes output, collects metrics, emits Done event to end streaming.',
    behavior: 'Sets state.final_output\nEmits MetricsEvent (tokens, cost, duration)\nEmits DoneEvent to close stream',
    bypass: '없음 — 항상 실행',
  },
}

const PHASE_BADGES: Record<string, { label: string; color: string }> = {
  ingress: { label: 'A · Ingress', color: 'from-blue-500/20 to-blue-600/10 text-blue-300 border-blue-500/20' },
  loop: { label: 'B · Agentic Loop', color: 'from-purple-500/20 to-purple-600/10 text-purple-300 border-purple-500/20' },
  egress: { label: 'C · Egress', color: 'from-emerald-500/20 to-emerald-600/10 text-emerald-300 border-emerald-500/20' },
}

export function StageDetailPanel() {
  const { selectedStageOrder, selectStage, locale } = useUIStore()
  const { stages, toggleStage, activeStrategies, setActiveStrategy, stageParams, setStageParam, getStageParam, dynamicOptions, fetchDynamicOptions } = usePipelineStore()

  if (selectedStageOrder === null) return null
  const stage = stages.find((s) => s.order === selectedStageOrder)
  if (!stage) return null

  const name = locale === 'ko' ? stage.display_name_ko : stage.display_name
  const detail = STAGE_DETAILS[stage.stage_id]
  const isLocked = stage.stage_id === 's01_input'
  const phase = PHASE_BADGES[stage.phase]

  return (
    <>
      <div className="fixed inset-0 bg-black/60 backdrop-blur-sm z-40" onClick={() => selectStage(null)} />

      <div className="fixed right-0 top-0 bottom-0 w-[440px] bg-gradient-to-b from-bg-secondary to-bg-primary border-l border-white/8 z-50 overflow-y-auto animate-slide-in">

        {/* Hero Header */}
        <div className="relative p-8 pb-6">
          <div className="absolute top-0 right-0 w-40 h-40 bg-accent/5 rounded-full blur-3xl -translate-y-1/2 translate-x-1/2" />

          <button onClick={() => selectStage(null)}
            className="absolute top-5 right-5 w-8 h-8 flex items-center justify-center rounded-full bg-white/5 hover:bg-white/10 text-white/40 hover:text-white/70 transition">
            x
          </button>

          <div className="flex items-center gap-4 mb-5">
            <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-accent/30 to-accent/10 border border-accent/20 flex items-center justify-center">
              <span className="text-accent text-2xl font-serif font-bold">{stage.order}</span>
            </div>
            <div>
              <h2 className="text-2xl font-serif font-semibold tracking-tight">{name}</h2>
              <span className="text-xs text-white/30 font-mono tracking-wider">{stage.stage_id}</span>
            </div>
          </div>

          {phase && (
            <div className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs border bg-gradient-to-r ${phase.color}`}>
              {phase.label}
            </div>
          )}
        </div>

        <div className="px-8 pb-8 space-y-6">

          {/* Toggle */}
          <div className="flex items-center justify-between p-4 rounded-xl bg-white/[.03] border border-white/[.06]">
            <div className="flex items-center gap-2.5">
              <div className={`w-2.5 h-2.5 rounded-full ${stage.active ? 'bg-green-400 shadow-lg shadow-green-400/30' : 'bg-white/20'}`} />
              <span className="text-sm" style={{ fontFamily: 'Inter' }}>
                {stage.active ? '활성' : '비활성'}
              </span>
            </div>
            {isLocked ? (
              <span className="text-[11px] text-white/25 font-mono">필수 스테이지</span>
            ) : (
              <button onClick={() => toggleStage(stage.stage_id)}
                className={`px-4 py-1.5 rounded-lg text-xs font-medium transition ${
                  stage.active
                    ? 'bg-white/5 text-white/50 hover:bg-red-500/10 hover:text-red-300 border border-white/8'
                    : 'bg-accent/15 text-accent hover:bg-accent/25 border border-accent/20'
                }`}>
                {stage.active ? 'Deactivate' : 'Activate'}
              </button>
            )}
          </div>

          {/* Description */}
          {detail && (
            <Section title="Description">
              <p className="text-[13px] text-white/55 leading-[1.7]" style={{ fontFamily: 'Inter' }}>
                {locale === 'ko' ? detail.desc_ko : detail.desc_en}
              </p>
            </Section>
          )}

          {/* Config Fields */}
          {stage.config?.fields && stage.config.fields.length > 0 && (
            <Section title="Configuration" count={stage.config.fields.length}>
              <div className="space-y-3">
                {stage.config.fields.map((f) => (
                  <ConfigFieldItem key={f.id} field={f} stage={stage} />
                ))}
              </div>
            </Section>
          )}

          {/* Technical Behavior */}
          {detail?.behavior && (
            <Section title="Technical Behavior">
              <div className="rounded-lg bg-black/30 border border-white/[.06] p-4">
                {detail.behavior.split('\n').map((line, i) => (
                  <div key={i} className="flex items-start gap-2 py-0.5">
                    <span className="text-accent/40 text-[10px] mt-1">{'\u25B8'}</span>
                    <span className="text-[11px] text-white/40 font-mono leading-relaxed">{line}</span>
                  </div>
                ))}
              </div>
            </Section>
          )}

          {/* Bypass */}
          {detail?.bypass && (
            <Section title="Bypass Condition">
              <p className="text-[12px] text-white/35 italic" style={{ fontFamily: 'Inter' }}>
                {detail.bypass}
              </p>
            </Section>
          )}

          {/* Artifacts */}
          {stage.artifacts.length > 0 && (
            <Section title="Artifacts" count={stage.artifacts.length}>
              <div className="space-y-2">
                {stage.artifacts.map((art) => (
                  <div key={art}
                    className="group flex items-center justify-between p-3 rounded-lg bg-white/[.02] border border-white/[.06] hover:border-accent/20 transition cursor-pointer">
                    <div className="flex items-center gap-2.5">
                      <div className="w-1.5 h-1.5 rounded-full bg-accent/50" />
                      <span className="text-[13px] text-white/60 font-mono">{art}</span>
                    </div>
                    {art === 'default' && (
                      <span className="text-[10px] text-accent/70 bg-accent/10 rounded-md px-2 py-0.5 font-medium">active</span>
                    )}
                  </div>
                ))}
              </div>
            </Section>
          )}

          {/* Strategies — 클릭으로 전환 */}
          {stage.strategies.length > 0 && (
            <Section title="Strategies" count={stage.strategies.length}>
              <div className="space-y-2">
                {stage.strategies.map((strat) => {
                  const isActive = activeStrategies[stage.stage_id] === strat.name
                  return (
                    <div key={strat.name}
                      onClick={() => setActiveStrategy(stage.stage_id, strat.name)}
                      className={`group p-3 rounded-lg cursor-pointer transition ${
                        isActive
                          ? 'bg-accent/8 border border-accent/25'
                          : 'bg-white/[.02] border border-white/[.06] hover:border-purple-500/20'
                      }`}>
                      <div className="flex items-center justify-between mb-1">
                        <span className={`text-[13px] font-mono ${isActive ? 'text-accent' : 'text-white/60'}`}>
                          {strat.name}
                        </span>
                        {isActive && (
                          <span className="text-[10px] text-accent/70 bg-accent/10 rounded-md px-2 py-0.5 font-medium">active</span>
                        )}
                        {!isActive && strat.is_default && (
                          <span className="text-[10px] text-green-400/70 bg-green-400/10 rounded-md px-2 py-0.5 font-medium">default</span>
                        )}
                      </div>
                      {strat.description && (
                        <p className="text-[11px] text-white/30 leading-relaxed">{strat.description}</p>
                      )}
                    </div>
                  )
                })}
              </div>
            </Section>
          )}
        </div>
      </div>
    </>
  )
}

// ─── Config Field Component ───

function ConfigFieldItem({ field, stage }: { field: StageField; stage: StageDescription }) {
  const { getStageParam, setStageParam, dynamicOptions, fetchDynamicOptions } = usePipelineStore()
  const val = getStageParam(stage.stage_id, field.id) ?? field.default
  const setVal = (v: any) => setStageParam(stage.stage_id, field.id, v)

  useEffect(() => {
    if (field.options_source && !dynamicOptions[field.options_source]) {
      fetchDynamicOptions(field.options_source)
    }
  }, [field.options_source])

  const options = field.options || dynamicOptions[field.options_source!] || []

  return (
    <div>
      <label className="block text-[11px] font-medium text-white/45 mb-1.5">{field.label}</label>

      {field.type === 'select' && (
        <select value={val} onChange={(e) => setVal(e.target.value)}
          className="w-full px-3 py-2 rounded-lg text-xs bg-white/[.04] border border-white/[.08] text-white outline-none focus:border-accent/40"
          style={{ fontFamily: 'inherit' }}>
          {options.map((o: string) => <option key={o} value={o} style={{ background: '#1a1a1a' }}>{o}</option>)}
        </select>
      )}

      {field.type === 'multi_select' && (
        <div className="flex flex-wrap gap-1">
          {!options.length && field.options_source && <span className="text-[10px] text-white/25">로딩...</span>}
          {options.map((o: string) => {
            const sel = (val || []).includes(o)
            return (
              <button key={o}
                onClick={() => setVal(sel ? (val || []).filter((x: string) => x !== o) : [...(val || []), o])}
                className={`text-[10px] px-2.5 py-1 rounded-full border cursor-pointer transition ${
                  sel ? 'bg-accent/15 border-accent/30 text-accent' : 'bg-white/[.04] border-white/[.08] text-white/50'
                }`}>
                {o}
              </button>
            )
          })}
        </div>
      )}

      {field.type === 'slider' && (
        <div className="flex items-center gap-3">
          <input type="range" min={field.min} max={field.max} step={field.step} value={val}
            onChange={(e) => setVal(parseFloat(e.target.value))}
            className="flex-1" style={{ accentColor: '#c8a45c' }} />
          <span className="text-xs font-mono font-semibold text-accent min-w-[36px] text-right">{val}</span>
        </div>
      )}

      {field.type === 'number' && (
        <input type="number" min={field.min} max={field.max} step={field.step} value={val}
          onChange={(e) => setVal(parseInt(e.target.value))}
          className="w-28 px-3 py-2 rounded-lg text-xs bg-white/[.04] border border-white/[.08] text-white outline-none focus:border-accent/40" />
      )}

      {field.type === 'toggle' && (
        <button onClick={() => setVal(!val)}
          className={`px-3.5 py-1 rounded-full text-[11px] font-semibold border cursor-pointer transition ${
            val ? 'bg-green-400/10 border-green-400/20 text-green-400' : 'bg-white/[.04] border-white/[.08] text-white/40'
          }`}>
          {val ? 'ON' : 'OFF'}
        </button>
      )}

      {field.type === 'textarea' && (
        <textarea value={val || ''} onChange={(e) => setVal(e.target.value)}
          placeholder={field.placeholder} rows={3}
          className="w-full px-3 py-2 rounded-lg text-xs bg-white/[.04] border border-white/[.08] text-white outline-none resize-y min-h-[60px] focus:border-accent/40"
          style={{ fontFamily: 'inherit' }} />
      )}

      {field.description && (
        <div className="text-[10px] text-white/25 mt-1">{field.description}</div>
      )}
    </div>
  )
}

// ─── Section ───

function Section({ title, count, children }: { title: string; count?: number; children: React.ReactNode }) {
  return (
    <div>
      <div className="flex items-center gap-2 mb-3">
        <h3 className="text-[11px] text-white/25 uppercase tracking-[.15em] font-medium" style={{ fontFamily: 'Inter' }}>
          {title}
        </h3>
        {count !== undefined && (
          <span className="text-[10px] text-white/15 font-mono">{count}</span>
        )}
        <div className="flex-1 h-px bg-white/[.04]" />
      </div>
      {children}
    </div>
  )
}
