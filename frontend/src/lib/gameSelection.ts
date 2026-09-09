import type { BookingSelection } from '../types/booking'

export function sortChronologically(selections: BookingSelection[]): BookingSelection[] {
  return [...selections].sort((a, b) => Date.parse(a.kickoff) - Date.parse(b.kickoff))
}
