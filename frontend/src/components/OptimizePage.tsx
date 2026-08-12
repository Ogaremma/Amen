import { motion } from 'framer-motion'
import { Button } from './ui/button'
import { Card } from './ui/card'
import { MatchList } from './MatchList'
import { type AppState, useAppStore } from '../store/useAppStore'

export function OptimizePage() {
  const matches = useAppStore((state: AppState) => state.matches)
  const selectedMatchIds = useAppStore((state: AppState) => state.selectedMatchIds)
  const sortMatchesByTime = useAppStore((state: AppState) => state.sortMatchesByTime)
  const toggleMatchSelection = useAppStore((state: AppState) => state.toggleMatchSelection)
  const generateBookingCode = useAppStore((state: AppState) => state.generateBookingCode)
  const bookingCode = useAppStore((state: AppState) => state.bookingCode)

  return (
    <div className="space-y-8">
      <motion.section
        initial={{ opacity: 0, y: 18 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.45 }}
      >
        <Card>
          <div className="grid gap-6 lg:grid-cols-[1fr_0.7fr]">
            <div className="space-y-4">
              <p className="text-sm uppercase tracking-[0.3em] text-slate-500">Optimize</p>
              <h1 className="text-3xl font-semibold text-white">Fine-tune your match selection.</h1>
              <p className="max-w-2xl text-sm leading-6 text-slate-300">
                Select kickoffs, sort the list, and generate a new booking code for your football mini app flow.
              </p>
            </div>
            <div className="rounded-[28px] border border-white/10 bg-white/5 p-5 shadow-sm">
              <p className="text-sm uppercase tracking-[0.3em] text-slate-400">Mock booking code</p>
              <p className="mt-3 text-2xl font-semibold text-white">{bookingCode || 'AMN-BOOK-900'}</p>
              <Button className="mt-5 w-full" onClick={generateBookingCode}>
                Generate New Booking Code
              </Button>
            </div>
          </div>
        </Card>
      </motion.section>

      <motion.section
        initial={{ opacity: 0, y: 18 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.45, delay: 0.08 }}
      >
        <MatchList
          matches={matches}
          selectedMatchIds={selectedMatchIds}
          onToggle={toggleMatchSelection}
          onSort={sortMatchesByTime}
        />
      </motion.section>
    </div>
  )
}
