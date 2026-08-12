export interface MatchItem {
  id: string
  home: string
  away: string
  competition: string
  kickoff: string
}

export const initialMatches: MatchItem[] = [
  {
    id: 'm1',
    home: 'Manchester City',
    away: 'Liverpool',
    competition: 'Premier League',
    kickoff: '2026-08-09T19:45:00Z',
  },
  {
    id: 'm2',
    home: 'Real Madrid',
    away: 'Barcelona',
    competition: 'La Liga',
    kickoff: '2026-08-10T18:30:00Z',
  },
  {
    id: 'm3',
    home: 'PSG',
    away: 'Bayern Munich',
    competition: 'Champions League',
    kickoff: '2026-08-11T20:00:00Z',
  },
  {
    id: 'm4',
    home: 'Juventus',
    away: 'AC Milan',
    competition: 'Serie A',
    kickoff: '2026-08-12T17:15:00Z',
  },
  {
    id: 'm5',
    home: 'Chelsea',
    away: 'Arsenal',
    competition: 'FA Cup',
    kickoff: '2026-08-13T16:00:00Z',
  },
]
