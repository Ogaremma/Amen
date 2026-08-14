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
      id: 'A', event_id: 'A', home: 'Team A', away: 'Team B', competition: 'Premier League', category: 'England',
      kickoff: '2026-08-13T11:30:00Z', kickoff_date: '2026-08-13', kickoff_time: '11:30',
      local_kickoff_date: '2026-08-13', local_kickoff_time: '12:30', market: '1X2', outcome: 'Home', odds: 1.5,
      status: 'Not start', game_status: 'upcoming',
    },
    {
      id: 'B', event_id: 'B', home: 'Team C', away: 'Team D', competition: 'La Liga', category: 'Spain',
      kickoff: '2026-08-13T14:00:00Z', kickoff_date: '2026-08-13', kickoff_time: '14:00',
      local_kickoff_date: '2026-08-13', local_kickoff_time: '15:00', market: 'Totals', outcome: 'Over 2.5', odds: 2.5,
      status: 'Live', game_status: 'live',
    },
    {
      id: 'C', event_id: 'C', home: 'Team E', away: 'Team F', competition: 'Serie A', category: 'Italy',
      kickoff: '2026-08-13T10:00:00Z', kickoff_date: '2026-08-13', kickoff_time: '10:00',
      local_kickoff_date: '2026-08-13', local_kickoff_time: '11:00', market: 'Both Teams To Score', outcome: 'Yes', odds: 1.8,
      status: 'Finished', game_status: 'ended',
    },
  ],
}

const updated: BookingResponse = { ...original, booking_code: 'QRZG53', total_selections: 1, remaining_odds: 1.5, selections: [original.selections[0]] }

async function loadTicket(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText('SportyBet booking code'), 'hw7udh')
  await user.click(screen.getByRole('button', { name: 'Load Ticket' }))
  await screen.findByRole('button', { name: 'Restore original ticket' })
}

describe('DashboardPage Phase 3 ticket flow', () => {
  let clipboardWrite: ReturnType<typeof vi.fn>

  afterEach(cleanup)
  beforeEach(() => {
    vi.resetAllMocks()
    vi.mocked(api.fetchBookingByCode).mockResolvedValue(original)
    clipboardWrite = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
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
  })

  it('opens ended games without reload and preserves selection across both views', async () => {
    const user = userEvent.setup()
    render(<DashboardPage />)
    await loadTicket(user)
    await user.click(screen.getByLabelText('Select Team A vs Team B'))
    await user.click(screen.getByRole('button', { name: 'View 1 ended games' }))
    expect(screen.getByRole('heading', { name: 'Ended Games' })).toBeInTheDocument()
    expect(screen.getByText(/Team E.*Team F/)).toBeInTheDocument()
    expect(screen.queryByText(/Team A.*Team B/)).not.toBeInTheDocument()
    expect(screen.getAllByText('1 selected')).toHaveLength(2)
    await user.click(screen.getByLabelText('Select Team E vs Team F'))
    expect(screen.getAllByText('2 selected')).toHaveLength(2)
    await user.click(screen.getByRole('button', { name: 'Back to ticket details' }))
    expect(screen.getByLabelText('Select Team A vs Team B')).toBeChecked()
    expect(screen.getAllByText('2 selected')).toHaveLength(2)
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
    const removeButtons = screen.getAllByRole('button', { name: /Remove Selected \((top|bottom)\)/ })
    expect(removeButtons.every((button) => button.hasAttribute('disabled'))).toBe(true)
    await user.click(screen.getByLabelText('Select Team A vs Team B'))
    await user.click(screen.getByLabelText('Select Team C vs Team D'))
    expect(screen.getAllByText('2 selected')).toHaveLength(2)
    await user.click(removeButtons[0])
    await user.click(screen.getByRole('button', { name: 'Remove Selected' }))
    await screen.findByText('QRZG53')
    expect(api.removeSelectedGames).toHaveBeenCalledTimes(1)
    expect(api.removeSelectedGames).toHaveBeenCalledWith('HW7UDH', expect.arrayContaining(['A', 'B']))
    expect(screen.getByLabelText('Select Team A vs Team B')).not.toBeChecked()
  })

  it('disables controls while rebooking and keeps current ticket on failure', async () => {
    const user = userEvent.setup()
    let rejectRequest: (error: Error) => void = () => undefined
    vi.mocked(api.removeSelectedGames).mockImplementation(() => new Promise((_, reject) => { rejectRequest = reject }))
    render(<DashboardPage />)
    await loadTicket(user)
    await user.click(screen.getByLabelText('Select Team A vs Team B'))
    await user.click(screen.getAllByRole('button', { name: /Remove Selected \((top|bottom)\)/ })[0])
    await user.click(screen.getByRole('button', { name: 'Remove Selected' }))
    expect(screen.getByText('SportyBet is generating the updated ticket…')).toBeInTheDocument()
    expect(screen.getByLabelText('Select Team A vs Team B')).toBeDisabled()
    rejectRequest(new Error('SportyBet failed'))
    await screen.findByText(/SportyBet failed.*current ticket is unchanged/i)
    expect(screen.getByText('HW7UDH')).toBeInTheDocument()
    expect(screen.getByLabelText('Select Team A vs Team B')).toBeChecked()
  })

  it('refreshes the original code after rebooking and resets selections', async () => {
    const user = userEvent.setup()
    vi.mocked(api.removeSelectedGames).mockResolvedValue(updated)
    render(<DashboardPage />)
    await loadTicket(user)
    await user.click(screen.getByLabelText('Select Team C vs Team D'))
    await user.click(screen.getAllByRole('button', { name: /Remove Selected \((top|bottom)\)/ })[0])
    await user.click(screen.getByRole('button', { name: 'Remove Selected' }))
    await screen.findByText('QRZG53')
    vi.mocked(api.fetchBookingByCode).mockResolvedValueOnce(original)
    await user.click(screen.getByRole('button', { name: 'Restore original ticket' }))
    await screen.findByText('Original ticket restored.')
    expect(api.fetchBookingByCode).toHaveBeenLastCalledWith('HW7UDH')
    expect(screen.getByText('HW7UDH')).toBeInTheDocument()
    expect(screen.getByLabelText('Select Team A vs Team B')).not.toBeChecked()
  })

  it('shows loading ticket state', async () => {
    vi.mocked(api.fetchBookingByCode).mockImplementation(() => new Promise(() => undefined))
    render(<DashboardPage />)
    fireEvent.change(screen.getByLabelText('SportyBet booking code'), { target: { value: 'HW7UDH' } })
    fireEvent.click(screen.getByRole('button', { name: 'Load Ticket' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Loading ticket…' })).toBeDisabled())
  })
})
