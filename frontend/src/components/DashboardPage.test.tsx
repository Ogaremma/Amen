import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { DashboardPage } from './DashboardPage'
import * as api from '../lib/api'
import type { BookingResponse } from '../types/booking'

vi.mock('../lib/api')

const original: BookingResponse = {
  booking_code: 'HW7UDH',
  total_selections: 3,
  total_odds: 3.75,
  remaining_odds: 1.5,
  selections: [
    {
      id: 'A', event_id: 'A', market_id: '1', outcome_id: '1', home: 'Team A', away: 'Team B', competition: 'Premier League', category: 'England',
      kickoff: '2026-08-13T11:30:00Z', kickoff_date: '2026-08-13', kickoff_time: '11:30',
      local_kickoff_date: '2026-08-13', local_kickoff_time: '12:30', market: '1X2', outcome: 'Home', odds: 1.5,
      status: 'Not start', game_status: 'upcoming', result_status: 'pending',
    },
    {
      id: 'B', event_id: 'B', market_id: '166', outcome_id: '12', home: 'Team C', away: 'Team D', competition: 'La Liga', category: 'Spain',
      kickoff: '2026-08-13T14:00:00Z', kickoff_date: '2026-08-13', kickoff_time: '14:00',
      local_kickoff_date: '2026-08-13', local_kickoff_time: '15:00', market: 'Totals', outcome: 'Over 2.5', odds: 2.5,
      status: 'Live', game_status: 'live', result_status: 'pending',
    },
    {
      id: 'C', event_id: 'C', market_id: '10', outcome_id: '2', home: 'Team E', away: 'Team F', competition: 'Serie A', category: 'Italy',
      kickoff: '2026-08-13T10:00:00Z', kickoff_date: '2026-08-13', kickoff_time: '10:00',
      local_kickoff_date: '2026-08-13', local_kickoff_time: '11:00', market: 'Both Teams To Score', outcome: 'Yes', odds: 1.8,
      status: 'Finished', game_status: 'ended', result_status: 'won',
    },
  ],
}

const updated: BookingResponse = { ...original, booking_code: 'QRZG53', total_selections: 1, remaining_odds: 1.5, selections: [original.selections[0]] }

async function loadTicket(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText('SportyBet booking code'), 'hw7udh')
  await user.click(screen.getByRole('button', { name: 'Load Ticket' }))
  await screen.findByRole('button', { name: 'Restore original ticket' })
}

const gameCard = (home: string, away: string) => screen.getByRole('button', {
  name: `Toggle selection for ${home} vs ${away}`,
})

const expectSelected = (home: string, away: string, selected: boolean) => {
  expect(gameCard(home, away)).toHaveAttribute('aria-pressed', String(selected))
}

