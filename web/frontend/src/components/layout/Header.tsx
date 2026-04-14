import { useEffect } from 'react'
import { usePipelineStore } from '../../stores/pipelineStore'
import { useUIStore } from '../../stores/uiStore'

export function Header() {
  const { loadPipeline, stages } = usePipelineStore()
  const { locale, setLocale } = useUIStore()

  useEffect(() => { loadPipeline() }, [])

  const activeCount = stages.filter((s) => s.active).length

  return (
    <header className="h-12 bg-bg-secondary flex items-center justify-between px-6">
      <div className="flex items-center gap-4">
        <h1 className="text-base font-serif">
          <span className="text-accent">xgen</span>
          <span className="text-white/50">-harness</span>
        </h1>
        <span className="text-[11px] text-white/25 font-mono">{activeCount}/12 stages</span>
      </div>
      <div className="flex items-center gap-3">
        <button onClick={() => setLocale(locale === 'ko' ? 'en' : 'ko')}
          className="text-[10px] text-white/40 hover:text-white/60 border border-white/10 rounded px-2 py-0.5 transition">
          {locale === 'ko' ? 'EN' : 'KO'}
        </button>
      </div>
    </header>
  )
}
