import { useCallback, useEffect, useRef, useState } from 'react'
import { AlertCircle, BarChart3, Check, Copy, Loader2, RefreshCw } from 'lucide-react'
import { getForebetDrawWindow, type ForebetDrawWindowDay } from '../lib/api'
import { copyTextToClipboard } from '../lib/clipboard'
import { Button } from './ui/button'
import { Card } from './ui/card'

const POLL_INTERVAL_MS = 60_000

function formatDate(value: string) {
  const parsed = new Date(`${value}T00:00:00`)
  return Number.isNaN(parsed.getTime()) ? value : new Intl.DateTimeFormat(undefined, { dateStyle: 'long' }).format(parsed)
}

function formatTimestamp(value: string) {
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? value : new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(parsed)
}

function BookingDayCard({ day }: { day: ForebetDrawWindowDay }) {
  const [copied, setCopied] = useState(false)
  async function copyCode() {
    if (await copyTextToClipboard(day.booking_code)) {
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1800)
    }
  }
  return <Card className="border-blue-400/20 bg-surface p-5">
    <div className="flex items-start justify-between gap-3">
      <div><p className="text-xs font-semibold uppercase tracking-[0.16em] text-blue-300">Forebet Draw Predictions</p><h2 className="mt-1 text-xl font-semibold text-white">Draw Predictions for {formatDate(day.prediction_date)}</h2></div>
      <span className={`rounded-full px-2.5 py-1 text-xs font-semibold ${day.status === 'active' ? 'bg-emerald-400/15 text-emerald-200' : day.status === 'complete' ? 'bg-slate-400/15 text-slate-300' : 'bg-red-400/15 text-red-200'}`}>{day.status}</span>
    </div>
    <div className="mt-5 flex flex-wrap items-center gap-3"><div className="min-w-0 flex-1 rounded-xl border border-white/10 bg-black/10 px-4 py-3"><p className="text-xs text-slate-500">SportyBet booking code</p><p className="mt-1 break-all font-mono text-2xl font-bold tracking-wider text-white">{day.booking_code}</p></div><Button variant="outline" onClick={copyCode} aria-label={`Copy booking code for ${day.prediction_date}`}>{copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}{copied ? 'Copied' : 'Copy'}</Button></div>
    <div className="mt-4 flex flex-wrap gap-x-5 gap-y-2 text-sm text-slate-300"><span>{day.selection_count} draw {day.selection_count === 1 ? 'selection' : 'selections'}</span><span>Updated {formatTimestamp(day.last_updated)}</span></div>
  </Card>
}

export function AnalyzerPage() {
  const [days, setDays] = useState<ForebetDrawWindowDay[]>([])
  const [state, setState] = useState<'loading' | 'success' | 'error'>('loading')
  const [error, setError] = useState('')
  const [retryToken, setRetryToken] = useState(0)
  const inFlight = useRef(false)

  const load = useCallback(async (initial = false) => {
    if (inFlight.current) return
    inFlight.current = true
    if (initial) setState('loading')
    try {
      const response = await getForebetDrawWindow()
      setDays(response.days)
      setState('success')
      setError('')
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Unable to load Forebet draw bookings.')
      setState('error')
    } finally {
      inFlight.current = false
    }
  }, [state])

  useEffect(() => {
    let active = true
    const run = async () => { if (active) await load(true) }
    void run()
    const timer = window.setInterval(() => { if (active) void load() }, POLL_INTERVAL_MS)
    return () => { active = false; window.clearInterval(timer) }
  }, [retryToken]) // eslint-disable-line react-hooks/exhaustive-deps

  return <div className="mx-auto max-w-5xl space-y-5">
    <Card className="border-blue-400/20 bg-surface p-5 sm:p-7"><div className="flex items-start gap-3"><div className="rounded-xl bg-blue-400/15 p-2 text-blue-200"><BarChart3 className="h-5 w-5" /></div><div><p className="text-xs font-semibold uppercase tracking-[0.2em] text-blue-300">Automatic daily bookings</p><h1 className="mt-1 text-2xl font-semibold text-white sm:text-3xl">Forebet Draw Analyzer</h1><p className="mt-2 text-sm leading-6 text-slate-300">Three rolling days of Forebet DRAW predictions, matched and booked on SportyBet.</p></div></div></Card>
    {state === 'loading' && <Card className="flex items-center gap-3 p-6 text-sm text-slate-300"><Loader2 className="h-5 w-5 animate-spin text-blue-300" />Loading active draw bookings...</Card>}
    {state === 'error' && <Card role="alert" className="flex items-center gap-3 border-red-300/20 bg-red-400/10 p-4 text-sm text-red-200"><AlertCircle className="h-4 w-4" /><span>{error}</span><Button variant="outline" size="sm" className="ml-auto" onClick={() => setRetryToken((value) => value + 1)}><RefreshCw className="h-4 w-4" />Retry</Button></Card>}
    {state === 'success' && (days.length ? <section aria-label="Forebet draw booking window" className="space-y-4">{days.map((day) => <BookingDayCard key={day.prediction_date} day={day} />)}</section> : <Card className="p-6 text-center text-sm text-slate-400">No active Forebet draw prediction days are available yet.</Card>)}
  </div>
}
