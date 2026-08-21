import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it } from 'vitest'
import App from '../App'

describe('Phase 5A workspace navigation', () => {
  afterEach(cleanup)

  it('starts on the dashboard with only workspace cards', () => {
    render(<App />)
    expect(screen.getByRole('button', { name: 'Optimizer' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Forebet Analyzer' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Future Tool/i })).toBeInTheDocument()
    expect(screen.getByLabelText('SportyBet booking code')).not.toBeVisible()
    expect(screen.queryByRole('textbox', { name: /Forebet predictions page URL/i })).not.toBeInTheDocument()
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

  it('opens the functional Forebet analyzer', async () => {
    const user = userEvent.setup()
    render(<App />)
    await user.click(screen.getByRole('button', { name: 'Forebet Analyzer' }))

    expect(screen.getByRole('heading', { name: 'Forebet Analyzer' })).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: /Forebet predictions page URL/i })).toBeVisible()
    expect(screen.getByRole('button', { name: 'Analyze Forebet' })).toBeEnabled()
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
