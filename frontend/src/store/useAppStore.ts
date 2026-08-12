import { create } from 'zustand'
import { initialMatches, type MatchItem } from '../data/matches'

export type PageKey = 'dashboard' | 'optimize' | 'merge' | 'split' | 'profile'

export interface AppState {
  page: PageKey
  bookingCode: string
  matches: MatchItem[]
  selectedMatchIds: string[]
  setPage: (page: PageKey) => void
  setBookingCode: (code: string) => void
  fetchBooking: () => void
  sortMatchesByTime: () => void
  toggleMatchSelection: (matchId: string) => void
  generateBookingCode: () => void
}

const generateFakeBookingCode = () => {
  const words = ['AME', 'FOOT', 'PLAY', 'BALL', 'ZONE', 'KICK', 'PASS']
  const code = Array.from({ length: 3 }, () => words[Math.floor(Math.random() * words.length)])
  return code.join('-').slice(0, 14).toUpperCase()
}

export const useAppStore = create<AppState>()((set: any) => ({
  page: 'dashboard',
  bookingCode: '',
  matches: initialMatches,
  selectedMatchIds: [],
  setPage: (page: PageKey) => set({ page }),
  setBookingCode: (code: string) => set({ bookingCode: code }),
  fetchBooking: () =>
    set(() => ({
      bookingCode: generateFakeBookingCode(),
    })),
  sortMatchesByTime: () =>
    set((state: AppState) => ({
      matches: [...state.matches].sort(
        (a, b) => new Date(a.kickoff).getTime() - new Date(b.kickoff).getTime(),
      ),
    })),
  toggleMatchSelection: (matchId: string) =>
    set((state: AppState) => ({
      selectedMatchIds: state.selectedMatchIds.includes(matchId)
        ? state.selectedMatchIds.filter((id) => id !== matchId)
        : [...state.selectedMatchIds, matchId],
    })),
  generateBookingCode: () =>
    set(() => ({
      bookingCode: generateFakeBookingCode(),
    })),
}))
