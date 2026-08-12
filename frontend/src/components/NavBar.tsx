import { Home, Sparkles, GitBranch, Scissors, User } from 'lucide-react'
import type { ElementType } from 'react'
import { type AppState, type PageKey, useAppStore } from '../store/useAppStore'
import { cn } from '../lib/utils'

const navItems: Array<{ key: PageKey; label: string; icon: ElementType }> = [
  { key: 'dashboard', label: 'Dashboard', icon: Home },
  { key: 'optimize', label: 'Optimize', icon: Sparkles },
  { key: 'merge', label: 'Merge', icon: GitBranch },
  { key: 'split', label: 'Split', icon: Scissors },
  { key: 'profile', label: 'Profile', icon: User },
]

export function NavBar() {
  const page = useAppStore((state: AppState) => state.page)
  const setPage = useAppStore((state: AppState) => state.setPage)

  return (
    <nav className="sticky top-0 z-40 border-b border-white/10 bg-primary/95 backdrop-blur-xl">
      <div className="mx-auto flex max-w-6xl items-center justify-between gap-3 px-4 py-3 sm:px-6 lg:px-8">
        <div className="flex items-center gap-3">
          <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-accent text-primary shadow-glow">
            <Sparkles className="h-5 w-5" />
          </div>
          <div>
            <p className="text-sm font-semibold uppercase tracking-[0.28em] text-slate-300">Amen</p>
            <p className="text-xs text-slate-500">Football Mini App</p>
          </div>
        </div>
        <div className="hidden gap-2 md:flex">
          {navItems.map((item) => {
            const Icon = item.icon
            const active = page === item.key
            return (
              <button
                key={item.key}
                type="button"
                onClick={() => setPage(item.key)}
                className={cn(
                  'inline-flex items-center gap-2 rounded-2xl px-4 py-2 text-sm font-semibold transition',
                  active
                    ? 'bg-white text-primary shadow-lg'
                    : 'text-slate-300 hover:bg-white/10 hover:text-white',
                )}
              >
                <Icon className="h-4 w-4" />
                {item.label}
              </button>
            )
          })}
        </div>
      </div>
    </nav>
  )
}
