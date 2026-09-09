import { afterEach, describe, expect, it, vi } from 'vitest'
import { removeSelectedGames } from './api'

describe('removeSelectedGames', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('posts selected event IDs to the existing remove-selected booking endpoint', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      text: async () => JSON.stringify({ booking_code: 'SPLIT2', total_selections: 0, total_odds: null, remaining_odds: 1, selections: [] }),
    })
    vi.stubGlobal('fetch', fetchMock)

    await removeSelectedGames('SPLIT1', ['A'])

    expect(fetchMock).toHaveBeenCalledOnce()
    expect(fetchMock.mock.calls[0][0]).toMatch(/\/bookings\/SPLIT1\/remove-selected$/)
    expect(fetchMock.mock.calls[0][1]).toMatchObject({
      method: 'POST',
      body: JSON.stringify({ event_ids: ['A'] }),
    })
  })
})
