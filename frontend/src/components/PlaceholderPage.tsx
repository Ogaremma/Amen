import { motion } from 'framer-motion'
import { Card } from './ui/card'

interface PlaceholderPageProps {
  title: string
  description: string
}

export function PlaceholderPage({ title, description }: PlaceholderPageProps) {
  return (
    <motion.div initial={{ opacity: 0, y: 18 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.4 }}>
      <Card>
        <div className="space-y-6">
          <div>
            <p className="text-sm uppercase tracking-[0.3em] text-slate-500">{title}</p>
            <h1 className="mt-3 text-3xl font-semibold text-white">Under construction</h1>
          </div>
          <p className="max-w-3xl text-sm leading-6 text-slate-300">{description}</p>
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="rounded-3xl bg-white/5 p-5 text-slate-300">
              <p className="text-xs uppercase tracking-[0.28em] text-slate-500">Status</p>
              <p className="mt-2 text-base font-semibold text-white">Coming soon</p>
            </div>
            <div className="rounded-3xl bg-white/5 p-5 text-slate-300">
              <p className="text-xs uppercase tracking-[0.28em] text-slate-500">Purpose</p>
              <p className="mt-2 text-base font-semibold text-white">{title}</p>
            </div>
          </div>
        </div>
      </Card>
    </motion.div>
  )
}
