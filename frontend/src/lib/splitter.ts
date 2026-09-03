import type { BookingSelection } from '../types/booking'

export type SplitMode = 'top' | 'middle' | 'bottom'

export function selectSplitGames(games: BookingSelection[], mode: SplitMode, count: number): BookingSelection[] {
  const sorted = [...games].sort((a, b) => Date.parse(a.kickoff) - Date.parse(b.kickoff))
  if (!Number.isInteger(count) || count < 1 || count > sorted.length) throw new Error(`Enter a number between 1 and ${sorted.length}.`)
  if (mode === 'top') return sorted.slice(0, count)
  if (mode === 'bottom') return sorted.slice(sorted.length - count)
  const picked = new Set<number>()
  let left: number
  let right: number
  if (sorted.length % 2 === 0) {
    left = sorted.length / 2 - 1
    right = sorted.length / 2
  } else {
    const center = Math.floor(sorted.length / 2)
    picked.add(center)
    left = center - 1
    right = center + 1
  }
  while (picked.size < count) {
    const remaining = count - picked.size
    const canLeft = left >= 0 && !picked.has(left)
    const canRight = right < sorted.length && !picked.has(right)
    if (remaining === 1) {
      if (canLeft && canRight) {
        const lo = sorted[left].odds ?? Infinity
        const ro = sorted[right].odds ?? Infinity
        picked.add(lo <= ro ? left : right)
      } else if (canLeft) picked.add(left)
      else if (canRight) picked.add(right)
      break
    }
    if (canLeft) { picked.add(left); left-- }
    if (picked.size < count && canRight) { picked.add(right); right++ }
    if (!canLeft && !canRight) break
  }
  return [...picked].sort((a, b) => a - b).map((i) => sorted[i])
}
