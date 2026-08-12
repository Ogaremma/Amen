import { useMemo, useState, type KeyboardEvent } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { CalendarDays, Clock3, Loader2, Trash2, AlertTriangle, X, CheckCircle2 } from 'lucide-react'
import { Button } from './ui/button'
import { Card } from './ui/card'
import { Input } from './ui/input'
import { fetchBookingByCode, removeSelectedGames } from '../lib/api'
import type { BookingResponse, BookingSelection } from '../types/booking'

const MONTHS = [
  'JANUARY', 'FEBRUARY', 'MARCH', 'APRIL', 'MAY', 'JUNE',
  'JULY', 'AUGUST', 'SEPTEMBER', 'OCTOBER', 'NOVEMBER', 'DECEMBER',
]

// Format the backend's UTC date string "2026-08-10" -> "AUGUST 10, 2026"
// via string parsing so grouping stays in the same (UTC) frame as the time.
function formatDateHeader(isoDate: string): string {
  const [year, month, day] = isoDate.split('-').map(Number)
  if (!year || !month || !day) return isoDate
  return `${MONTHS[month - 1]} ${day}, ${year}`
}

// Format the backend's UTC 24h time "20:00" -> "8:00 PM".
function formatTime12(hhmm: string): string {
  const [hRaw, m] = hhmm.split(':')
  const h = Number(hRaw)
  if (Number.isNaN(h)) return hhmm
  const period = h >= 12 ? 'PM' : 'AM'
  const hour12 = h % 12 === 0 ? 12 : h % 12
  return `${hour12}:${m ?? '00'} ${period}`
}

