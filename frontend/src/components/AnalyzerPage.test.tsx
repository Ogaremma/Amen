import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AnalyzerPage } from './AnalyzerPage'
import * as api from '../lib/api'

vi.mock('../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../lib/api')>('../lib/api')
  return { ...actual, analyzeForebet: vi.fn() }
})

const match = (result: api.ForebetPredictionResult, id: string): api.ForebetMatch => ({
  match_id: id, home_team: 'Home FC', away_team: 'Away FC', competition: 'Test League', country: 'Testland', competition_code: 'TL', kickoff: '2026-08-21', kickoff_display: '21/08/2026 20:00', match_url: null, predicted_result: result, predicted_score_home: 1, predicted_score_away: 1, probabilities: { home: 30, draw: 45, away: 25 }, average_goals: 2.1, primary_coefficient: null, odds_home: 2, odds_draw: 3, odds_away: 4, narrative: null, source: 'forebet', source_url: null,
})

describe('AnalyzerPage', () => {
  afterEach(() => { cleanup(); vi.clearAllMocks() })
  it('renders Forebet branding and input', () => {
    render(<AnalyzerPage />)
    expect(screen.getByRole('heading', { name: 'Forebet Analyzer' })).toBeInTheDocument()
    expect(screen.queryByText(/39 Billion/i)).not.toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: /Forebet predictions page URL/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Analyze Forebet' })).toBeInTheDocument()
  })

  it('shows loading, success counts, predictions and draw candidates', async () => {
    const user = userEvent.setup()
    let resolve: (value: api.ForebetAnalyzeResponse) => void = () => undefined
    vi.mocked(api.analyzeForebet).mockReturnValueOnce(new Promise((r) => { resolve = r }))
    render(<AnalyzerPage />)
    await user.click(screen.getByRole('button', { name: 'Analyze Forebet' }))
    expect(screen.getByRole('button', { name: /Analyzing/i })).toBeDisabled()
    const home = match('HOME', '1'); const draw = match('DRAW', '2'); draw.home_team = 'Draw Home'
    resolve({ source_url: 'https://www.forebet.com/en/football-predictions', total_matches: 2, draw_count: 1, draw_matches: [draw], matches: [home, draw] })
    await waitFor(() => expect(screen.getByText('Matches analyzed')).toBeInTheDocument())
    expect(screen.getByText('2', { selector: 'p' })).toBeInTheDocument()
    expect(screen.getByText('Forebet Draw Candidates')).toBeInTheDocument()
    expect(screen.getAllByText('Forebet Draw Prediction').length).toBeGreaterThan(0)
    expect(screen.getByText('Home')).toBeInTheDocument()
  })

  it('shows a useful error and allows retry', async () => {
    const user = userEvent.setup()
    vi.mocked(api.analyzeForebet).mockRejectedValueOnce(new Error('Forebet is unavailable')).mockResolvedValueOnce({ source_url: 'x', total_matches: 0, draw_count: 0, draw_matches: [], matches: [] })
    render(<AnalyzerPage />)
    await user.click(screen.getByRole('button', { name: 'Analyze Forebet' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Forebet is unavailable')
    await user.click(screen.getByRole('button', { name: 'Retry' }))
    await waitFor(() => expect(screen.getByText('Matches analyzed')).toBeInTheDocument())
    expect(api.analyzeForebet).toHaveBeenCalledTimes(2)
  })
})
