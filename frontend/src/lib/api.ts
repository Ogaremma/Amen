import type { BookingResponse } from '../types/booking'

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000/api/v1'

function buildUrl(path: string) {
  return `${BASE_URL}${path}`
}

function telegramHeaders(): HeadersInit {
  const initData = typeof window !== 'undefined' ? window.Telegram?.WebApp?.initData : ''
  return initData ? { 'X-Telegram-Init-Data': initData } : {}
}

async function checkResponse(response: Response): Promise<BookingResponse> {
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

  return body as BookingResponse
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
