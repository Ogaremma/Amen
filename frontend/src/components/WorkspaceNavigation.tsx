import { BarChart3, SlidersHorizontal } from 'lucide-react'

export type Workspace = 'optimizer' | 'analyzer'

interface WorkspaceNavigationProps {
  active: Workspace | null
  onChange: (workspace: Workspace) => void
  onFutureTool?: () => void
}

export function WorkspaceNavigation({ active, onChange, onFutureTool }: WorkspaceNavigationProps) {
  return (
    <nav aria-label="Amen workspaces" className="mx-auto max-w-3xl rounded-3xl border border-white/10 bg-surface/80 p-3 shadow-glow backdrop-blur-xl sm:p-4">
      <p className="mb-3 px-1 text-xs font-semibold uppercase tracking-[0.22em] text-slate-400 sm:mb-4">Workspace</p>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
      <button
        type="button"
        aria-pressed={active === 'optimizer'}
        onClick={() => onChange('optimizer')}
        className={`flex min-h-24 items-center justify-center gap-2 rounded-2xl border px-3 text-center text-xs font-semibold uppercase tracking-[0.14em] transition sm:min-h-28 sm:text-sm ${active === 'optimizer' ? 'border-blue-400/30 bg-primary text-white shadow-lg shadow-blue-950/40 ring-1 ring-blue-400/25' : 'border-white/10 bg-white/[0.03] text-slate-400 hover:border-white/20 hover:bg-white/5 hover:text-white'}`}
      >
        <SlidersHorizontal className="h-4 w-4 shrink-0" />
        <span>Optimizer</span>
      </button>
      <button
        type="button"
        aria-pressed={active === 'analyzer'}
        onClick={() => onChange('analyzer')}
        className={`flex min-h-24 items-center justify-center gap-2 rounded-2xl border px-3 text-center text-xs font-semibold uppercase tracking-[0.1em] transition sm:min-h-28 sm:text-sm ${active === 'analyzer' ? 'border-violet-300/30 bg-violet-700 text-white shadow-lg shadow-violet-950/30 ring-1 ring-violet-300/25' : 'border-white/10 bg-white/[0.03] text-slate-400 hover:border-white/20 hover:bg-white/5 hover:text-white'}`}
      >
        <BarChart3 className="h-4 w-4 shrink-0" />
        <span>39 Billion Analyzer</span>
      </button>
      <button type="button" onClick={onFutureTool} className="flex min-h-24 flex-col items-center justify-center rounded-2xl border border-white/10 bg-white/[0.03] px-3 text-center transition hover:border-white/20 hover:bg-white/5 sm:col-span-2 sm:min-h-24">
        <span className="text-xs font-semibold uppercase tracking-[0.14em] text-slate-500 sm:text-sm">Future Tool</span>
        <span className="mt-1 text-xs text-slate-600">Coming soon</span>
      </button>
      </div>
    </nav>
  )
}
