import type { BookingResponse } from '../types/booking'

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000/api/v1'

function buildUrl(path: string) {
  return `${BASE_URL}${path}`
}

function telegramHeaders(): HeadersInit {
  const initData = typeof window !== 'undefined' ? window.Telegram?.WebApp?.initData : ''
  return initData ? { 'X-Telegram-Init-Data': initData } : {}
}

async function checkResponse<T>(response: Response): Promise<T> {
  const text = await response.text()
  let body: unknown = null
  try {
    body = text ? JSON.parse(text) : null
  } catch {
    body = null
  }

  if (!response.ok) {
    const detail =
      body && typeof body === 'object' && 'detail' in body
        ? String((body as { detail: unknown }).detail)
        : text || 'Unexpected server response'
    throw new Error(detail)
  }

  return body as T
}

export async function fetchBookingByCode(bookingCode: string): Promise<BookingResponse> {
  const response = await fetch(buildUrl(`/bookings/${encodeURIComponent(bookingCode)}`), { headers: telegramHeaders() })
  return checkResponse(response)
}

// Ask the backend to remove one OR MORE selections (by SportyBet eventId) and
// rebook in a SINGLE operation. The backend is the source of truth: it re-fetches
// the authoritative ticket, drops the events, has SportyBet regenerate the code +
// odds ONCE, and returns the brand-new booking. The frontend never computes odds
// or builds SportyBet payloads itself, and never sends one request per game.
export async function removeSelectedGames(
  bookingCode: string,
  eventIds: string[],
): Promise<BookingResponse> {
  const response = await fetch(
    buildUrl(`/bookings/${encodeURIComponent(bookingCode)}/remove-selected`),
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ event_ids: eventIds }),
    },
  )
  return checkResponse(response)
}

export interface TelegramAuthResult {
  ok: boolean
  user: {
    telegram_user_id: number
    username: string | null
    first_name: string | null
    last_name: string | null
    language_code: string | null
    is_premium: boolean
  }
  current_booking_code: string | null
}

// Send the RAW signed initData to the backend, which validates it with the
// bot token (backend-only) and returns the verified identity. The frontend
// NEVER validates initData itself and NEVER sees the bot token.
export async function authenticateTelegram(initData: string): Promise<TelegramAuthResult> {
  const response = await fetch(buildUrl('/telegram/auth'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ init_data: initData }),
  })
  const text = await response.text()
  let body: unknown = null
  try {
    body = text ? JSON.parse(text) : null
  } catch {
    body = null
  }
  if (!response.ok) {
    const detail =
      body && typeof body === 'object' && 'detail' in body
        ? String((body as { detail: unknown }).detail)
        : text || 'Telegram authentication failed'
    throw new Error(detail)
  }
  return body as TelegramAuthResult
}

export interface HistoryItem { id: number; booking_code: string; loaded_at: string; selection_count: number | null; remaining_odds: number | null }

export async function fetchHistory(): Promise<HistoryItem[]> {
  const response = await fetch(buildUrl('/history'), { headers: telegramHeaders() })
  const body = await response.json()
  if (!response.ok) throw new Error(body?.detail || 'Unable to load history')
  return body as HistoryItem[]
}
export async function deleteHistoryItem(id: number): Promise<void> {
  const response = await fetch(buildUrl(`/history/${id}`), { method: 'DELETE', headers: telegramHeaders() })
  if (!response.ok) throw new Error('Unable to delete history item')
}

export type ForebetPredictionResult = 'HOME' | 'DRAW' | 'AWAY' | 'UNKNOWN'

export interface ForebetProbability {
  home: number | null
  draw: number | null
  away: number | null
}

export interface ForebetMatch {
  match_id: string | null
  home_team: string
  away_team: string
  competition: string | null
  country: string | null
  competition_code: string | null
  kickoff: string | null
  kickoff_display: string | null
  match_url: string | null
  predicted_result: ForebetPredictionResult
  predicted_score_home: number | null
  predicted_score_away: number | null
  probabilities: ForebetProbability | null
  average_goals: number | null
  primary_coefficient: number | null
  odds_home: number | null
  odds_draw: number | null
  odds_away: number | null
  narrative: string | null
  source: string
  source_url: string | null
}

export interface ForebetAnalyzeResponse {
  source_url: string
  total_matches: number
  draw_count: number
  draw_matches: ForebetMatch[]
  matches: ForebetMatch[]
}

export async function analyzeForebet(url: string): Promise<ForebetAnalyzeResponse> {
  const response = await fetch(buildUrl('/forebet/analyze'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...telegramHeaders() },
    body: JSON.stringify({ url }),
  })
  return checkResponse<ForebetAnalyzeResponse>(response)
}

export interface ForebetDrawWindowMatch {
  event_id: string
  home_team: string
  away_team: string
  kickoff: string
  match_status: string | null
  market_id: string
  outcome_id: string
  product_id: number
  sport_id: string
  specifier: string | null
}

export interface ForebetDrawWindowDay {
  prediction_date: string
  booking_code: string | null
  selection_count: number
  status: 'active' | 'unavailable' | 'complete' | 'error'
  matches: ForebetDrawWindowMatch[]
  source_urls: string[]
  diagnostics: string[]
  created_at: string
  last_updated: string
}
export interface ForebetDrawCompilation {
  compilation_id: string
  identity: string
  booking_code: string | null
  selection_count: number
  prediction_dates: string[]
  matches: ForebetDrawWindowMatch[]
  status: 'active' | 'empty' | 'error'
  created_at: string
  updated_at: string
}

export interface ForebetDrawWindowResponse {
  days: ForebetDrawWindowDay[]
  active_count: number
  prebooking_days?: ForebetPrebookingDay[]
  compilation?: ForebetDrawCompilation | null
}

export interface ForebetPrebookingCandidate {
  prediction_date: string
  home_team: string
  away_team: string
  draw_probability: number | null
  status: string
  sportybet_event_id: string | null
  sportybet_kickoff: string | null
  booking_eligible: boolean
  reason: string | null
}

export interface ForebetPrebookingDay {
  prediction_date: string
  candidates: ForebetPrebookingCandidate[]
  diagnostics: Record<string, unknown>
  updated_at: string
}

export async function getForebetDrawWindow(): Promise<ForebetDrawWindowResponse> {
  const response = await fetch(buildUrl('/forebet/draw-window'), { headers: telegramHeaders() })
  return checkResponse<ForebetDrawWindowResponse>(response)
}
