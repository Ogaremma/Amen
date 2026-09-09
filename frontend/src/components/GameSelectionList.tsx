import { useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import { CalendarDays, Check, CheckCheck, CircleHelp, Clock3, X } from 'lucide-react'
import type { BookingSelection } from '../types/booking'
import { Card } from './ui/card'
import { sortChronologically } from '../lib/gameSelection'

const MONTHS = [
  'JANUARY', 'FEBRUARY', 'MARCH', 'APRIL', 'MAY', 'JUNE',
  'JULY', 'AUGUST', 'SEPTEMBER', 'OCTOBER', 'NOVEMBER', 'DECEMBER',
]

function formatDateHeader(isoDate: string): string {
  const [year, month, day] = isoDate.split('-').map(Number)
  if (!year || !month || !day) return isoDate
  return `${MONTHS[month - 1]} ${day}, ${year}`
}

function formatTime12(hhmm: string): string {
  const [hRaw, minutes = '00'] = hhmm.split(':')
  const hour = Number(hRaw)
  if (Number.isNaN(hour)) return hhmm
  return `${hour % 12 || 12}:${minutes} ${hour >= 12 ? 'PM' : 'AM'}`
}

function formatOdds(odds: number | null): string {
  if (odds === null || !Number.isFinite(odds)) return '—'
  return odds.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

const STATUS = {
  upcoming: { label: 'Upcoming', classes: 'border-sky-400/20 bg-sky-400/10 text-sky-200' },
  live: { label: 'Live', classes: 'border-emerald-400/25 bg-emerald-400/10 text-emerald-300' },
  ended: { label: 'Ended', classes: 'border-red-400/25 bg-red-400/10 text-red-300' },
} as const

const RESULT = {
  pending: { label: 'Pending', classes: 'text-orange-400', icon: null },
  won: { label: 'Won', classes: 'text-emerald-400', icon: Check },
  lost: { label: 'Lost', classes: 'text-red-400', icon: X },
  void: { label: 'Void', classes: 'text-slate-300', icon: CircleHelp },
  unknown: { label: 'Unknown', classes: 'text-slate-500', icon: CircleHelp },
} as const

interface DateGroup { date: string; selections: BookingSelection[] }

function groupByLocalDate(selections: BookingSelection[]): DateGroup[] {
  const groups: DateGroup[] = []
  for (const selection of selections) {
    const last = groups.at(-1)
    if (last?.date === selection.local_kickoff_date) last.selections.push(selection)
    else groups.push({ date: selection.local_kickoff_date, selections: [selection] })
  }
  return groups
}

function SwipeDeleteSurface({ children, onDelete, rounded = 'rounded-2xl' }: { children: ReactNode; onDelete: () => Promise<void>; rounded?: string }) {
  const start = useRef<{ x: number; y: number } | null>(null)
  const [offset, setOffset] = useState(0)
  const [deleting, setDeleting] = useState(false)
  const end = async (x: number, y: number) => {
    const origin = start.current
    start.current = null
    if (!origin) return
    const dx = x - origin.x
    const dy = y - origin.y
    if (Math.abs(dx) <= Math.abs(dy) || Math.abs(dx) < 100) { setOffset(0); return }
    setDeleting(true)
    setOffset(dx > 0 ? 700 : -700)
    try { await onDelete() } catch { setDeleting(false); setOffset(0) }
  }
  return <div className={`relative overflow-hidden bg-red-600 ${rounded}`}><div className="absolute inset-0 flex items-center justify-center text-sm font-bold tracking-[0.2em] text-white">DELETE</div><div className="relative bg-surface" data-swipe-delete style={{ transform: `translateX(${offset}px)`, transition: deleting ? 'transform 180ms ease-out' : 'none', touchAction: 'pan-y' }} onPointerDown={(e) => { start.current = { x: e.clientX, y: e.clientY }; e.currentTarget.setPointerCapture?.(e.pointerId) }} onPointerMove={(e) => { if (start.current) { const dx = e.clientX - start.current.x; if (Math.abs(dx) > 8) setOffset(dx) } }} onPointerUp={(e) => void end(e.clientX, e.clientY)} onPointerCancel={() => { start.current = null; setOffset(0) }}>{children}</div></div>
}

export interface GameSelectionListProps {
  selections: BookingSelection[]
  selectedEventIds: Set<string>
  busy?: boolean
  onToggleSelection: (eventId: string) => void
  onToggleDateSelections: (selections: BookingSelection[]) => void
  onSwipeDelete?: (eventId: string) => Promise<void>
}

export function GameSelectionList({ selections, selectedEventIds, busy = false, onToggleSelection, onToggleDateSelections, onSwipeDelete }: GameSelectionListProps) {
  const groups = useMemo(() => groupByLocalDate(sortChronologically(selections)), [selections])
  return <div className="space-y-2.5">
    {groups.map((group) => (
      <section key={group.date} className="space-y-2.5">
        <div className="flex items-center gap-2 px-1">
          <CalendarDays className="h-4 w-4 text-accent" />
          <h2 className="date-section-heading text-xs font-bold uppercase tracking-[0.18em] text-yellow-300">{formatDateHeader(group.date)}</h2>
          <div className="ml-auto flex items-center gap-2">
            <span className="text-[11px] text-slate-500">{group.selections.length} game{group.selections.length === 1 ? '' : 's'}</span>
            <button type="button" onClick={() => onToggleDateSelections(group.selections)} disabled={busy || group.selections.length === 0} aria-label={`${group.selections.every((selection) => selectedEventIds.has(selection.event_id)) ? 'Deselect' : 'Select'} all games on ${formatDateHeader(group.date)}`} className={`flex h-8 w-8 items-center justify-center rounded-lg border transition disabled:cursor-not-allowed disabled:opacity-50 ${group.selections.every((selection) => selectedEventIds.has(selection.event_id)) ? 'border-red-400/40 bg-red-500/15 text-red-200' : 'border-white/10 bg-white/[0.03] text-slate-300 hover:bg-white/[0.06]'}`}>
              <CheckCheck className="h-4 w-4" />
            </button>
          </div>
        </div>
        <div className="space-y-2">
          {group.selections.map((selection) => {
            const selected = selectedEventIds.has(selection.event_id)
            const status = STATUS[selection.game_status]
            const result = RESULT[selection.result_status]
            const ResultIcon = result.icon
            const card = <Card key={selection.event_id} data-selected={selected ? 'true' : 'false'} role="button" tabIndex={busy ? -1 : 0} aria-pressed={selected} aria-disabled={busy} aria-label={`Toggle selection for ${selection.home} vs ${selection.away}`} onClick={() => onToggleSelection(selection.event_id)} onKeyDown={(event: KeyboardEvent<HTMLDivElement>) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onToggleSelection(selection.event_id) } }} className={`relative cursor-pointer p-3 pb-5 pr-12 transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-400 focus-visible:ring-offset-2 focus-visible:ring-offset-surface ${selected ? 'border-red-400/60 bg-red-500/10' : ''}`}>
              <div className="min-w-0"><div className="flex items-center justify-between gap-2"><span className="inline-flex items-center gap-1 text-xs font-medium text-accent"><Clock3 className="h-3.5 w-3.5" />{formatTime12(selection.local_kickoff_time)}</span><span className="flex items-center gap-2"><span role="img" aria-label={`Bet result: ${result.label}`} title={`Bet result: ${result.label}`} className={`inline-flex h-4 w-4 items-center justify-center ${result.classes}`}>{ResultIcon ? <ResultIcon className="h-3.5 w-3.5" strokeWidth={3} /> : <span className="h-2 w-2 rounded-full bg-current" />}</span><span className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${status.classes}`}>{status.label}</span></span></div><h3 className="mt-1.5 truncate text-sm font-semibold text-white sm:text-base">{selection.home} <span className="text-slate-500">vs</span> {selection.away}</h3><p className="mt-0.5 truncate text-[11px] text-slate-500">{selection.competition}{selection.category ? ` · ${selection.category}` : ''}</p><div className="mt-2 flex items-end justify-between gap-2"><div className="min-w-0 text-xs text-slate-300"><p className="truncate">{selection.market}</p><p className="truncate font-medium text-accent">{selection.outcome}</p></div><p className="shrink-0 text-base font-semibold text-white">{selection.odds === null ? '—' : `${formatOdds(selection.odds)}x`}</p></div></div><span className="absolute bottom-1 right-1 flex h-10 w-10 items-center justify-center" title={`${selected ? 'Deselect' : 'Select'} ${selection.home} vs ${selection.away}`} aria-hidden="true"><span data-testid={`selection-control-${selection.event_id}`} className={`flex h-4 w-4 items-center justify-center rounded border transition ${selected ? 'border-red-500 bg-red-500 text-white' : 'border-slate-500 bg-transparent text-transparent'}`}><Check className="h-3.5 w-3.5" strokeWidth={3} /></span></span>
            </Card>
            return onSwipeDelete ? <SwipeDeleteSurface key={selection.event_id} onDelete={() => onSwipeDelete(selection.event_id)}>{card}</SwipeDeleteSurface> : card
          })}
        </div>
      </section>
    ))}
  </div>
}
