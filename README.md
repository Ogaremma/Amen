# Amen SportyBet Booking Optimizer

This repository implements Phase 1 of the Amen Telegram Mini App for SportyBet football bookings.

The current implementation includes:
- A FastAPI backend with SportyBet booking retrieval and rebooking endpoints
- A React + Vite frontend for fetching a booking, displaying football selections ordered by kickoff time, removing selections, and generating a new booking
- Backend-only SportyBet API integration support via server-side environment variables

## Current Architecture

- `backend/app/main.py` - FastAPI application entrypoint
- `backend/app/api/bookings.py` - booking endpoints
- `backend/app/services/sportybet.py` - SportyBet service wrapper and mock fallback
- `backend/app/schemas/booking.py` - Pydantic request/response models
- `frontend/src/App.tsx` - Phase 1 dashboard shell
- `frontend/src/components/DashboardPage.tsx` - booking fetch and rebook UI
- `frontend/src/lib/api.ts` - frontend API client

## Backend API

Available endpoints:
- `GET /api/v1/bookings/{booking_code}` — fetch a SportyBet booking
- `POST /api/v1/bookings/rebook` — remove selections and generate a new booking
- `GET /health` — health check

## Environment Setup

### Backend
Create `.env` from `.env.example` and set:
```env
SPORTYBET_API_KEY=your_sportybet_api_key_here
SPORTYBET_API_BASE_URL=https://api.sportybet.example
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
CORS_ORIGINS=http://localhost:5173
```

### Frontend
Create `frontend/.env` from `frontend/.env.example` and set:
```env
VITE_API_BASE_URL=http://localhost:8000/api/v1
```

## Running Locally

### Backend
```bash
cd backend
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### Frontend
```bash
cd frontend
npm install
npm run dev
```

## Build Verification

- Frontend build passed successfully with `npm run build`
- Backend Python source compiles cleanly with `python -m compileall app`

## Notes

- The backend currently uses mock SportyBet data when `SPORTYBET_API_BASE_URL` or `SPORTYBET_API_KEY` are not configured.
- The app is limited to football booking fetch, selection removal, and rebook generation to satisfy Phase 1 requirements.
