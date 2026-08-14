import { useMemo, useState, type KeyboardEvent } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import {
  AlertTriangle,
  ArrowLeft,
  CalendarDays,
  Check,
  ChevronRight,
  Clipboard,
  Clock3,
  Loader2,
  RefreshCw,
  Trash2,
  X,
} from 'lucide-react'
import { fetchBookingByCode, removeSelectedGames } from '../lib/api'
import type { BookingResponse, BookingSelection } from '../types/booking'
import { Button } from './ui/button'
import { Card } from './ui/card'
import { Input } from './ui/input'

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

function sortChronologically(selections: BookingSelection[]): BookingSelection[] {
  return [...selections].sort((a, b) => Date.parse(a.kickoff) - Date.parse(b.kickoff))
}

export function DashboardPage() {
  const [bookingCodeInput, setBookingCodeInput] = useState('')
  const [originalBookingCode, setOriginalBookingCode] = useState<string | null>(null)
  const [booking, setBooking] = useState<BookingResponse | null>(null)
  const [selectedEventIds, setSelectedEventIds] = useState<Set<string>>(new Set())
  const [loading, setLoading] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [rebooking, setRebooking] = useState(false)
  const [copying, setCopying] = useState(false)
  const [copied, setCopied] = useState(false)
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [view, setView] = useState<'ticket' | 'ended'>('ticket')

  const activeSelections = useMemo(
    () => booking ? sortChronologically(booking.selections.filter((selection) => selection.game_status !== 'ended')) : [],
    [booking],
  )
  const endedSelections = useMemo(
    () => booking ? sortChronologically(booking.selections.filter((selection) => selection.game_status === 'ended')) : [],
    [booking],
  )
  const groups = useMemo(
    () => groupByLocalDate(view === 'ended' ? endedSelections : activeSelections),
    [activeSelections, endedSelections, view],
  )
  const selectedCount = selectedEventIds.size
  const busy = loading || refreshing || rebooking

  const loadTicket = async () => {
    const code = bookingCodeInput.trim()
    setError(null)
    setNotice(null)
    if (!code) return setError('Please enter a SportyBet booking code.')
    setLoading(true)
    try {
      const loaded = await fetchBookingByCode(code)
      setOriginalBookingCode(code)
      setBooking(loaded)
      setSelectedEventIds(new Set())
      setView('ticket')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to load ticket')
    } finally {
      setLoading(false)
    }
  }

  const goBack = () => {
    if (busy) return
    setBooking(null)
    setOriginalBookingCode(null)
    setSelectedEventIds(new Set())
    setError(null)
    setNotice(null)
    setCopied(false)
    setBookingCodeInput('')
    setView('ticket')
  }

  const refreshOriginal = async () => {
    if (!originalBookingCode || busy) return
    setRefreshing(true)
    setError(null)
    setNotice(null)
    try {
      const restored = await fetchBookingByCode(originalBookingCode)
      setBooking(restored)
      setSelectedEventIds(new Set())
      setNotice('Original ticket restored.')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to restore the original ticket')
    } finally {
      setRefreshing(false)
    }
  }

  const copyCode = async () => {
    if (!booking || copying) return
    setCopying(true)
    setCopied(false)
    try {
      await navigator.clipboard.writeText(booking.booking_code)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1800)
    } catch {
      setError('Unable to copy the booking code.')
    } finally {
      setCopying(false)
    }
  }

  const toggleSelection = (eventId: string) => {
    if (busy) return
    setSelectedEventIds((current) => {
      const next = new Set(current)
      if (next.has(eventId)) next.delete(eventId)
      else next.add(eventId)
      return next
    })
  }

  const removeSelected = async () => {
    if (!booking || selectedCount === 0 || busy) return
    setConfirmOpen(false)
    setRebooking(true)
    setError(null)
    setNotice(null)
    try {
      const updated = await removeSelectedGames(booking.booking_code, [...selectedEventIds])
      setBooking(updated)
      setSelectedEventIds(new Set())
      setNotice(`Ticket updated. New booking code: ${updated.booking_code}.`)
    } catch (err) {
      setError(`${err instanceof Error ? err.message : 'Unable to update ticket'} Your current ticket is unchanged.`)
    } finally {
      setRebooking(false)
    }
  }

  const removeBar = (position: 'top' | 'bottom') => (
    <div className="flex items-center justify-between gap-3 rounded-2xl border border-white/10 bg-surface/95 p-3">
      <span className="min-w-0 text-xs text-slate-300">
        {selectedCount ? `${selectedCount} selected` : 'Select games to remove'}
      </span>
      <Button
        size="sm"
        disabled={busy || selectedCount === 0}
        onClick={() => setConfirmOpen(true)}
        aria-label={`Remove Selected (${position})`}
        className="shrink-0 bg-red-500 hover:bg-red-600"
      >
        {rebooking ? <Loader2 className="h-4 w-4 animate-spin" /> : <Trash2 className="h-4 w-4" />}
        <span className="ml-2">{rebooking ? 'Updating…' : 'Remove Selected'}</span>
      </Button>
    </div>
  )

  const gameList = groups.map((group) => (
    <section key={group.date} className="space-y-2.5">
      <div className="flex items-center gap-2 px-1">
        <CalendarDays className="h-4 w-4 text-accent" />
        <h2 className="text-xs font-semibold uppercase tracking-[0.18em] text-white">{formatDateHeader(group.date)}</h2>
        <span className="ml-auto text-[11px] text-slate-500">{group.selections.length} game{group.selections.length === 1 ? '' : 's'}</span>
      </div>
      <div className="space-y-2">
        {group.selections.map((selection) => {
          const selected = selectedEventIds.has(selection.event_id)
          const status = STATUS[selection.game_status]
          return (
            <Card key={selection.event_id} className={`relative p-3 pb-5 pr-12 transition ${selected ? 'border-accent/60 bg-accent/10' : ''}`}>
              <div className="min-w-0">
                <div className="flex items-center justify-between gap-2">
                  <span className="inline-flex items-center gap-1 text-xs font-medium text-accent"><Clock3 className="h-3.5 w-3.5" />{formatTime12(selection.local_kickoff_time)}</span>
                  <span className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${status.classes}`}>{status.label}</span>
                </div>
                <h3 className="mt-1.5 truncate text-sm font-semibold text-white sm:text-base">{selection.home} <span className="text-slate-500">vs</span> {selection.away}</h3>
                <p className="mt-0.5 truncate text-[11px] text-slate-500">{selection.competition}{selection.category ? ` · ${selection.category}` : ''}</p>
                <div className="mt-2 flex items-end justify-between gap-2">
                  <div className="min-w-0 text-xs text-slate-300">
                    <p className="truncate">{selection.market}</p>
                    <p className="truncate font-medium text-accent">{selection.outcome}</p>
                  </div>
                  <p className="shrink-0 text-base font-semibold text-white">{formatOdds(selection.odds)}x</p>
                </div>
              </div>
              <label className="absolute bottom-1 right-1 flex h-10 w-10 cursor-pointer items-center justify-center" title={`Select ${selection.home} vs ${selection.away}`}>
                <input
                  type="checkbox"
                  checked={selected}
                  disabled={busy}
                  onChange={() => toggleSelection(selection.event_id)}
                  aria-label={`Select ${selection.home} vs ${selection.away}`}
                  className="peer sr-only"
                />
                <span className="flex h-[18px] w-[18px] items-center justify-center rounded border border-slate-500 bg-transparent text-transparent transition peer-checked:border-sky-500 peer-checked:bg-sky-500 peer-checked:text-white peer-focus-visible:ring-2 peer-focus-visible:ring-sky-400 peer-focus-visible:ring-offset-2 peer-focus-visible:ring-offset-surface peer-disabled:cursor-not-allowed peer-disabled:opacity-50">
                  <Check className="h-3.5 w-3.5" strokeWidth={3} />
                </span>
              </label>
            </Card>
          )
        })}
      </div>
    </section>
  ))

  if (!booking) {
    return (
      <motion.section initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }}>
        <Card className="mx-auto max-w-2xl">
          <div className="space-y-5">
            <div>
              <p className="text-xs uppercase tracking-[0.3em] text-accent">Amen Booking Optimizer</p>
              <h1 className="mt-2 text-3xl font-semibold text-white">Load your ticket</h1>
              <p className="mt-2 text-sm text-slate-300">Enter a SportyBet booking code to view and optimize it.</p>
            </div>
            <div className="grid gap-3 sm:grid-cols-[1fr_auto]">
              <Input
                aria-label="SportyBet booking code"
                value={bookingCodeInput}
                onChange={(event) => setBookingCodeInput(event.target.value.toUpperCase())}
                onKeyDown={(event: KeyboardEvent<HTMLInputElement>) => event.key === 'Enter' && loadTicket()}
                placeholder="Booking code (e.g. HW7UDH)"
                disabled={loading}
              />
              <Button size="lg" onClick={loadTicket} disabled={loading}>
                {loading && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                {loading ? 'Loading ticket…' : 'Load Ticket'}
              </Button>
            </div>
            {error && <div className="rounded-2xl border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-200">{error}</div>}
          </div>
        </Card>
      </motion.section>
    )
  }

  if (view === 'ended') {
    return (
      <div className="mx-auto max-w-3xl space-y-4 overflow-x-hidden">
        <header className="flex items-center gap-3">
          <Button variant="outline" size="sm" onClick={() => setView('ticket')} disabled={busy} aria-label="Back to ticket details">
            <ArrowLeft className="h-4 w-4" /><span className="ml-1.5">Back</span>
          </Button>
          <h1 className="text-sm font-semibold uppercase tracking-[0.2em] text-slate-200">Ended Games</h1>
          <span className="ml-auto text-xs text-slate-500">{endedSelections.length}</span>
        </header>
        {error && <div className="rounded-2xl border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-200">{error}</div>}
        {rebooking && <div className="flex items-center gap-2 rounded-2xl border border-accent/30 bg-accent/10 p-3 text-sm text-accent"><Loader2 className="h-4 w-4 animate-spin" />SportyBet is generating the updated ticket…</div>}
        {endedSelections.length > 0 && removeBar('top')}
        {gameList}
        {endedSelections.length === 0 && <Card className="p-4 text-sm text-slate-300">This ticket has no ended games.</Card>}
        {endedSelections.length > 0 && removeBar('bottom')}
        <AnimatePresence>
          {confirmOpen && (
            <motion.div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 p-4" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} role="dialog" aria-modal="true">
              <motion.div className="w-full max-w-sm rounded-3xl border border-white/10 bg-surface p-5" initial={{ scale: 0.95 }} animate={{ scale: 1 }} exit={{ scale: 0.95 }}>
                <div className="flex gap-3">
                  <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-red-300" />
                  <div className="min-w-0 flex-1"><h3 className="font-semibold text-white">Remove {selectedCount} selected game{selectedCount === 1 ? '' : 's'}?</h3><p className="mt-2 text-sm leading-6 text-slate-300">SportyBet will create one new ticket from all remaining games.</p></div>
                  <button onClick={() => setConfirmOpen(false)} aria-label="Close confirmation"><X className="h-5 w-5 text-slate-400" /></button>
                </div>
                <div className="mt-5 flex justify-end gap-2"><Button variant="outline" size="sm" onClick={() => setConfirmOpen(false)}>Cancel</Button><Button size="sm" onClick={removeSelected} className="bg-red-500 hover:bg-red-600">Remove Selected</Button></div>
              </motion.div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-3xl space-y-4 overflow-x-hidden">
      <header className="space-y-3">
        <div className="flex items-center justify-between gap-2">
          <Button variant="outline" size="sm" onClick={goBack} disabled={busy} aria-label="Back to booking input">
            <ArrowLeft className="h-4 w-4" /><span className="ml-1.5">Back</span>
          </Button>
          <p className="text-sm font-semibold uppercase tracking-[0.2em] text-slate-300">Ticket</p>
          <Button variant="outline" size="sm" onClick={refreshOriginal} disabled={busy} aria-label="Restore original ticket">
            <RefreshCw className={`h-4 w-4 ${refreshing ? 'animate-spin' : ''}`} />
            <span className="ml-1.5">{refreshing ? 'Refreshing…' : 'Refresh'}</span>
          </Button>
        </div>

        <Card className="p-4 sm:p-5">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="text-[11px] uppercase tracking-[0.25em] text-slate-500">Booking code</p>
              <p className="mt-1 truncate text-2xl font-semibold tracking-wide text-white">{booking.booking_code}</p>
            </div>
            <button onClick={copyCode} disabled={copying} className="flex min-h-10 shrink-0 items-center gap-1.5 rounded-xl border border-white/10 px-3 text-xs text-slate-200 hover:bg-white/5">
              {copied ? <Check className="h-4 w-4 text-emerald-300" /> : <Clipboard className="h-4 w-4" />}
              {copying ? 'Copying…' : copied ? 'Copied' : 'Copy'}
            </button>
          </div>
          <div className="mt-4 grid grid-cols-2 gap-3">
            <div className="rounded-xl bg-white/5 p-3">
              <p className="text-[10px] uppercase tracking-[0.2em] text-slate-500">Selections</p>
              <p className="mt-1 text-lg font-semibold text-white">{booking.total_selections}</p>
            </div>
            <div className="rounded-xl bg-accent/10 p-3">
              <p className="text-[10px] uppercase tracking-[0.2em] text-slate-400">Remaining odds</p>
              <p className="mt-1 truncate text-lg font-semibold text-accent">{formatOdds(booking.remaining_odds)}x</p>
            </div>
          </div>
          {endedSelections.length > 0 && (
            <button
              type="button"
              onClick={() => setView('ended')}
              className="mt-3 flex min-h-11 w-full items-center justify-between rounded-xl border border-white/10 bg-white/[0.03] px-3 text-left text-xs font-semibold uppercase tracking-[0.14em] text-slate-200 transition hover:bg-white/[0.06]"
              aria-label={`View ${endedSelections.length} ended games`}
            >
              <span>Ended Games <span className="ml-1 text-red-300">{endedSelections.length}</span></span>
              <ChevronRight className="h-4 w-4 text-slate-500" />
            </button>
          )}
          <p className="mt-3 text-[11px] text-slate-500">Times shown in Africa/Lagos (UTC+1).</p>
        </Card>
      </header>

      {error && <div className="rounded-2xl border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-200">{error}</div>}
      {notice && <div className="rounded-2xl border border-emerald-500/30 bg-emerald-500/10 p-3 text-sm text-emerald-200">{notice}</div>}
      {rebooking && <div className="flex items-center gap-2 rounded-2xl border border-accent/30 bg-accent/10 p-3 text-sm text-accent"><Loader2 className="h-4 w-4 animate-spin" />SportyBet is generating the updated ticket…</div>}

      {activeSelections.length > 0 && removeBar('top')}
      {gameList}
      {activeSelections.length === 0 && <Card className="p-4 text-sm text-slate-300">This ticket has no live or upcoming games.</Card>}
      {activeSelections.length > 0 && removeBar('bottom')}

      <AnimatePresence>
        {confirmOpen && (
          <motion.div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 p-4" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} role="dialog" aria-modal="true">
            <motion.div className="w-full max-w-sm rounded-3xl border border-white/10 bg-surface p-5" initial={{ scale: 0.95 }} animate={{ scale: 1 }} exit={{ scale: 0.95 }}>
              <div className="flex gap-3">
                <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-red-300" />
                <div className="min-w-0 flex-1">
                  <h3 className="font-semibold text-white">Remove {selectedCount} selected game{selectedCount === 1 ? '' : 's'}?</h3>
                  <p className="mt-2 text-sm leading-6 text-slate-300">SportyBet will create one new ticket from all remaining games.</p>
                </div>
                <button onClick={() => setConfirmOpen(false)} aria-label="Close confirmation"><X className="h-5 w-5 text-slate-400" /></button>
              </div>
              <div className="mt-5 flex justify-end gap-2">
                <Button variant="outline" size="sm" onClick={() => setConfirmOpen(false)}>Cancel</Button>
                <Button size="sm" onClick={removeSelected} className="bg-red-500 hover:bg-red-600">Remove Selected</Button>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
