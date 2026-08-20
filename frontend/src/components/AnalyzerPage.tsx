import { BarChart3, Database, Upload } from 'lucide-react'
import { Button } from './ui/button'
import { Card } from './ui/card'

const EMPTY_STATISTICS = [
  ['Tickets analyzed', 0],
  ['Games analyzed', 0],
  ['Games won', 0],
  ['Games lost', 0],
] as const

export function AnalyzerPage() {
  return (
    <section className="mx-auto max-w-3xl space-y-4" aria-labelledby="analyzer-heading">
      <Card className="overflow-hidden border-violet-400/20 bg-surface p-5 sm:p-7">
        <div className="flex items-start gap-3">
          <div className="rounded-2xl border border-violet-300/20 bg-violet-400/10 p-2.5 text-violet-200">
            <BarChart3 className="h-5 w-5" />
          </div>
          <div className="min-w-0">
            <p className="text-xs font-semibold uppercase tracking-[0.22em] text-violet-300">Research workspace</p>
            <h1 id="analyzer-heading" className="mt-1 text-2xl font-semibold text-white sm:text-3xl">39 Billion Analyzer</h1>
            <p className="mt-2 text-sm leading-6 text-slate-300">Study the playing patterns of the 39 Billion account.</p>
          </div>
        </div>

        <Button className="mt-6 w-full sm:w-auto" disabled aria-describedby="import-note">
          <Upload className="mr-2 h-4 w-4" />
          Import Booking Code
        </Button>
        <p id="import-note" className="mt-2 text-xs text-slate-500">Importing will be enabled when the data-collection rules are defined.</p>
      </Card>

      <Card className="p-5 sm:p-6">
        <h2 className="text-sm font-semibold uppercase tracking-[0.18em] text-slate-200">Statistics</h2>
        <p className="mt-1 text-xs text-slate-500">Empty-state values - no analysis has been performed.</p>
        <dl className="mt-4 grid grid-cols-2 gap-3">
          {EMPTY_STATISTICS.map(([label, value]) => (
            <div key={label} className="rounded-2xl border border-white/10 bg-white/[0.03] p-4">
              <dt className="text-xs leading-5 text-slate-400">{label}</dt>
              <dd className="mt-1 text-2xl font-semibold text-white">{value}</dd>
            </div>
          ))}
        </dl>
      </Card>

      <Card className="flex items-start gap-3 p-5 sm:p-6">
        <Database className="mt-0.5 h-5 w-5 shrink-0 text-violet-300" />
        <div>
          <h2 className="text-sm font-semibold text-white">No training data has been imported yet.</h2>
          <p className="mt-2 text-sm leading-6 text-slate-400">
            Booking codes explicitly sourced from the 39 Billion account will eventually be imported and analyzed here. Optimizer bookings are not automatically used as analyzer data.
          </p>
        </div>
      </Card>
    </section>
  )
}
