import { motion } from 'framer-motion'
import { Clock3 } from 'lucide-react'
import { type MatchItem } from '../data/matches'
import { cn } from '../lib/utils'
import { Button } from './ui/button'

interface MatchListProps {
  matches: MatchItem[]
  selectedMatchIds: string[]
  onToggle: (id: string) => void
  onSort: () => void
}

export function MatchList({ matches, selectedMatchIds, onToggle, onSort }: MatchListProps) {
  return (
    <div className="space-y-4">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <p className="text-sm uppercase tracking-[0.3em] text-slate-500">Optimize fixtures</p>
          <h2 className="mt-2 text-2xl font-semibold text-white">Select matches to merge your best booking</h2>
        </div>
        <Button variant="secondary" onClick={onSort}>
          Sort by Kickoff Time
        </Button>
      </div>

      <div className="grid gap-4">
        {matches.map((match) => {
          const selected = selectedMatchIds.includes(match.id)
          return (
            <motion.div
              key={match.id}
              initial={{ opacity: 0, y: 14 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.25 }}
              className={cn(
                'rounded-[28px] border border-white/10 bg-white/5 p-5 shadow-sm transition hover:border-accent/40',
                selected && 'border-accent/60 bg-accent/10',
              )}
            >
              <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
                <div>
                  <p className="text-sm uppercase tracking-[0.3em] text-slate-400">{match.competition}</p>
                  <h3 className="mt-2 text-xl font-semibold text-white">
                    {match.home} vs {match.away}
                  </h3>
                </div>
                <div className="flex items-center gap-3">
                  <div className="inline-flex items-center gap-2 rounded-full border border-white/10 bg-surface px-3 py-2 text-sm text-slate-300">
                    <Clock3 className="h-4 w-4 text-accent" />
                    <span>{new Date(match.kickoff).toLocaleString('en-GB', { weekday: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}</span>
                  </div>
                  <Button
                    variant={selected ? 'default' : 'outline'}
                    size="sm"
                    onClick={() => onToggle(match.id)}
                  >
                    {selected ? 'Selected' : 'Select'}
                  </Button>
                </div>
              </div>
            </motion.div>
          )
        })}
      </div>

      <div className="rounded-[28px] border border-white/10 bg-surface/95 p-5 text-slate-300">
        <p className="text-sm uppercase tracking-[0.3em] text-slate-500">Selected matches</p>
        <p className="mt-2 text-sm leading-6">
          {selectedMatchIds.length === 0
            ? 'Pick a few matches to assemble a premium booking.'
            : `${selectedMatchIds.length} match${selectedMatchIds.length === 1 ? '' : 'es'} selected.`}
        </p>
      </div>
    </div>
  )
}
