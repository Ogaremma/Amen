import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it } from 'vitest'
import App from '../App'

describe('Phase 5A workspace navigation', () => {
  afterEach(cleanup)

  it('starts on the dashboard with only workspace cards', () => {
    render(<App />)
    expect(screen.getByRole('button', { name: 'Optimizer' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '39 Billion Analyzer' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Future Tool/i })).toBeInTheDocument()
    expect(screen.getByLabelText('SportyBet booking code')).not.toBeVisible()
    expect(screen.getByText('No training data has been imported yet.')).not.toBeVisible()
  })

  it('opens Optimizer as a dedicated view and returns to the dashboard', async () => {
    const user = userEvent.setup()
    render(<App />)
    await user.click(screen.getByRole('button', { name: 'Optimizer' }))
    expect(screen.getByLabelText('SportyBet booking code')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Open booking history' })).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Back to Dashboard' }))
    expect(screen.getByLabelText('SportyBet booking code')).not.toBeVisible()
    expect(screen.getByRole('button', { name: 'Optimizer' })).toBeVisible()
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

  it('opens the future tool placeholder as a dedicated view', async () => {
    const user = userEvent.setup()
    render(<App />)
    await user.click(screen.getByRole('button', { name: /Future Tool/i }))
    expect(screen.getByRole('heading', { name: 'Under construction' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Back to Dashboard' })).toBeInTheDocument()
  })
})
