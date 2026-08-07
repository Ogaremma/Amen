# Amen 🤖✝️

Production-ready, highly scalable AI-powered Telegram Bot architecture built with Python 3.12+, FastAPI, Aiogram 3, PostgreSQL (SQLAlchemy 2.0 Async), Redis, and APScheduler.

## 🏛 Clean Architecture Overview

Amen follows SOLID design principles and Clean Architecture to ensure strict separation of concerns, testability, and high maintainability:

```text
[ Telegram User ] <---> [ Aiogram Handlers & Keyboards ]
                              │
[ Web API Client ] <--> [ FastAPI Routes & Middleware ]
                              │
                        [ Business Services Layer ]
                              │
                        [ Repository Data Access Layer ]
                              │
                ┌─────────────┴─────────────┐
                ▼                           ▼
        [ PostgreSQL DB ]           [ Redis / Storage ]
```

### Module Blueprint

- **`app/bot/`**: Telegram Bot interface (handlers, custom keyboards, state management, middlewares).
- **`app/api/`**: REST API endpoints, Webhook handlers, and FastAPI dependency injectors.
- **`app/core/`**: Central application configurations (`pydantic-settings`), logging, security, and global exception handlers.
- **`app/database/`**: Async SQLAlchemy database engine, session lifecycle, and base declarations.
- **`app/models/`**: SQLAlchemy 2.0 declarative database ORM models.
- **`app/repositories/`**: Generic async CRUD data access layer abstraction (`BaseRepository`).
- **`app/services/`**: Core business domain logic, isolated from transport protocols (HTTP/Telegram).
- **`app/schemas/`**: Pydantic v2 data transfer objects (DTOs) and request/response models.
- **`app/collectors/`**: Data ingestion & external API integration collectors.
- **`app/ml/`**: Machine Learning inference contracts and model wrappers.
- **`app/predictions/`**: Prediction domain logic interface placeholders.
- **`app/scheduler/`**: Async background job scheduler (`APScheduler`).
- **`app/prompts/`**: AI Prompt management & template engine.
- **`app/middleware/`**: Custom HTTP middlewares for FastAPI.
- **`app/utils/`**: Shared helper functions.

---

## 🛠 Tech Stack

- **Language**: Python 3.12+
- **Web Framework**: FastAPI
- **Telegram Bot Framework**: Aiogram 3.x
- **Database**: PostgreSQL 16 + SQLAlchemy 2.0 (AsyncIO) + Alembic
- **Caching & FSM**: Redis 7
- **Scheduler**: APScheduler
- **Validation & Settings**: Pydantic v2 & `pydantic-settings`
- **Containerization**: Docker & Docker Compose
- **Dependency Management**: Poetry / Pyproject.toml

---

## 🚀 Quick Start with Docker

1. **Clone the repository and prepare `.env`**:
   ```bash
   cp .env.example .env
   ```

2. **Configure your `.env`**:
   Insert your Telegram Bot Token from [@BotFather](https://t.me/BotFather):
   ```env
   BOT_TOKEN=your_actual_bot_token_here
   ```

3. **Spin up using Docker Compose**:
   ```bash
   docker-compose up -d --build
   ```

4. **Verify Health**:
   Open http://localhost:8000/health in your browser or run:
   ```bash
   curl http://localhost:8000/health
   ```

---

## 💻 Local Development Setup (Without Docker)

1. **Install Dependencies**:
   ```bash
   poetry install
   ```

2. **Run PostgreSQL & Redis**:
   Ensure PostgreSQL and Redis services are running locally.

3. **Run Database Migrations**:
   ```bash
   poetry run alembic upgrade head
   ```

4. **Start the Application**:
   ```bash
   poetry run python -m app.main
   ```

---

## 🧪 Testing & Code Quality

Run tests and linters:
```bash
poetry run ruff check .
poetry run pytest
```
