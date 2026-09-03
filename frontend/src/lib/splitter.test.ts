import { describe, expect, it } from 'vitest'
import { selectSplitGames } from './splitter'

const games = (n: number, odds = (i: number) => i + 1) => Array.from({ length: n }, (_, i) => ({
  event_id: String(i + 1), kickoff: `2026-01-01T${String(i).padStart(2, '0')}:00:00Z`, odds: odds(i),
})) as any
const ids = (items: any[]) => items.map((g) => g.event_id)

describe('selectSplitGames', () => {
  it('T=26 Top N=4 returns exactly games 1-4', () => {
    expect(ids(selectSplitGames(games(26), 'top', 4))).toEqual(['1', '2', '3', '4'])
  })

  it('T=26 Bottom N=5 returns exactly games 22-26', () => {
    expect(ids(selectSplitGames(games(26), 'bottom', 5))).toEqual(['22', '23', '24', '25', '26'])
  })

  it('T=26 Middle N=3 takes center pair then the lower-odds candidate of 12/15', () => {
    const result = selectSplitGames(games(26, (i) => i === 11 ? 1.1 : i === 14 ? 2.2 : 5), 'middle', 3)
    expect(ids(result)).toEqual(['12', '13', '14'])
  })

  it('even T Middle N=1 compares the two center candidates immediately', () => {
    const result = selectSplitGames(games(6, (i) => i === 2 ? 3 : i === 3 ? 1 : 9), 'middle', 1)
    expect(ids(result)).toEqual(['4'])
  })

  it('odd T Middle N=2 takes center then compares its neighbors', () => {
    const result = selectSplitGames(games(5, (i) => i === 1 ? 4 : i === 3 ? 1 : 9), 'middle', 2)
    expect(ids(result)).toEqual(['3', '4'])
  })

  it('N=T returns every game for Top, Bottom, and Middle', () => {
    for (const mode of ['top', 'bottom', 'middle'] as const) expect(ids(selectSplitGames(games(7), mode, 7))).toEqual(['1', '2', '3', '4', '5', '6', '7'])
  })

  it('middle continues from the remaining side when one boundary is exhausted', () => {
    expect(ids(selectSplitGames(games(5), 'middle', 5))).toEqual(['1', '2', '3', '4', '5'])
  })

  it('ties on the final odds comparison resolve to the earlier left candidate', () => {
    const result = selectSplitGames(games(6, (i) => i === 2 || i === 3 ? 2 : 9), 'middle', 1)
    expect(ids(result)).toEqual(['3'])
  })

  it.each([0, -1, 4])('rejects invalid N=%s with a clear range error', (count) => {
    expect(() => selectSplitGames(games(3), 'top', count)).toThrow('between 1 and 3')
  })
})
