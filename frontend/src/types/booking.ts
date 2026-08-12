export interface BookingSelection {
  id: string
  event_id: string
  home: string
  away: string
  competition: string
  category: string
  kickoff: string
  kickoff_date: string
  kickoff_time: string
  market: string
  outcome: string
  odds: number | null
  specifier?: string | null
  status?: string | null
}

export interface BookingResponse {
  booking_code: string
  total_selections: number
  total_odds: number | null
  selections: BookingSelection[]
}
