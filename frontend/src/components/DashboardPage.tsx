import { useMemo, useRef, useState, type KeyboardEvent } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import {
  AlertTriangle,
  ArrowLeft,
  CalendarDays,
  Check,
  CheckCheck,
  ChevronRight,
  Clipboard,
  Copy,
  Clock3,
  CircleHelp,
  Loader2,
  Eye,
  EyeOff,
  Edit3,
  Plus,
  RefreshCw,
  Trash2,
  X,
  History as HistoryIcon,
} from 'lucide-react'
import { deleteHistoryItem, fetchBookingByCode, fetchHistory, removeSelectedGames, type HistoryItem } from '../lib/api'
import { copyTextToClipboard } from '../lib/clipboard'
import type { BookingResponse, BookingSelection } from '../types/booking'
import { Button } from './ui/button'
import { Card } from './ui/card'
import { selectSplitGames, type SplitMode } from '../lib/splitter'

function SwipeHistoryCard({ item, onOpen, onDeleted, onCopy, copied, copyError }: { item: HistoryItem; onOpen: (code: string) => void; onDeleted: () => void; onCopy: (code: string) => void; copied: boolean; copyError: boolean }) {
  const start = useRef<{ x: number; y: number } | null>(null)
  const [offset, setOffset] = useState(0)
  const [deleting, setDeleting] = useState(false)
  const end = async (x: number, y: number) => {
    const origin = start.current; start.current = null
    if (!origin) return
    const dx = x - origin.x; const dy = y - origin.y
    if (Math.abs(dx) <= Math.abs(dy) || Math.abs(dx) < 100) { setOffset(0); return }
    setDeleting(true); setOffset(dx > 0 ? 700 : -700)
    try { await deleteHistoryItem(item.id); window.setTimeout(onDeleted, 180) } catch { setDeleting(false); setOffset(0) }
  }
  return <div className="relative overflow-hidden rounded-2xl bg-red-600"><div className="absolute inset-0 flex items-center justify-center text-sm font-bold tracking-[0.2em] text-white">DELETE</div><Card className="relative overflow-hidden p-0" style={{ transform: `translateX(${offset}px)`, transition: deleting ? 'transform 180ms ease-out' : 'none', touchAction: 'pan-y' }} onPointerDown={(e) => { start.current = { x: e.clientX, y: e.clientY }; e.currentTarget.setPointerCapture?.(e.pointerId) }} onPointerMove={(e) => { if (start.current) { const dx = e.clientX - start.current.x; if (Math.abs(dx) > 8) setOffset(dx) } }} onPointerUp={(e) => void end(e.clientX, e.clientY)} onPointerCancel={() => { start.current = null; setOffset(0) }}><div className="flex items-center gap-3 p-4"><button type="button" className="min-w-0 flex-1 text-left" onClick={() => { if (!deleting && Math.abs(offset) < 10) onOpen(item.booking_code) }}><p className="font-semibold tracking-wide text-white">{item.booking_code}</p><p className="mt-1 text-xs text-slate-400">Loaded {new Date(item.loaded_at).toLocaleString()}</p></button><div className="flex items-center gap-2"><span role="status" aria-live="polite" className="min-w-[5.5rem] text-right text-[11px] text-emerald-300">{copied ? 'Copied to clipboard' : copyError ? 'Copy failed' : ''}</span><button type="button" aria-label={`Copy ${item.booking_code}`} onClick={(e) => { e.stopPropagation(); onCopy(item.booking_code) }} className="rounded-xl border border-white/10 p-2 text-slate-300"><Clipboard className="h-4 w-4" /></button></div></div></Card></div>
}
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
  const [view, setView] = useState<'ticket' | 'ended' | 'splitter'>('ticket')
  type SplitCard = { id: number; state: 'empty' | 'selecting' | 'generating' | 'result' | 'error'; mode?: SplitMode; count?: number; code?: string; error?: string; games?: BookingSelection[]; revealed?: boolean }
  const [splitters, setSplitters] = useState<SplitCard[]>([{ id: 0, state: 'empty' }])
  const [historyOpen, setHistoryOpen] = useState(false)
  const [history, setHistory] = useState<HistoryItem[]>([])
  const [historyCopying, setHistoryCopying] = useState<string | null>(null)
  const [historyCopied, setHistoryCopied] = useState<string | null>(null)
  const [historyCopyError, setHistoryCopyError] = useState<string | null>(null)

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
  const allActiveSelected = activeSelections.length > 0
    && activeSelections.every((selection) => selectedEventIds.has(selection.event_id))
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
      setSplitters([{ id: 0, state: 'empty' }])
      setView('ticket')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to load ticket')
    } finally {
      setLoading(false)
    }
  }

  const openHistory = async () => {
    try { setHistory(await fetchHistory()); setHistoryOpen(true) }
    catch (err) { setError(err instanceof Error ? err.message : 'Unable to load history') }
  }

  const reopenHistory = async (code: string) => {
    setHistoryOpen(false); setBookingCodeInput(code); setLoading(true)
    try { const loaded = await fetchBookingByCode(code); setOriginalBookingCode(code); setBooking(loaded); setSelectedEventIds(new Set()); setSplitters([{ id: 0, state: 'empty' }]); setView('ticket') }
    catch (err) { setError(err instanceof Error ? err.message : 'Unable to load ticket') }
    finally { setLoading(false) }
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
      setSplitters([{ id: 0, state: 'empty' }])
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
      if (!await copyTextToClipboard(booking.booking_code)) throw new Error('Clipboard write failed')
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1800)
    } catch {
      setError('Unable to copy the booking code.')
    } finally {
      setCopying(false)
    }
  }

  const copyHistoryCode = async (code: string) => {
    if (historyCopying) return
    setHistoryCopying(code)
    setHistoryCopied(null)
    setHistoryCopyError(null)
    try {
      if (!await copyTextToClipboard(code)) throw new Error('Clipboard write failed')
      setHistoryCopied(code)
      window.setTimeout(() => setHistoryCopied((current) => current === code ? null : current), 1800)
    } catch {
      setHistoryCopyError(code)
      window.setTimeout(() => setHistoryCopyError((current) => current === code ? null : current), 2400)
    } finally {
      setHistoryCopying(null)
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

  const toggleAllActiveSelections = () => {
    if (busy || activeSelections.length === 0) return
    setSelectedEventIds((current) => {
      const next = new Set(current)
      if (activeSelections.every((selection) => current.has(selection.event_id))) {
        activeSelections.forEach((selection) => next.delete(selection.event_id))
      } else {
        activeSelections.forEach((selection) => next.add(selection.event_id))
      }
      return next
    })
  }

  const toggleDateSelections = (selections: BookingSelection[]) => {
    if (busy || selections.length === 0) return
    setSelectedEventIds((current) => {
      const next = new Set(current)
      if (selections.every((selection) => current.has(selection.event_id))) {
        selections.forEach((selection) => next.delete(selection.event_id))
      } else {
        selections.forEach((selection) => next.add(selection.event_id))
      }
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

  const runSplitter = async (id: number, mode: SplitMode) => {
    if (!booking) return
    setSplitters((cards) => cards.map((card) => card.id === id ? { ...card, state: 'selecting', mode, error: undefined } : card))
  }

  const submitSplitter = async (id: number, raw: string) => {
    const card = splitters.find((item) => item.id === id)
    if (!booking || !card?.mode) return
    const count = Number(raw)
    const total = booking.selections.length
    if (!Number.isInteger(count) || count < 1 || count > total) {
      setSplitters((cards) => cards.map((item) => item.id === id ? { ...item, state: 'error', error: `Enter a number between 1 and ${total}.` } : item))
      return
    }
    const allGames = sortChronologically(booking.selections)
    const chosen = selectSplitGames(allGames, card.mode, count)
    const omitted = allGames.filter((g) => !chosen.some((c) => c.event_id === g.event_id)).map((g) => g.event_id)
    setSplitters((cards) => cards.map((item) => item.id === id ? { ...item, state: 'generating', count, error: undefined } : item))
    try {
      const result = await removeSelectedGames(booking.booking_code, omitted)
      setSplitters((cards) => cards.map((item) => item.id === id ? { ...item, state: 'result', code: result.booking_code, games: chosen, revealed: false } : item))
    } catch (err) {
      setSplitters((cards) => cards.map((item) => item.id === id ? { ...item, state: 'error', error: err instanceof Error ? err.message : 'Unable to create split booking.' } : item))
    }
  }

  const resetSplitter = (id: number) => setSplitters((cards) => cards.map((item) => item.id === id ? { id, state: 'empty' } : item))
  const addSplitter = () => setSplitters((cards) => [...cards, { id: Date.now(), state: 'empty' }])
  const copyValue = async (value: string) => { try { await copyTextToClipboard(value) } catch { setError('Unable to copy to clipboard.') } }
  const copyAllSplits = async () => { const text = splitters.filter((c) => c.state === 'result' && c.code && c.mode && c.count).map((c) => `${c.mode![0].toUpperCase()}${c.mode!.slice(1)} ${c.count} from ${originalBookingCode}\n${c.code}`).join('\n\n'); if (text) await copyValue(text) }

  const removeBar = (position: 'top' | 'bottom', showSelectAll = false) => (
    <div className="grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-2 rounded-2xl border border-white/10 bg-surface/95 p-2.5 sm:gap-3 sm:p-3">
      <div className="min-w-0">
        {showSelectAll && (
          <button
            type="button"
            onClick={toggleAllActiveSelections}
            disabled={busy || activeSelections.length === 0}
            aria-label={allActiveSelected ? 'Deselect all live and upcoming games' : 'Select all live and upcoming games'}
            className={`flex min-h-9 shrink-0 items-center gap-1.5 whitespace-nowrap rounded-xl border px-2.5 text-[11px] font-semibold transition disabled:cursor-not-allowed disabled:opacity-50 ${allActiveSelected ? 'border-red-400/40 bg-red-500/15 text-red-200' : 'border-white/10 bg-white/[0.03] text-slate-300 hover:bg-white/[0.06]'}`}
          >
            <CheckCheck className="h-3.5 w-3.5" />
            {allActiveSelected ? 'Deselect All' : 'Select All'}
          </button>
        )}
        <span className="min-w-0 truncate text-center text-[11px] text-slate-400 sm:text-xs">
          {selectedCount ? `${selectedCount} selected` : 'Select games to remove'}
        </span>
      </div>
      <Button
        size="sm"
        disabled={busy || selectedCount === 0}
        onClick={() => setConfirmOpen(true)}
        aria-label={`Remove Selected (${position})`}
        className="shrink-0 whitespace-nowrap bg-red-500 px-2.5 hover:bg-red-600 sm:px-3"
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
        <h2 className="date-section-heading text-xs font-bold uppercase tracking-[0.18em] text-yellow-300">{formatDateHeader(group.date)}</h2>
        <div className="ml-auto flex items-center gap-2">
          <span className="text-[11px] text-slate-500">{group.selections.length} game{group.selections.length === 1 ? '' : 's'}</span>
          <button
            type="button"
            onClick={() => toggleDateSelections(group.selections)}
            disabled={busy || group.selections.length === 0}
            aria-label={`${group.selections.every((selection) => selectedEventIds.has(selection.event_id)) ? 'Deselect' : 'Select'} all games on ${formatDateHeader(group.date)}`}
            className={`flex h-8 w-8 items-center justify-center rounded-lg border transition disabled:cursor-not-allowed disabled:opacity-50 ${group.selections.every((selection) => selectedEventIds.has(selection.event_id)) ? 'border-red-400/40 bg-red-500/15 text-red-200' : 'border-white/10 bg-white/[0.03] text-slate-300 hover:bg-white/[0.06]'}`}
          >
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
          return (
            <Card
              key={selection.event_id}
              data-selected={selected ? 'true' : 'false'}
              role="button"
              tabIndex={busy ? -1 : 0}
              aria-pressed={selected}
              aria-disabled={busy}
              aria-label={`Toggle selection for ${selection.home} vs ${selection.away}`}
              onClick={() => toggleSelection(selection.event_id)}
              onKeyDown={(event: KeyboardEvent<HTMLDivElement>) => {
                if (event.key === 'Enter' || event.key === ' ') {
                  event.preventDefault()
                  toggleSelection(selection.event_id)
                }
              }}
              className={`relative cursor-pointer p-3 pb-5 pr-12 transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-400 focus-visible:ring-offset-2 focus-visible:ring-offset-surface ${selected ? 'border-red-400/60 bg-red-500/10' : ''}`}
            >
              <div className="min-w-0">
                <div className="flex items-center justify-between gap-2">
                  <span className="inline-flex items-center gap-1 text-xs font-medium text-accent"><Clock3 className="h-3.5 w-3.5" />{formatTime12(selection.local_kickoff_time)}</span>
                  <span className="flex items-center gap-2">
                    <span
                      role="img"
                      aria-label={`Bet result: ${result.label}`}
                      title={`Bet result: ${result.label}`}
                      className={`inline-flex h-4 w-4 items-center justify-center ${result.classes}`}
                    >
                      {ResultIcon ? <ResultIcon className="h-3.5 w-3.5" strokeWidth={3} /> : <span className="h-2 w-2 rounded-full bg-current" />}
                    </span>
                    <span className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${status.classes}`}>{status.label}</span>
                  </span>
                </div>
                <h3 className="mt-1.5 truncate text-sm font-semibold text-white sm:text-base">{selection.home} <span className="text-slate-500">vs</span> {selection.away}</h3>
                <p className="mt-0.5 truncate text-[11px] text-slate-500">{selection.competition}{selection.category ? ` · ${selection.category}` : ''}</p>
                <div className="mt-2 flex items-end justify-between gap-2">
                  <div className="min-w-0 text-xs text-slate-300">
                    <p className="truncate">{selection.market}</p>
                    <p className="truncate font-medium text-accent">{selection.outcome}</p>
                  </div>
                  <p className="shrink-0 text-base font-semibold text-white">
                    {selection.odds === null ? '—' : `${formatOdds(selection.odds)}x`}
                  </p>
                </div>
              </div>
              <span className="absolute bottom-1 right-1 flex h-10 w-10 items-center justify-center" title={`${selected ? 'Deselect' : 'Select'} ${selection.home} vs ${selection.away}`} aria-hidden="true">
                <span data-testid={`selection-control-${selection.event_id}`} className={`flex h-4 w-4 items-center justify-center rounded border transition ${selected ? 'border-red-500 bg-red-500 text-white' : 'border-slate-500 bg-transparent text-transparent'}`}>
                  <Check className="h-3.5 w-3.5" strokeWidth={3} />
                </span>
              </span>
            </Card>
          )
        })}
      </div>
    </section>
  ))

  if (!booking) {
    if (historyOpen) return <div className="mx-auto max-w-3xl space-y-4"><header className="flex items-center gap-3"><Button variant="outline" size="sm" onClick={() => setHistoryOpen(false)} aria-label="Back to booking input"><ArrowLeft className="h-4 w-4" /><span className="ml-1.5">Back</span></Button><h1 className="text-sm font-semibold uppercase tracking-[0.2em] text-slate-200">History</h1></header>{history.length === 0 ? <Card className="p-4 text-sm text-slate-300">No booking codes yet.</Card> : history.map((item) => <SwipeHistoryCard key={item.id} item={item} onOpen={reopenHistory} onCopy={copyHistoryCode} copied={historyCopied === item.booking_code} copyError={historyCopyError === item.booking_code} onDeleted={() => setHistory((items) => items.filter((entry) => entry.id !== item.id))} />)}</div>
    return (
      <motion.section initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }}>
        <Card className="mx-auto max-w-2xl">
          <div className="space-y-5">
            <div>
              <p className="text-xs uppercase tracking-[0.3em] text-accent">Amen Booking Optimizer</p>
              <h1 className="mt-2 text-3xl font-semibold text-white">Load your ticket</h1>
              <p className="mt-2 text-sm text-slate-300">Enter a SportyBet booking code to view and optimize it.</p>
            </div>
            <div className="grid gap-3 sm:grid-cols-[1fr_auto_auto]">
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
              <Button variant="outline" size="lg" onClick={openHistory} disabled={loading} aria-label="Open booking history"><HistoryIcon className="h-4 w-4" /><span className="ml-1.5">History</span></Button>
            </div>
            {error && <div className="rounded-2xl border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-200">{error}</div>}
          </div>
        </Card>
      </motion.section>
    )
  }

  if (view === 'splitter') {
    const total = booking.selections.length
    return <div className="mx-auto max-w-3xl space-y-4 overflow-x-hidden"><header className="flex items-center gap-3"><Button variant="outline" size="sm" onClick={() => setView('ticket')} aria-label="Back to ticket details"><ArrowLeft className="h-4 w-4" />Back</Button><h1 className="text-sm font-semibold uppercase tracking-[0.2em] text-slate-200">Splitter</h1><div className="ml-auto flex gap-2"><button type="button" aria-label="Refresh all splitters" onClick={() => setSplitters([{ id: 0, state: 'empty' }])} className="rounded-lg border border-white/10 p-2"><RefreshCw className="h-4 w-4" /></button><button type="button" aria-label="Copy all split booking codes" onClick={copyAllSplits} className="rounded-lg border border-white/10 p-2"><Copy className="h-4 w-4" /></button></div></header><div className="flex flex-col gap-3 sm:flex-row">{splitters.map((card) => <Card key={card.id} className="flex-1 space-y-3 p-4">{(card.state === 'empty' || card.state === 'selecting') && <div className="flex gap-2"><Button size="sm" onClick={() => runSplitter(card.id, 'top')}>Top</Button><Button size="sm" onClick={() => runSplitter(card.id, 'middle')}>Middle</Button><Button size="sm" onClick={() => runSplitter(card.id, 'bottom')}>Bottom</Button></div>}{card.state === 'selecting' && <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); void submitSplitter(card.id, (e.currentTarget.elements.namedItem('count') as HTMLInputElement).value) }}><Input name="count" type="number" min="1" max={total} defaultValue={card.count} autoFocus aria-label={`Number of games for splitter ${card.id}`} /><Button type="submit" size="sm">Confirm</Button></form>}{card.state === 'generating' && <p className="text-sm text-slate-300"><Loader2 className="mr-2 inline h-4 w-4 animate-spin" />Generating booking code...</p>}{card.state === 'error' && <><p className="text-sm text-red-300">{card.error}</p><button type="button" aria-label={`Refresh splitter ${card.id}`} onClick={() => resetSplitter(card.id)} className="rounded-lg border border-white/10 p-2"><RefreshCw className="h-4 w-4" /></button></>}{card.state === 'result' && <><p className="break-all font-mono text-xl text-emerald-300">{card.code}</p><p className="text-sm text-slate-300">{card.mode![0].toUpperCase() + card.mode!.slice(1)} {card.count} from <button type="button" className="text-accent underline" onClick={() => void copyValue(originalBookingCode ?? '')}>{originalBookingCode}</button></p><div className="flex gap-2"><button aria-label="Toggle split games" onClick={() => setSplitters((cards) => cards.map((c) => c.id === card.id ? { ...c, revealed: !c.revealed } : c))} className="rounded-lg border border-white/10 p-2">{card.revealed ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}</button><button aria-label="Edit splitter" onClick={() => setSplitters((cards) => cards.map((c) => c.id === card.id ? { ...c, state: 'selecting' } : c))} className="rounded-lg border border-white/10 p-2"><Edit3 className="h-4 w-4" /></button><button aria-label="Add splitter" onClick={addSplitter} className="rounded-lg border border-white/10 p-2"><Plus className="h-4 w-4" /></button><button aria-label="Refresh splitter" onClick={() => resetSplitter(card.id)} className="rounded-lg border border-white/10 p-2"><RefreshCw className="h-4 w-4" /></button><button aria-label="Copy split booking code" onClick={() => void copyValue(card.code!)} className="rounded-lg border border-white/10 p-2"><Copy className="h-4 w-4" /></button></div>{card.revealed && <div className="space-y-2">{card.games?.map((game) => <div key={game.event_id} className="rounded-lg border border-white/10 p-2 text-sm text-slate-200">{game.home} vs {game.away}</div>)}</div>}</>}</Card>)}</div></div>
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
          {booking.selections.length > 0 && <button type="button" onClick={() => setView('splitter')} className="mt-2 flex min-h-11 w-full items-center justify-between rounded-xl border border-white/10 bg-white/[0.03] px-3 text-left text-xs font-semibold uppercase tracking-[0.14em] text-slate-200" aria-label="Open Splitter"><span>Splitter</span><ChevronRight className="h-4 w-4 text-slate-500" /></button>}
          <p className="mt-3 text-[11px] text-slate-500">Times shown in Africa/Lagos (UTC+1).</p>
        </Card>
      </header>

      {error && <div className="rounded-2xl border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-200">{error}</div>}
      {notice && <div className="rounded-2xl border border-emerald-500/30 bg-emerald-500/10 p-3 text-sm text-emerald-200">{notice}</div>}
      {rebooking && <div className="flex items-center gap-2 rounded-2xl border border-accent/30 bg-accent/10 p-3 text-sm text-accent"><Loader2 className="h-4 w-4 animate-spin" />SportyBet is generating the updated ticket…</div>}

      {activeSelections.length > 0 && removeBar('top', true)}
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
