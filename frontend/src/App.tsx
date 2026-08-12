import { DashboardPage } from './components/DashboardPage'
import { useTelegramWebApp } from './hooks/useTelegramWebApp'

function App() {
  const { isTelegram, user, verified, authStatus, authError } = useTelegramWebApp()

  // Prefer the backend-verified name; fall back to the (display-only) initData
  // user while verification is in flight.
  const displayName =
    verified?.user?.first_name ?? user?.first_name ?? user?.username ?? null

  return (
    <div className="min-h-screen bg-background text-white">
      <div className="mx-auto max-w-6xl px-4 py-4 sm:px-6 lg:px-8">
        <main className="space-y-6 py-6">
          {/* Telegram status — only shown when actually running inside Telegram.
              In a plain browser (dev) nothing appears and the app just works. */}
          {isTelegram && authStatus === 'authenticated' && displayName && (
            <div className="rounded-3xl border border-emerald-500/20 bg-emerald-500/5 px-4 py-3 text-sm text-emerald-200">
              Signed in via Telegram as <span className="font-semibold">{displayName}</span>.
            </div>
          )}

          {isTelegram && authStatus === 'pending' && (
            <div className="rounded-3xl border border-white/10 bg-surface/95 px-4 py-3 text-sm text-slate-300">
              Verifying your Telegram session…
            </div>
          )}

          {isTelegram && (authStatus === 'error' || authStatus === 'unconfigured') && (
            <div className="rounded-3xl border border-amber-500/20 bg-amber-500/5 px-4 py-3 text-sm text-amber-200">
              {authStatus === 'unconfigured'
                ? 'Telegram sign-in is not configured on the server yet — you can still use the app.'
                : `Telegram sign-in could not be verified (${authError}). You can still use the app.`}
            </div>
          )}

          <DashboardPage />
        </main>
      </div>
    </div>
  )
}

export default App
