import { motion } from 'framer-motion'
import { ArrowRight } from 'lucide-react'
import { Card } from './ui/card'
import { Button } from './ui/button'

interface QuickActionCardProps {
  title: string
  description: string
  onClick: () => void
}

export function QuickActionCard({ title, description, onClick }: QuickActionCardProps) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4 }}
    >
      <Card className="h-full overflow-hidden p-5">
        <div className="flex flex-col gap-4">
          <div>
            <p className="text-sm uppercase tracking-[0.24em] text-slate-400">Quick action</p>
            <h3 className="mt-2 text-xl font-semibold text-white">{title}</h3>
          </div>
          <p className="text-sm leading-6 text-slate-300">{description}</p>
          <div className="mt-auto">
            <Button variant="outline" size="sm" onClick={onClick}>
              Start {title}
              <ArrowRight className="ml-2 h-4 w-4" />
            </Button>
          </div>
        </div>
      </Card>
    </motion.div>
  )
}