describe('DashboardPage Phase 3 ticket flow', () => {
  let clipboardWrite: ReturnType<typeof vi.fn>

  afterEach(cleanup)
  beforeEach(() => {
    vi.resetAllMocks()
    vi.mocked(api.fetchBookingByCode).mockResolvedValue(original)
    vi.mocked(api.fetchHistory).mockResolvedValue([])
    clipboardWrite = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      writable: true,
      value: { writeText: clipboardWrite },
    })
  })

  it('starts on the booking input page and navigates to compact ticket details', async () => {
    const user = userEvent.setup()
    render(<DashboardPage />)
    expect(screen.getByText('Load your ticket')).toBeInTheDocument()
    await loadTicket(user)
    expect(screen.queryByLabelText('SportyBet booking code')).not.toBeInTheDocument()
    expect(screen.getByText('3')).toBeInTheDocument()
    expect(screen.getAllByText('1.50x')).not.toHaveLength(0)
    expect(screen.getByText('Upcoming')).toBeInTheDocument()
    expect(screen.getByText('Live')).toBeInTheDocument()
    expect(screen.queryByText('Ended')).not.toBeInTheDocument()
    expect(screen.queryByText('Team E')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'View 1 ended games' })).toBeInTheDocument()
    expect(screen.getByText('12:30 PM')).toBeInTheDocument()
    expect(screen.getAllByRole('img', { name: 'Bet result: Pending' })).toHaveLength(2)
  })

  it('renders every date section heading as bold yellow and keeps game count separate', async () => {
    const user = userEvent.setup()
    vi.mocked(api.fetchBookingByCode).mockResolvedValue({
      ...original,
      selections: original.selections.map((selection, index) => ({
        ...selection,
        game_status: 'upcoming',
        result_status: 'pending',
        local_kickoff_date: index === 2 ? '2026-08-25' : '2026-08-24',
        kickoff_date: index === 2 ? '2026-08-25' : '2026-08-24',
      })),
    })
    render(<DashboardPage />)
    await loadTicket(user)

    const headings = screen.getAllByRole('heading', { level: 2 })
    expect(headings.map((heading) => heading.textContent)).toEqual(['AUGUST 25, 2026', 'AUGUST 24, 2026'])
    for (const heading of headings) {
      expect(heading).toHaveClass('date-section-heading', 'font-bold', 'text-yellow-300')
      expect(heading).not.toHaveTextContent(/games?$/i)
    }
    expect(screen.getByText('2 games')).not.toHaveClass('text-yellow-300')
    expect(screen.getByText('1 game')).not.toHaveClass('text-yellow-300')
  })

  it('renders backend-provided won result on the ended card without replacing game status', async () => {
    const user = userEvent.setup()
    render(<DashboardPage />)
    await loadTicket(user)
    await user.click(screen.getByRole('button', { name: 'View 1 ended games' }))
    expect(screen.getByRole('img', { name: 'Bet result: Won' })).toHaveClass('text-emerald-400')
    expect(screen.getByText('Ended')).toHaveClass('text-red-300')
  })

  it.each([
    ['lost', 'Lost', 'text-red-400'],
    ['void', 'Void', 'text-slate-300'],
    ['unknown', 'Unknown', 'text-slate-500'],
  ] as const)('renders %s settlement from the backend', async (resultStatus, label, className) => {
    const user = userEvent.setup()
    vi.mocked(api.fetchBookingByCode).mockResolvedValue({
      ...original,
      selections: original.selections.map((selection) => selection.event_id === 'C'
        ? { ...selection, result_status: resultStatus }
        : selection),
    })
    render(<DashboardPage />)
    await loadTicket(user)
    await user.click(screen.getByRole('button', { name: 'View 1 ended games' }))
    expect(screen.getByRole('img', { name: `Bet result: ${label}` })).toHaveClass(className)
    expect(screen.getByText('Ended')).toBeInTheDocument()
  })

  it('opens ended games without reload and preserves selection across both views', async () => {
    const user = userEvent.setup()
    render(<DashboardPage />)
    await loadTicket(user)
    await user.click(gameCard('Team A', 'Team B'))
    await user.click(screen.getByRole('button', { name: 'View 1 ended games' }))
    expect(screen.getByRole('heading', { name: 'Ended Games' })).toBeInTheDocument()
    expect(screen.getByText(/Team E.*Team F/)).toBeInTheDocument()
    expect(screen.queryByText(/Team A.*Team B/)).not.toBeInTheDocument()
    expect(screen.getAllByText('1 selected')).toHaveLength(2)
    await user.click(gameCard('Team E', 'Team F'))
    expect(screen.getAllByText('2 selected')).toHaveLength(2)
    await user.click(screen.getByRole('button', { name: 'Back to ticket details' }))
    expectSelected('Team A', 'Team B', true)
    expect(screen.getAllByText('2 selected')).toHaveLength(2)
  })

  it('selects and deselects every live/upcoming game without changing ended selections', async () => {
    const user = userEvent.setup()
    render(<DashboardPage />)
    await loadTicket(user)

    await user.click(screen.getByRole('button', { name: 'View 1 ended games' }))
    await user.click(gameCard('Team E', 'Team F'))
    await user.click(screen.getByRole('button', { name: 'Back to ticket details' }))

    const selectAll = screen.getByRole('button', { name: 'Select all live and upcoming games' })
    await user.click(selectAll)
    expectSelected('Team A', 'Team B', true)
    expectSelected('Team C', 'Team D', true)
    expect(screen.getByRole('button', { name: 'Deselect all live and upcoming games' })).toBeInTheDocument()
    expect(screen.getAllByText('3 selected')).toHaveLength(2)

    await user.click(screen.getByRole('button', { name: 'Deselect all live and upcoming games' }))
    expectSelected('Team A', 'Team B', false)
    expectSelected('Team C', 'Team D', false)
    expect(screen.getAllByText('1 selected')).toHaveLength(2)

    await user.click(screen.getByRole('button', { name: 'View 1 ended games' }))
    expectSelected('Team E', 'Team F', true)
  })

  it('toggles a game by clicking the card body and supports Enter and Space', async () => {
    const user = userEvent.setup()
    render(<DashboardPage />)
    await loadTicket(user)

    const card = screen.getByRole('button', { name: 'Toggle selection for Team A vs Team B' })
    await user.click(screen.getByText(/Team A.*Team B/))
    expectSelected('Team A', 'Team B', true)

    card.focus()
    await user.keyboard('{Enter}')
    expectSelected('Team A', 'Team B', false)
    await user.keyboard(' ')
    expectSelected('Team A', 'Team B', true)
  })

  it('toggles once when the existing corner selection icon is clicked', async () => {
    const user = userEvent.setup()
    render(<DashboardPage />)
    await loadTicket(user)

    await user.click(screen.getByTestId('selection-control-A'))
    expectSelected('Team A', 'Team B', true)
    expect(screen.getAllByText('1 selected')).toHaveLength(2)
  })

  it('selects and deselects only the games in one date group', async () => {
    const user = userEvent.setup()
    vi.mocked(api.fetchBookingByCode).mockResolvedValue({
      ...original,
      selections: original.selections.map((selection) => selection.event_id === 'B'
        ? { ...selection, local_kickoff_date: '2026-08-14' }
        : selection),
    })
    render(<DashboardPage />)
    await loadTicket(user)

    await user.click(screen.getByRole('button', { name: 'Select all games on AUGUST 13, 2026' }))
    expectSelected('Team A', 'Team B', true)
    expectSelected('Team C', 'Team D', false)

    await user.click(screen.getByRole('button', { name: 'Deselect all games on AUGUST 13, 2026' }))
    expectSelected('Team A', 'Team B', false)
    expectSelected('Team C', 'Team D', false)
  })

  it('completes a partially selected date without affecting another date', async () => {
    const user = userEvent.setup()
    vi.mocked(api.fetchBookingByCode).mockResolvedValue({
      ...original,
      selections: [
        original.selections[0],
        { ...original.selections[1], local_kickoff_date: '2026-08-13' },
        { ...original.selections[2], event_id: 'D', id: 'D', home: 'Team G', away: 'Team H', game_status: 'upcoming', result_status: 'pending', local_kickoff_date: '2026-08-14' },
      ],
    })
    render(<DashboardPage />)
    await loadTicket(user)

    await user.click(gameCard('Team A', 'Team B'))
    await user.click(screen.getByRole('button', { name: 'Select all games on AUGUST 13, 2026' }))
    expectSelected('Team A', 'Team B', true)
    expectSelected('Team C', 'Team D', true)
    expectSelected('Team G', 'Team H', false)
  })

  it('select all completes a partial selection and removal remains one batch request', async () => {
    const user = userEvent.setup()
    vi.mocked(api.removeSelectedGames).mockResolvedValue(updated)
    render(<DashboardPage />)
    await loadTicket(user)

    await user.click(gameCard('Team A', 'Team B'))
    await user.click(screen.getByRole('button', { name: 'Select all live and upcoming games' }))
    expectSelected('Team A', 'Team B', true)
    expectSelected('Team C', 'Team D', true)

    await user.click(screen.getAllByRole('button', { name: /Remove Selected \((first|last)\)/ })[0])
    await user.click(screen.getByRole('button', { name: 'Remove Selected' }))
    await screen.findByText('QRZG53')
    expect(api.removeSelectedGames).toHaveBeenCalledTimes(1)
    expect(api.removeSelectedGames).toHaveBeenCalledWith('HW7UDH', expect.arrayContaining(['A', 'B']))
  })

  it('uses compact transparent controls and red removal styling while preserving status colors', async () => {
    const user = userEvent.setup()
    render(<DashboardPage />)
    await loadTicket(user)

    const upcomingCard = gameCard('Team A', 'Team B')
    const control = screen.getByTestId('selection-control-A')
    expect(control).toHaveClass('h-4', 'w-4', 'bg-transparent', 'border-slate-500')
    expect(screen.getByText('Live')).toHaveClass('text-emerald-300')
    expect(screen.getByText('Upcoming')).toHaveClass('text-sky-200')

    await user.click(upcomingCard)
    expect(upcomingCard).toHaveAttribute('data-selected', 'true')
    expect(upcomingCard).toHaveClass('border-red-400/60', 'bg-red-500/10')
    expect(control).toHaveClass('border-red-500', 'bg-red-500', 'text-white')

    await user.click(screen.getByRole('button', { name: 'View 1 ended games' }))
    expect(screen.getByText('Ended')).toHaveClass('text-red-300')
  })

  it('supports back navigation and copy', async () => {
    const user = userEvent.setup()
    render(<DashboardPage />)
    await loadTicket(user)
    const writeText = vi.spyOn(navigator.clipboard, 'writeText')
    await user.click(screen.getByRole('button', { name: 'Copy' }))
    expect(writeText).toHaveBeenCalledWith('HW7UDH')
    expect(screen.getByText('Copied')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Back to booking input' }))
    expect(screen.getByText('Load your ticket')).toBeInTheDocument()
  })

  it('enables both remove actions after multiple selection and sends one batch request', async () => {
    const user = userEvent.setup()
    vi.mocked(api.removeSelectedGames).mockResolvedValue(updated)
    render(<DashboardPage />)
    await loadTicket(user)
    const removeButtons = screen.getAllByRole('button', { name: /Remove Selected \((first|last)\)/ })
    expect(removeButtons.every((button) => button.hasAttribute('disabled'))).toBe(true)
    await user.click(gameCard('Team A', 'Team B'))
    await user.click(gameCard('Team C', 'Team D'))
    expect(screen.getAllByText('2 selected')).toHaveLength(2)
    await user.click(removeButtons[0])
    await user.click(screen.getByRole('button', { name: 'Remove Selected' }))
    await screen.findByText('QRZG53')
    expect(api.removeSelectedGames).toHaveBeenCalledTimes(1)
    expect(api.removeSelectedGames).toHaveBeenCalledWith('HW7UDH', expect.arrayContaining(['A', 'B']))
    expectSelected('Team A', 'Team B', false)
  })

  it('disables controls while rebooking and keeps current ticket on failure', async () => {
    const user = userEvent.setup()
    let rejectRequest: (error: Error) => void = () => undefined
    vi.mocked(api.removeSelectedGames).mockImplementation(() => new Promise((_, reject) => { rejectRequest = reject }))
    render(<DashboardPage />)
    await loadTicket(user)
    await user.click(gameCard('Team A', 'Team B'))
    await user.click(screen.getAllByRole('button', { name: /Remove Selected \((first|last)\)/ })[0])
    await user.click(screen.getByRole('button', { name: 'Remove Selected' }))
    expect(screen.getByText('SportyBet is generating the updated ticket…')).toBeInTheDocument()
    expect(gameCard('Team A', 'Team B')).toHaveAttribute('aria-disabled', 'true')
    rejectRequest(new Error('SportyBet failed'))
    await screen.findByText(/SportyBet failed.*current ticket is unchanged/i)
    expect(screen.getByText('HW7UDH')).toBeInTheDocument()
    expectSelected('Team A', 'Team B', true)
  })

  it('refreshes the original code after rebooking and resets selections', async () => {
    const user = userEvent.setup()
    vi.mocked(api.removeSelectedGames).mockResolvedValue(updated)
    render(<DashboardPage />)
    await loadTicket(user)
    await user.click(gameCard('Team C', 'Team D'))
    await user.click(screen.getAllByRole('button', { name: /Remove Selected \((first|last)\)/ })[0])
    await user.click(screen.getByRole('button', { name: 'Remove Selected' }))
    await screen.findByText('QRZG53')
    vi.mocked(api.fetchBookingByCode).mockResolvedValueOnce(original)
    await user.click(screen.getByRole('button', { name: 'Restore original ticket' }))
    await screen.findByText('Original ticket restored.')
    expect(api.fetchBookingByCode).toHaveBeenLastCalledWith('HW7UDH')
    expect(screen.getByText('HW7UDH')).toBeInTheDocument()
    expectSelected('Team A', 'Team B', false)
  })

  it('shows loading ticket state', async () => {
    vi.mocked(api.fetchBookingByCode).mockImplementation(() => new Promise(() => undefined))
    render(<DashboardPage />)
    fireEvent.change(screen.getByLabelText('SportyBet booking code'), { target: { value: 'HW7UDH' } })
    fireEvent.click(screen.getByRole('button', { name: 'Load Ticket' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Loading ticket…' })).toBeDisabled())
  })

  it('shows history copy confirmation without leaving the history page', async () => {
    const user = userEvent.setup()
    vi.mocked(api.fetchHistory).mockResolvedValue([
      { id: 1, booking_code: 'OLD123', loaded_at: '2026-08-14T19:42:00Z', selection_count: 2, remaining_odds: 1.4 },
      { id: 2, booking_code: 'OTHER9', loaded_at: '2026-08-13T19:42:00Z', selection_count: 1, remaining_odds: 1.2 },
    ])
    render(<DashboardPage />)
    await user.click(screen.getByRole('button', { name: 'Open booking history' }))
    await screen.findByText('OLD123')
    let resolveCopy: (() => void) | undefined
    const writeText = vi.spyOn(navigator.clipboard, 'writeText').mockImplementation(() => new Promise<void>((resolve) => { resolveCopy = resolve }))
    await user.click(screen.getByRole('button', { name: 'Copy OLD123' }))
    expect(writeText).toHaveBeenCalledWith('OLD123')
    expect(screen.queryByText('Copied to clipboard')).not.toBeInTheDocument()
    resolveCopy?.()
    expect(await screen.findByText('Copied to clipboard')).toBeInTheDocument()
    expect(screen.getAllByRole('status').filter((status) => status.textContent === 'Copied to clipboard')).toHaveLength(1)
    expect(screen.getByText('History')).toBeInTheDocument()
  })

  it('shows history copy failure when clipboard rejects', async () => {
    const user = userEvent.setup()
    vi.mocked(api.fetchHistory).mockResolvedValue([{ id: 1, booking_code: 'OLD123', loaded_at: '2026-08-14T19:42:00Z', selection_count: 2, remaining_odds: 1.4 }])
    const writeText = vi.spyOn(navigator.clipboard, 'writeText').mockRejectedValueOnce(new Error('clipboard unavailable'))
    render(<DashboardPage />)
    await user.click(screen.getByRole('button', { name: 'Open booking history' }))
    await user.click(await screen.findByRole('button', { name: 'Copy OLD123' }))
    expect(writeText).toHaveBeenCalledWith('OLD123')
    expect(await screen.findByText('Copy failed')).toBeInTheDocument()
    expect(screen.getByText('History')).toBeInTheDocument()
  })

  it('uses the legacy copy fallback only when it reports success', async () => {
    const user = userEvent.setup()
    vi.mocked(api.fetchHistory).mockResolvedValue([{ id: 1, booking_code: 'OLD123', loaded_at: '2026-08-14T19:42:00Z', selection_count: 2, remaining_odds: 1.4 }])
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: undefined })
    Object.defineProperty(document, 'execCommand', { configurable: true, value: vi.fn().mockReturnValue(true) })
    render(<DashboardPage />)
    await user.click(screen.getByRole('button', { name: 'Open booking history' }))
    await user.click(await screen.findByRole('button', { name: 'Copy OLD123' }))
    expect(document.execCommand).toHaveBeenCalledWith('copy')
    expect(await screen.findByText('Copied to clipboard')).toBeInTheDocument()
  })
})
