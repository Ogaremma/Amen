export interface BookingSelection {
  id: string
  event_id: string
  market_id: string
  outcome_id: string
  home: string
  away: string
  competition: string
  category: string
  kickoff: string
  kickoff_date: string
  kickoff_time: string
  local_kickoff_date: string
  local_kickoff_time: string
  market: string
  outcome: string
  odds: number | null
  specifier?: string | null
  status?: string | null
  game_status: 'upcoming' | 'live' | 'ended'
  result_status: 'pending' | 'won' | 'lost' | 'void' | 'unknown'
}

export interface BookingResponse {
  booking_code: string
  total_selections: number
  total_odds: number | null
  remaining_odds: number
  selections: BookingSelection[]
}
