import { useState } from 'react'
import { Header } from './components/layout/Header'
import { PipelineView } from './components/pipeline/PipelineView'
import { StageDetailPanel } from './components/pipeline/StageDetailPanel'
import { EventLog } from './components/execution/EventLog'
import { ResultPanel } from './components/execution/ResultPanel'
import { InputPanel } from './components/execution/InputPanel'

type Tab = 'pipeline' | 'orchestrator'

export default function App() {
  const [tab, setTab] = useState<Tab>('pipeline')

  return (
    <div className="h-screen flex flex-col bg-bg-primary text-white overflow-hidden">
      {/* Header */}
      <Header />

      {/* Tab Bar */}
      <div className="h-9 bg-bg-secondary border-b border-white/10 flex items-center px-6 gap-4">
        <TabBtn active={tab === 'pipeline'} onClick={() => setTab('pipeline')}>Pipeline</TabBtn>
        <TabBtn active={tab === 'orchestrator'} onClick={() => setTab('orchestrator')}>Orchestrator</TabBtn>
      </div>

      {/* Main */}
      <div className="flex-1 flex min-h-0">
        {tab === 'pipeline' ? (
          <>
            <PipelineView />
            <EventLog />
          </>
        ) : (
          <OrchestratorView />
        )}
      </div>

      {/* Result + Input */}
      <ResultPanel />
      <InputPanel />

      {/* Overlay */}
      <StageDetailPanel />
    </div>
  )
}

function TabBtn({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      className={`text-xs font-mono px-3 py-1 rounded transition ${
        active ? 'bg-accent/20 text-accent border border-accent/30' : 'text-white/40 hover:text-white/60'
      }`}
    >
      {children}
    </button>
  )
}

function OrchestratorView() {
  return (
    <div className="flex-1 flex items-center justify-center">
      <div className="text-center max-w-md">
        <div className="text-5xl mb-4 opacity-30">🔀</div>
        <h2 className="text-lg font-serif text-white/60 mb-2">DAG Orchestrator</h2>
        <p className="text-sm text-white/30 leading-relaxed">
          멀티 에이전트 DAG 실행을 시각화합니다.<br/>
          워크플로우에 여러 에이전트 노드가 있으면<br/>
          자동으로 DAG를 구성하여 병렬/순차 실행합니다.
        </p>
        <div className="mt-6 space-y-2 text-xs text-white/20 font-mono">
          <div>• 토폴로지 정렬 (Kahn's algorithm)</div>
          <div>• 같은 레벨 에이전트 병렬 실행</div>
          <div>• 이전 출력 → 다음 입력 자동 연결</div>
          <div>• 실시간 이벤트 스트리밍</div>
        </div>
      </div>
    </div>
  )
}
