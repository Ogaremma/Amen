import * as React from 'react'
import { cn } from '../../lib/utils'

interface CardProps extends React.HTMLAttributes<HTMLDivElement> {}

const Card = React.forwardRef<HTMLDivElement, CardProps>(({ className, ...props }, ref) => (
  <div
    ref={ref}
    className={cn(
      'rounded-[28px] border border-white/10 bg-surface/95 p-6 shadow-glow backdrop-blur-xl',
      className,
    )}
    {...props}
  />
))
Card.displayName = 'Card'

export { Card }
