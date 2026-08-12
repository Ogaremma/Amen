import { useEffect, useState } from 'react'
import { authenticateTelegram, type TelegramAuthResult } from '../lib/api'
import type { TelegramWebApp, TelegramWebAppUser } from '../types/telegram'

// Amen's brand background — we keep our dark-navy identity even inside Telegram,
// while still telling Telegram's chrome to match it (header/background color).
const AMEN_BG = '#081120'

type AuthStatus = 'idle' | 'pending' | 'authenticated' | 'error' | 'unconfigured'

export interface TelegramState {
  // True only when actually running inside the Telegram client (real initData).
  isTelegram: boolean
  // SDK finished ready()/expand() (or we're in a plain browser and there's
  // nothing to wait for). The UI should render regardless of this.
  isReady: boolean
  // Display-only user from initDataUnsafe. NEVER trusted for auth — the backend
  // verifies the signed initData instead.
  user: TelegramWebAppUser | null
  // Verified identity returned by the backend after validating initData.
  verified: TelegramAuthResult | null
  authStatus: AuthStatus
  authError: string | null
  themeMode: 'dark' | 'light'
}

function getWebApp(): TelegramWebApp | null {
  if (typeof window === 'undefined') return null
  return window.Telegram?.WebApp ?? null
}

export function useTelegramWebApp(): TelegramState {
  const [state, setState] = useState<TelegramState>({
    isTelegram: false,
    isReady: false,
    user: null,
    verified: null,
    authStatus: 'idle',
    authError: null,
    themeMode: 'dark',
  })

  useEffect(() => {
    const tg = getWebApp()

    // Not inside Telegram (e.g. local browser dev). The app must still work —
    // mark ready so no banner blocks the UI, and skip backend auth entirely.
    if (!tg || !tg.initData) {
      setState((prev) => ({ ...prev, isTelegram: false, isReady: true }))
      return
    }

    // Inside Telegram: initialise the viewport.
    try {
      tg.ready()
      tg.expand()
      // Keep Amen's visual identity: match Telegram chrome to our navy.
      tg.setHeaderColor?.(AMEN_BG)
      tg.setBackgroundColor?.(AMEN_BG)
    } catch {
      // Older clients may lack some methods; ignore and continue.
    }

    setState((prev) => ({
      ...prev,
      isTelegram: true,
      isReady: true,
      user: tg.initDataUnsafe?.user ?? null,
      themeMode: tg.colorScheme === 'light' ? 'light' : 'dark',
      authStatus: 'pending',
    }))

    // Send the RAW signed initData to our backend for verification.
    let cancelled = false
    authenticateTelegram(tg.initData)
      .then((result) => {
        if (cancelled) return
        setState((prev) => ({
          ...prev,
          verified: result,
          authStatus: 'authenticated',
          authError: null,
        }))
      })
      .catch((err: unknown) => {
        if (cancelled) return
        const message = err instanceof Error ? err.message : 'Telegram authentication failed'
        setState((prev) => ({
          ...prev,
          authStatus: message.toLowerCase().includes('not configured')
            ? 'unconfigured'
            : 'error',
          authError: message,
        }))
      })

    const handleTheme = () => {
      const current = getWebApp()
      setState((prev) => ({
        ...prev,
        themeMode: current?.colorScheme === 'light' ? 'light' : 'dark',
      }))
    }
    tg.onEvent('themeChanged', handleTheme)

    return () => {
      cancelled = true
      if (typeof tg.offEvent === 'function') {
        tg.offEvent('themeChanged', handleTheme)
      }
    }
  }, [])

  return state
}
