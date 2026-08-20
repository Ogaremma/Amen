import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it } from 'vitest'
import App from '../App'

describe('Phase 5A workspace navigation', () => {
  afterEach(cleanup)

  it('starts in Optimizer and exposes the existing booking workflow', () => {
    render(<App />)
    expect(screen.getByRole('button', { name: 'Optimizer' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByLabelText('SportyBet booking code')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Open booking history' })).toBeInTheDocument()
  })

  it('shows an explicit analyzer empty state without collecting data', async () => {
    const user = userEvent.setup()
    render(<App />)
    await user.click(screen.getByRole('button', { name: '39 Billion Analyzer' }))

    expect(screen.getByRole('heading', { name: '39 Billion Analyzer' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Import Booking Code' })).toBeDisabled()
    expect(screen.getByText('No training data has been imported yet.')).toBeInTheDocument()
    expect(screen.getByText('Empty-state values - no analysis has been performed.')).toBeInTheDocument()
    expect(screen.queryByLabelText('SportyBet booking code')).not.toBeVisible()
  })
})
