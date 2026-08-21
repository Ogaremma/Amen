import { useState } from 'react'
import { AlertCircle, BarChart3, Loader2, RefreshCw } from 'lucide-react'
import { analyzeForebet, type ForebetMatch, type ForebetPredictionResult } from '../lib/api'
import { Button } from './ui/button'
import { Card } from './ui/card'
import { Input } from './ui/input'

const DEFAULT_URL = 'https://www.forebet.com/en/football-predictions'

function predictionLabel(result: ForebetPredictionResult) {
  return result === 'HOME' ? 'Home' : result === 'AWAY' ? 'Away' : result === 'DRAW' ? 'Draw' : 'Unknown'
}

function MatchDetails({ match, drawCandidate = false }: { match: ForebetMatch; drawCandidate?: boolean }) {
  const score = match.predicted_score_home !== null && match.predicted_score_away !== null
    ? `${match.predicted_score_home} - ${match.predicted_score_away}` : null
  return (
    <article className={`rounded-2xl border p-4 ${drawCandidate ? 'border-amber-300/30 bg-amber-300/[0.06]' : 'border-white/10 bg-white/[0.03]'}`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="font-semibold text-white">{match.home_team} <span className="text-slate-500">vs</span> {match.away_team}</h3>
          <p className="mt-1 text-xs text-slate-400">{[match.competition, match.country].filter(Boolean).join(' · ') || 'Competition unavailable'}</p>
        </div>
        <span className={`rounded-full px-2.5 py-1 text-xs font-semibold ${match.predicted_result === 'DRAW' ? 'bg-amber-300/20 text-amber-200' : 'bg-blue-400/15 text-blue-200'}`}>
          {drawCandidate ? 'Forebet Draw Prediction' : predictionLabel(match.predicted_result)}
        </span>
      </div>
      <dl className="mt-4 grid grid-cols-2 gap-3 text-xs sm:grid-cols-4">
        {match.kickoff_display && <div><dt className="text-slate-500">Kickoff</dt><dd className="mt-1 text-slate-200">{match.kickoff_display}</dd></div>}
        {score && <div><dt className="text-slate-500">Predicted score</dt><dd className="mt-1 text-slate-200">{score}</dd></div>}
        {match.probabilities && <div><dt className="text-slate-500">Probabilities</dt><dd className="mt-1 text-slate-200">{match.probabilities.home ?? '-'}% / {match.probabilities.draw ?? '-'}% / {match.probabilities.away ?? '-'}%</dd></div>}
        {match.average_goals !== null && <div><dt className="text-slate-500">Average goals</dt><dd className="mt-1 text-slate-200">{match.average_goals}</dd></div>}
      </dl>
      {(match.odds_home !== null || match.odds_draw !== null || match.odds_away !== null) && <p className="mt-3 text-xs text-slate-400">1X2 odds: {match.odds_home ?? '-'} / {match.odds_draw ?? '-'} / {match.odds_away ?? '-'}</p>}
      {match.narrative && <p className="mt-3 border-t border-white/10 pt-3 text-xs leading-5 text-slate-400">{match.narrative}</p>}
    </article>
  )
}

export function AnalyzerPage() {
  const [url, setUrl] = useState(DEFAULT_URL)
  const [status, setStatus] = useState<'idle' | 'loading' | 'success' | 'error'>('idle')
  const [error, setError] = useState('')
  const [result, setResult] = useState<Awaited<ReturnType<typeof analyzeForebet>> | null>(null)

  async function submit() {
    const value = url.trim()
    if (!value) { setError('Enter a Forebet predictions page URL.'); setStatus('error'); return }
    setStatus('loading'); setError('')
    try { setResult(await analyzeForebet(value)); setStatus('success') }
    catch (cause) { setResult(null); setError(cause instanceof Error ? cause.message : 'Unable to analyze this Forebet page.'); setStatus('error') }
  }

  return <div className="mx-auto max-w-5xl space-y-5">
    <Card className="border-blue-400/20 bg-surface p-5 sm:p-7">
      <div className="flex items-start gap-3"><div className="rounded-xl bg-blue-400/15 p-2 text-blue-200"><BarChart3 className="h-5 w-5" /></div><div><p className="text-xs font-semibold uppercase tracking-[0.2em] text-blue-300">Forebet data analysis</p><h1 className="mt-1 text-2xl font-semibold text-white sm:text-3xl">Forebet Analyzer</h1><p className="mt-2 text-sm leading-6 text-slate-300">Analyze a Forebet predictions page and inspect its normalized match predictions.</p></div></div>
      <div className="mt-6 flex flex-col gap-3 sm:flex-row"><Input aria-label="Forebet predictions page URL" value={url} onChange={(event) => setUrl(event.target.value)} onKeyDown={(event) => event.key === 'Enter' && submit()} placeholder={DEFAULT_URL} disabled={status === 'loading'} /><Button onClick={submit} disabled={status === 'loading'}>{status === 'loading' && <Loader2 className="h-4 w-4 animate-spin" />}{status === 'loading' ? 'Analyzing...' : 'Analyze Forebet'}</Button></div>
      {status === 'error' && <div role="alert" className="mt-4 flex items-start gap-2 rounded-xl border border-red-300/20 bg-red-400/10 p-3 text-sm text-red-200"><AlertCircle className="mt-0.5 h-4 w-4 shrink-0" /><span>{error}</span><Button variant="outline" size="sm" className="ml-auto" onClick={submit}><RefreshCw className="h-4 w-4" /><span className="sr-only">Retry</span></Button></div>}
    </Card>
    {result && <>
      <section aria-label="Forebet summary" className="grid grid-cols-2 gap-3 sm:grid-cols-3"><Card className="p-4"><p className="text-xs uppercase tracking-wider text-slate-500">Matches analyzed</p><p className="mt-2 text-2xl font-semibold text-white">{result.total_matches}</p></Card><Card className="border-amber-300/30 bg-amber-300/[0.06] p-4"><p className="text-xs uppercase tracking-wider text-amber-200/70">Draw predictions</p><p className="mt-2 text-2xl font-semibold text-amber-100">{result.draw_count}</p></Card><Card className="col-span-2 p-4 sm:col-span-1"><p className="text-xs uppercase tracking-wider text-slate-500">Non-draw predictions</p><p className="mt-2 text-2xl font-semibold text-white">{result.total_matches - result.draw_count}</p></Card></section>
      <section><div className="mb-3 flex items-center justify-between"><h2 className="text-lg font-semibold text-white">Forebet Draw Candidates</h2><span className="text-xs text-slate-500">{result.draw_count} matches</span></div>{result.draw_matches.length ? <div className="space-y-3">{result.draw_matches.map((match) => <MatchDetails key={match.match_id ?? `${match.home_team}-${match.away_team}`} match={match} drawCandidate />)}</div> : <Card className="p-4 text-sm text-slate-400">No draw predictions were returned.</Card>}</section>
      <section><h2 className="mb-3 text-lg font-semibold text-white">All Forebet Matches</h2><div className="space-y-3">{result.matches.map((match) => <MatchDetails key={match.match_id ?? `${match.home_team}-${match.away_team}`} match={match} />)}</div></section>
    </>}
  </div>
}
