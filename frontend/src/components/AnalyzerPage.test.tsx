import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AnalyzerPage } from './AnalyzerPage'
import * as api from '../lib/api'
import * as clipboard from '../lib/clipboard'

vi.mock('../lib/api', async () => ({ ...(await vi.importActual<typeof import('../lib/api')>('../lib/api')), getForebetDrawWindow: vi.fn() }))
vi.mock('../lib/clipboard', () => ({ copyTextToClipboard: vi.fn() }))

const day = (date: string, code: string): api.ForebetDrawWindowDay => ({ prediction_date: date, booking_code: code, selection_count: 2, status: 'active', matches: [], source_urls: [], diagnostics: [], created_at: '2026-08-21T10:00:00Z', last_updated: '2026-08-21T11:00:00Z' })

describe('AnalyzerPage', () => {
  afterEach(() => { cleanup(); vi.clearAllMocks(); vi.useRealTimers() })

  it('renders loading then three API prediction days and booking codes', async () => {
    vi.mocked(api.getForebetDrawWindow).mockResolvedValue({ active_count: 3, days: [day('2026-08-22', 'CODE-A'), day('2026-08-23', 'CODE-B'), day('2026-08-24', 'CODE-C')] })
    render(<AnalyzerPage />)
    expect(screen.getByText(/Loading active draw bookings/i)).toBeInTheDocument()
    expect(await screen.findByText('CODE-A')).toBeInTheDocument()
    expect(screen.getByText('CODE-B')).toBeInTheDocument(); expect(screen.getByText('CODE-C')).toBeInTheDocument()
    expect(screen.getByText(/22.*August.*2026|August.*22.*2026/i)).toBeInTheDocument()
    expect(screen.queryByText(/39 Billion/i)).not.toBeInTheDocument()
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
  })

  it('copies the correct code and shows copied state', async () => {
    vi.mocked(api.getForebetDrawWindow).mockResolvedValue({ active_count: 1, days: [day('2026-08-22', 'COPY-ME')] })
    vi.mocked(clipboard.copyTextToClipboard).mockResolvedValue(true)
    const user = userEvent.setup(); render(<AnalyzerPage />)
    await user.click(await screen.findByRole('button', { name: /Copy booking code/i }))
    expect(clipboard.copyTextToClipboard).toHaveBeenCalledWith('COPY-ME')
    expect(screen.getByRole('button', { name: /Copy booking code/i })).toHaveTextContent('Copied')
  })

  it('renders empty state when fewer than three means zero', async () => {
    vi.mocked(api.getForebetDrawWindow).mockResolvedValue({ active_count: 0, days: [] })
    render(<AnalyzerPage />)
    expect(await screen.findByText(/No active Forebet draw prediction days/i)).toBeInTheDocument()
  })

  it('shows an error and retries', async () => {
    vi.mocked(api.getForebetDrawWindow).mockRejectedValueOnce(new Error('Window unavailable')).mockResolvedValueOnce({ active_count: 1, days: [day('2026-08-22', 'RETRY')] })
    const user = userEvent.setup(); render(<AnalyzerPage />)
    expect(await screen.findByRole('alert')).toHaveTextContent('Window unavailable')
    await user.click(screen.getByRole('button', { name: /Retry/i }))
    expect(await screen.findByText('RETRY')).toBeInTheDocument()
  })

  it('sets up polling and clears it on unmount', async () => {
    const setSpy = vi.spyOn(window, 'setInterval'); const clearSpy = vi.spyOn(window, 'clearInterval')
    vi.mocked(api.getForebetDrawWindow).mockResolvedValue({ active_count: 1, days: [day('2026-08-22', 'POLL')] })
    const view = render(<AnalyzerPage />); await screen.findByText('POLL')
    expect(setSpy).toHaveBeenCalled(); view.unmount(); expect(clearSpy).toHaveBeenCalled()
  })

  it('renders and copies the API compilation booking', async () => {
    vi.mocked(api.getForebetDrawWindow).mockResolvedValue({ active_count: 3, days: [day('2026-08-22', 'A'), day('2026-08-23', 'B'), day('2026-08-24', 'C')], compilation: { compilation_id: 'comp-1', identity: 'hash', booking_code: 'PAPER-COMP', selection_count: 6, prediction_dates: ['2026-08-22', '2026-08-23', '2026-08-24'], matches: [], status: 'active', diagnostics: [], created_at: '2026-08-21T10:00:00Z', updated_at: '2026-08-21T11:00:00Z' } })
    vi.mocked(clipboard.copyTextToClipboard).mockResolvedValue(true)
    const user = userEvent.setup(); render(<AnalyzerPage />)
    await user.click(await screen.findByRole('button', { name: 'Compilation' }))
    expect(screen.getByText('PAPER-COMP')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /Copy compilation booking code/i }))
    expect(clipboard.copyTextToClipboard).toHaveBeenCalledWith('PAPER-COMP')
  })
})