function formatOdds(odds: number | null): string {
  if (odds === null || odds === undefined) return '—'
  return odds.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

interface DateGroup {
  date: string
  selections: BookingSelection[]
}

// Backend already returns selections sorted by complete kickoff datetime,
// so we only need to partition into contiguous date groups (order preserved).
function groupByDate(selections: BookingSelection[]): DateGroup[] {
  const groups: DateGroup[] = []
  for (const selection of selections) {
    const last = groups[groups.length - 1]
    if (last && last.date === selection.kickoff_date) {
      last.selections.push(selection)
    } else {
      groups.push({ date: selection.kickoff_date, selections: [selection] })
    }
  }
  return groups
}

export function DashboardPage() {
  const [bookingCode, setBookingCode] = useState('')
  const [booking, setBooking] = useState<BookingResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Games MARKED for removal (frontend-only until "Remove Selected" is confirmed).
  // Keyed by event_id — NOT array index — because the whole booking is replaced
  // after every rebooking and indices are meaningless across replacements.
  const [selectedEventIds, setSelectedEventIds] = useState<Set<string>>(new Set())

  const [rebooking, setRebooking] = useState(false)
  const [removeError, setRemoveError] = useState<string | null>(null)
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [updatedNote, setUpdatedNote] = useState<string | null>(null)

  const groups = useMemo(
    () => (booking ? groupByDate(booking.selections) : []),
    [booking],
  )

  const selectedCount = selectedEventIds.size
  // While a rebooking is in flight, every control is locked (input, Load, all
  // checkboxes and both Remove buttons) so no two operations can race.
  const busy = loading || rebooking

  const handleLoad = async () => {
    const code = bookingCode.trim()
    setError(null)
    setRemoveError(null)
    setUpdatedNote(null)
    if (!code) {
      setError('Please enter a SportyBet booking code.')
      return
    }

    setLoading(true)
    setBooking(null)
    setSelectedEventIds(new Set())
    try {
      const response = await fetchBookingByCode(code)
      setBooking(response)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to load booking')
    } finally {
      setLoading(false)
    }
  }

  const toggleSelect = (eventId: string) => {
    if (busy) return
    setUpdatedNote(null)
    setSelectedEventIds((prev) => {
      const next = new Set(prev)
      if (next.has(eventId)) next.delete(eventId)
      else next.add(eventId)
      return next
    })
  }

  const openConfirm = () => {
    if (busy || selectedCount === 0) return
    setRemoveError(null)
    setConfirmOpen(true)
  }

  // ATOMIC: the displayed booking is replaced ONLY after the backend returns a
  // full new ticket. Exactly ONE request is sent for the entire batch. On any
  // failure we keep the current booking untouched and surface an error.
  const handleConfirmRemove = async () => {
    if (!booking) return
    setConfirmOpen(false)
    setRebooking(true)
    setRemoveError(null)
    setUpdatedNote(null)
    const eventIds = Array.from(selectedEventIds)
    try {
      const updated = await removeSelectedGames(booking.booking_code, eventIds)
      setBooking(updated)
      setSelectedEventIds(new Set())
      setUpdatedNote(
        `Booking updated — new code ${updated.booking_code}, ${updated.total_selections} games remaining.`,
      )
    } catch (err) {
      setRemoveError(
        err instanceof Error ? err.message : 'Unable to remove selections and rebook',
      )
    } finally {
      setRebooking(false)
    }
  }

  const handleKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Enter') handleLoad()
  }

  const removeLabel = selectedCount > 0 ? `Remove Selected (${selectedCount})` : 'Remove Selected'

  const renderRemoveBar = (position: 'top' | 'bottom') => (
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-white/10 bg-white/5 px-4 py-3">
      <p className="text-sm text-slate-300">
        {selectedCount > 0
          ? `${selectedCount} game${selectedCount === 1 ? '' : 's'} marked for removal`
          : 'Tick the games you want to remove'}
      </p>
      <Button
        variant="default"
        size="sm"
        onClick={openConfirm}
        disabled={busy || selectedCount === 0}
        aria-label={`${removeLabel} (${position} of list)`}
        className="bg-red-500 hover:bg-red-600 disabled:opacity-40"
      >
        {rebooking ? (
          <>
            <Loader2 className="h-4 w-4 animate-spin" />
            <span className="ml-2">Updating…</span>
          </>
        ) : (
          <>
            <Trash2 className="h-4 w-4" />
            <span className="ml-2">{removeLabel}</span>
          </>
        )}
      </Button>
    </div>
  )

  return (
    <div className="space-y-8">
      {/* Header + input */}
      <motion.section
        initial={{ opacity: 0, y: 18 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4 }}
      >
        <Card>
          <div className="flex flex-col gap-5">
            <div className="space-y-2">
              <p className="text-sm uppercase tracking-[0.35em] text-accent">Amen Optimizer</p>
              <h1 className="text-3xl font-semibold text-white sm:text-4xl">
                Load a SportyBet booking
              </h1>
              <p className="max-w-2xl text-sm leading-6 text-slate-300">
                Enter a SportyBet booking code to retrieve the ticket, then tick any games
                you want to drop and remove them together in one rebooking.
              </p>
            </div>

            <div className="grid gap-3 sm:grid-cols-[1fr_auto]">
              <Input
                aria-label="SportyBet booking code"
                value={bookingCode}
                onChange={(event) => setBookingCode(event.target.value.toUpperCase())}
                onKeyDown={handleKeyDown}
                placeholder="Enter SportyBet booking code (e.g. HW7UDH)"
                autoCapitalize="characters"
                spellCheck={false}
                disabled={busy}
              />
              <Button onClick={handleLoad} disabled={busy} size="lg">
                {loading ? 'Loading…' : 'Load Booking'}
              </Button>
            </div>

            {error && (
              <div className="rounded-2xl border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-200">
                {error}
              </div>
            )}
          </div>
        </Card>
      </motion.section>

      {/* Summary */}
      {booking && (
        <motion.section
          initial={{ opacity: 0, y: 18 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4 }}
        >
          <Card className="bg-gradient-to-br from-primary/70 to-secondary/60">
            <p className="text-xs uppercase tracking-[0.3em] text-slate-300">SportyBet booking</p>
            <div className="mt-3 grid gap-4 sm:grid-cols-3">
              <div className="rounded-2xl bg-white/5 p-5">
                <p className="text-xs uppercase tracking-[0.28em] text-slate-400">Booking code</p>
                <p className="mt-2 text-2xl font-semibold text-white">{booking.booking_code}</p>
              </div>
              <div className="rounded-2xl bg-white/5 p-5">
                <p className="text-xs uppercase tracking-[0.28em] text-slate-400">Total selections</p>
                <p className="mt-2 text-2xl font-semibold text-white">{booking.total_selections}</p>
              </div>
              <div className="rounded-2xl bg-white/5 p-5">
                <p className="text-xs uppercase tracking-[0.28em] text-slate-400">Total odds</p>
                <p className="mt-2 text-2xl font-semibold text-accent">
                  {formatOdds(booking.total_odds)}
                </p>
              </div>
            </div>

            {rebooking && (
              <div className="mt-4 flex items-center gap-2 rounded-2xl border border-accent/30 bg-accent/10 p-4 text-sm text-accent">
                <Loader2 className="h-4 w-4 animate-spin" />
                Updating booking with SportyBet… generating a new code and refreshing odds.
              </div>
            )}

            {updatedNote && !rebooking && (
              <div className="mt-4 flex items-center gap-2 rounded-2xl border border-emerald-500/30 bg-emerald-500/10 p-4 text-sm text-emerald-200">
                <CheckCircle2 className="h-4 w-4" />
                {updatedNote}
              </div>
            )}

            {removeError && !rebooking && (
              <div className="mt-4 rounded-2xl border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-200">
                {removeError} — your current booking is unchanged.
              </div>
            )}
          </Card>
        </motion.section>
      )}

      {/* Empty state */}
      {booking && booking.selections.length === 0 && (
        <Card>
          <p className="text-sm text-slate-300">
            This booking returned no resolvable football selections.
          </p>
        </Card>
      )}

      {/* Top Remove Selected bar */}
      {booking && booking.selections.length > 0 && renderRemoveBar('top')}

      {/* Selections grouped by date */}
      {groups.map((group, groupIndex) => (
        <motion.section
          key={group.date}
          initial={{ opacity: 0, y: 18 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.35, delay: 0.05 + groupIndex * 0.04 }}
          className="space-y-4"
        >
          <div className="flex items-center gap-3">
            <span className="flex h-10 w-10 items-center justify-center rounded-2xl bg-accent/15 text-accent">
              <CalendarDays className="h-5 w-5" />
            </span>
            <h2 className="text-lg font-semibold uppercase tracking-[0.18em] text-white">
              {formatDateHeader(group.date)}
            </h2>
            <span className="ml-auto rounded-full border border-white/10 bg-white/5 px-3 py-1 text-xs text-slate-300">
              {group.selections.length} game{group.selections.length === 1 ? '' : 's'}
            </span>
          </div>

          <div className="space-y-3">
            {group.selections.map((selection) => {
              const checked = selectedEventIds.has(selection.event_id)
              return (
                <Card
                  key={selection.event_id}
                  className={`p-5 transition-colors ${
                    checked ? 'border-accent/60 bg-accent/10' : ''
                  }`}
                >
                  <div className="flex items-start gap-4">
                    {/* Checkbox — large tap target for mobile */}
                    <label
                      className="flex cursor-pointer items-center pt-1"
                      aria-label={`Mark ${selection.home} vs ${selection.away} for removal`}
                    >
                      <input
                        type="checkbox"
                        checked={checked}
                        disabled={busy}
                        onChange={() => toggleSelect(selection.event_id)}
                        className="h-6 w-6 shrink-0 cursor-pointer rounded-md border-white/30 bg-white/10 text-accent accent-sky-500 focus:ring-2 focus:ring-accent disabled:cursor-not-allowed disabled:opacity-40"
                      />
                    </label>

                    <div className="flex flex-1 flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
                      <div className="space-y-2">
                        <div className="inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/5 px-3 py-1 text-xs text-slate-300">
                          <Clock3 className="h-3.5 w-3.5 text-accent" />
                          {formatTime12(selection.kickoff_time)}
                        </div>
                        <h3 className="text-xl font-semibold text-white">
                          {selection.home} <span className="text-slate-500">vs</span> {selection.away}
                        </h3>
                        <p className="text-xs uppercase tracking-[0.2em] text-slate-500">
                          {selection.competition}
                          {selection.category ? ` · ${selection.category}` : ''}
                        </p>
                        <div className="flex flex-wrap gap-2 pt-1">
                          <span className="rounded-full bg-white/5 px-3 py-1 text-sm text-slate-300">
                            {selection.market}
                          </span>
                          <span className="rounded-full bg-accent/15 px-3 py-1 text-sm font-medium text-accent">
                            {selection.outcome}
                          </span>
                          {selection.status && (
                            <span className="rounded-full bg-white/5 px-3 py-1 text-xs text-slate-400">
                              {selection.status}
                            </span>
                          )}
                        </div>
                      </div>

                      <div className="flex shrink-0 flex-col items-start sm:items-end">
                        <p className="text-xs uppercase tracking-[0.24em] text-slate-500">Odds</p>
                        <p className="text-3xl font-semibold text-white">{formatOdds(selection.odds)}</p>
                      </div>
                    </div>
                  </div>
                </Card>
              )
            })}
          </div>
        </motion.section>
      ))}

      {/* Bottom Remove Selected bar (same action as the top one) */}
      {booking && booking.selections.length > 0 && renderRemoveBar('bottom')}

      {/* Confirmation dialog */}
      <AnimatePresence>
        {confirmOpen && (
          <motion.div
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            role="dialog"
            aria-modal="true"
            aria-labelledby="confirm-title"
          >
            <motion.div
              className="w-full max-w-md rounded-3xl border border-white/10 bg-surface p-6 shadow-2xl"
              initial={{ scale: 0.94, y: 12 }}
              animate={{ scale: 1, y: 0 }}
              exit={{ scale: 0.94, y: 12 }}
            >
              <div className="flex items-start gap-3">
                <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-2xl bg-red-500/15 text-red-300">
                  <AlertTriangle className="h-5 w-5" />
                </span>
                <div className="flex-1">
                  <h3 id="confirm-title" className="text-lg font-semibold text-white">
                    Remove {selectedCount} selected game{selectedCount === 1 ? '' : 's'}?
                  </h3>
                  <p className="mt-2 text-sm leading-6 text-slate-300">
                    SportyBet will generate a new booking code with the remaining games and
                    recalculate the odds. This cannot be undone.
                  </p>
                </div>
                <button
                  onClick={() => setConfirmOpen(false)}
                  className="text-slate-400 hover:text-white"
                  aria-label="Close dialog"
                >
                  <X className="h-5 w-5" />
                </button>
              </div>

              <div className="mt-6 flex justify-end gap-3">
                <Button variant="outline" size="sm" onClick={() => setConfirmOpen(false)}>
                  Cancel
                </Button>
                <Button
                  size="sm"
                  onClick={handleConfirmRemove}
                  className="bg-red-500 hover:bg-red-600"
                >
                  <Trash2 className="h-4 w-4" />
                  <span className="ml-2">Remove Games</span>
                </Button>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
