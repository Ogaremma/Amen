import { BarChart3, SlidersHorizontal } from 'lucide-react'

export type Workspace = 'optimizer' | 'analyzer'

interface WorkspaceNavigationProps {
  active: Workspace
  onChange: (workspace: Workspace) => void
}

export function WorkspaceNavigation({ active, onChange }: WorkspaceNavigationProps) {
  return (
    <nav aria-label="Amen workspaces" className="mx-auto grid max-w-3xl grid-cols-2 gap-2 rounded-2xl border border-white/10 bg-surface/80 p-1.5 shadow-glow backdrop-blur-xl">
      <button
        type="button"
        aria-pressed={active === 'optimizer'}
        onClick={() => onChange('optimizer')}
        className={`flex min-h-12 items-center justify-center gap-2 rounded-xl px-2 text-center text-[11px] font-semibold uppercase tracking-wide transition sm:text-sm ${active === 'optimizer' ? 'bg-primary text-white shadow-lg shadow-blue-950/40 ring-1 ring-blue-400/25' : 'text-slate-400 hover:bg-white/5 hover:text-white'}`}
      >
        <SlidersHorizontal className="h-4 w-4 shrink-0" />
        <span>Optimizer</span>
      </button>
      <button
        type="button"
        aria-pressed={active === 'analyzer'}
        onClick={() => onChange('analyzer')}
        className={`flex min-h-12 items-center justify-center gap-2 rounded-xl px-2 text-center text-[10px] font-semibold uppercase tracking-wide transition sm:text-sm ${active === 'analyzer' ? 'bg-violet-700 text-white shadow-lg shadow-violet-950/30 ring-1 ring-violet-300/25' : 'text-slate-400 hover:bg-white/5 hover:text-white'}`}
      >
        <BarChart3 className="h-4 w-4 shrink-0" />
        <span>39 Billion Analyzer</span>
      </button>
    </nav>
  )
}
